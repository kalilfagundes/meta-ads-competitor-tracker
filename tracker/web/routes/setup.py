"""First-run setup wizard: language -> administrator -> first competitor -> collection.

The first two steps are open while the instance has no user. The last two need
the session the admin got in step 2, and stay reachable until the wizard is
finished (`app_settings.setup_completed`); until then the Ads screen sends the
admin back here. Everything the wizard sets can be changed later in Settings.
Infrastructure (database, media storage, passwords) is not asked here: it comes
from `.env`, written by the setup script.

Creating the admin needs the instance's setup code, so whoever reaches a fresh
instance first can't claim it: the setup script prints it (`SETUP_TOKEN` in `.env`);
without one, the web app generates a code and writes it to its log.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ... import apify, db, i18n
from ...auth import hash_password
from ...config import get_settings
from ...i18n import N_
from ..deps import (
    _authed, _needs_setup, _set_session_cookie, _setup_pending, request_lang, templates,
)
from .help import source_pricing

router = APIRouter(prefix="/setup")

MIN_PASSWORD_LENGTH = 8
INTERVAL_CHOICES = (1, 3, 6, 12, 24)

# uvicorn's logger: shows up in `docker compose logs web` without extra config.
logger = logging.getLogger("uvicorn.error")
_generated_token: str | None = None
_token_lock = threading.Lock()


def setup_token() -> str:
    """SETUP_TOKEN, or a code generated once per process and logged for the operator."""
    global _generated_token
    if configured := get_settings().setup_token:
        return configured
    with _token_lock:
        if _generated_token is None:
            _generated_token = secrets.token_urlsafe(18)
            logger.warning("No SETUP_TOKEN set. Setup code for creating the administrator: %s",
                           _generated_token)
        return _generated_token


def _setup_token_matches(given: str) -> bool:
    return hmac.compare_digest(given.strip().encode(), setup_token().encode())


def _before_admin() -> RedirectResponse | None:
    """Steps 1-2 only exist until the admin does."""
    return None if _needs_setup() else RedirectResponse("/setup/competitor", status_code=302)


def _after_admin(request: Request) -> RedirectResponse | None:
    """Steps 3-4: signed in and not finished yet, or go where you belong."""
    if _needs_setup():
        return RedirectResponse("/setup", status_code=302)
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if not _setup_pending():
        return RedirectResponse("/", status_code=302)
    return None


# ── Step 1: language ────────────────────────────────────────────────────────

@router.get("")
def setup_language(request: Request, lang: str = ""):
    if not _needs_setup():
        if _authed(request) and _setup_pending():
            return RedirectResponse("/setup/competitor", status_code=302)
        return RedirectResponse("/login", status_code=302)
    if lang in i18n.LANGUAGES:
        request.state.lang = lang   # preview the wizard in the language being picked
    return templates.TemplateResponse(request, "setup_language.html", {})


@router.post("/language")
def setup_language_submit(request: Request, language: str = Form("")):
    if redirect := _before_admin():
        return redirect
    if language in i18n.LANGUAGES:
        db.set_interface_language(language)
    return RedirectResponse("/setup/admin", status_code=302)


# ── Step 2: administrator ───────────────────────────────────────────────────

@router.get("/admin")
def setup_admin(request: Request):
    if redirect := _before_admin():
        return redirect
    setup_token()  # make sure a generated code is in the log before it's asked for
    return templates.TemplateResponse(request, "setup_admin.html", {"error": "", "username": ""})


@router.post("/admin")
def setup_admin_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
    setup_code: str = Form(""),
):
    # Race: if someone already created the admin, send them on.
    if redirect := _before_admin():
        return redirect

    username = username.strip()
    error = ""
    if not _setup_token_matches(setup_code):
        error = N_("That setup code isn't right.")
    elif not username:
        error = N_("Enter a username.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        error = N_("Password must be at least 8 characters.")
    elif password != confirm:
        error = N_("Passwords don't match.")
    if error:
        return templates.TemplateResponse(
            request, "setup_admin.html", {"error": error, "username": username}, status_code=400,
        )

    if db.create_first_user(username, hash_password(password)) is None:
        return RedirectResponse("/login", status_code=302)  # lost the race: someone else is the admin
    resp = RedirectResponse("/setup/competitor", status_code=302)  # log the created admin straight in
    _set_session_cookie(resp, request)
    return resp


# ── Step 3: first competitor ────────────────────────────────────────────────

def _competitor_page(request: Request, *, company: str = "", page_id: str = "",
                     error: str = "", status_code: int = 200):
    return templates.TemplateResponse(request, "setup_competitor.html", {
        "company": company, "page_id": page_id, "error": error,
        "pages": db.list_pages(),
    }, status_code=status_code)


@router.get("/competitor")
def setup_competitor(request: Request):
    if redirect := _after_admin(request):
        return redirect
    return _competitor_page(request)


@router.post("/competitor")
def setup_competitor_submit(request: Request, company: str = Form(""), page_id: str = Form("")):
    if redirect := _after_admin(request):
        return redirect
    company, page_id = company.strip(), page_id.strip()
    error = ""
    if not page_id:
        error = N_("Add the company's Facebook page ID, or skip this step.")
    elif not page_id.isdigit():
        error = N_("A page ID is only digits. Check “How do I find a page ID?” below.")
    elif not company:
        error = N_("Give the company a name.")
    if error:
        return _competitor_page(request, company=company, page_id=page_id,
                                error=error, status_code=400)
    co = db.create_company(company)
    db.create_page(co["id"], page_id)
    return RedirectResponse("/setup/collection", status_code=302)


# ── Step 4: collection ──────────────────────────────────────────────────────

APIFY_ERRORS = {
    "missing": N_("Add your Apify API token, or collect self-hosted."),
    "unauthorized": N_("Apify didn't accept this token."),
    "unreachable": N_("Couldn't reach Apify to check the token. Try again in a moment."),
}


def _collection_page(request: Request, *, country: str | None = None, interval: int = 6,
                     source: str = "apify", error: str = "", status_code: int = 200):
    lang = request_lang(request)
    token = (db.get_app_settings() or {}).get("apify_token")
    return templates.TemplateResponse(request, "setup_collection.html", {
        "countries": i18n.countries(lang),
        "country": country or i18n.guess_country(request.headers.get("accept-language"), lang),
        "interval_choices": INTERVAL_CHOICES,
        "interval": interval,
        "has_pages": bool(db.list_tracked_pages()),
        "source": source,
        "token_mask": apify.mask_token(token),
        "error": error,
        **source_pricing(lang),
    }, status_code=status_code)


@router.get("/collection")
def setup_collection(request: Request):
    if redirect := _after_admin(request):
        return redirect
    return _collection_page(request)


@router.post("/collection")
def setup_collection_submit(request: Request, country: str = Form("US"), interval: str = Form("6"),
                            collect_source: str = Form("self_hosted"), apify_token: str = Form("")):
    if redirect := _after_admin(request):
        return redirect
    country = country.strip().upper()
    if country not in dict(i18n.countries(i18n.DEFAULT_LANGUAGE)):
        country = "US"
    hours = int(interval) if interval.isdigit() and int(interval) in INTERVAL_CHOICES else 6
    source = collect_source if collect_source in db.COLLECT_SOURCES else "self_hosted"

    token = apify_token.strip() or None
    if source == "apify":
        # Collection must work when the wizard ends: check the token now (free).
        check = token or (db.get_app_settings() or {}).get("apify_token")
        reason = "" if check else "missing"
        if check:
            try:
                apify.account_username(check)
            except apify.ApifyError as exc:
                reason = exc.reason if exc.reason in APIFY_ERRORS else "unreachable"
        if reason:
            return _collection_page(request, country=country, interval=hours, source=source,
                                    error=APIFY_ERRORS[reason], status_code=400)

    db.update_app_settings(collect_interval_hours=hours, collect_country=country)
    db.update_collection_source(source, apify_max_parallel_runs=apify.DEFAULT_PARALLEL_RUNS,
                                apify_token=token)
    db.complete_setup()
    if db.list_tracked_pages():
        db.request_collect_now()
        return RedirectResponse("/admin", status_code=302)   # Settings shows the first run live
    return RedirectResponse("/", status_code=302)
