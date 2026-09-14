"""Settings/admin: companies -> pages, proxies, config, and the live run status."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ... import apify, db, i18n
from ...i18n import N_
from ..deps import _authed, request_lang, templates
from .help import source_pricing

router = APIRouter(prefix="/admin")

# ?err=<code> -> the message shown on Settings (translated in the template). Codes,
# not text, travel in the URL so nobody can put arbitrary words on the page.
ERRORS = {
    "company_name_empty": N_("Company name can't be empty."),
    "company_has_ads": N_("Can't delete: this company still has ads. Pause its pages instead of deleting."),
    "page_id_empty": N_("Page ID can't be empty."),
    "page_id_digits": N_("A page ID is only digits. Check “How do I find a page ID?” below."),
    "page_has_ads": N_("Can't delete this page: it still has ads. Pause it instead of deleting."),
    db.PageEditError.NOT_FOUND.value: N_("Page not found."),
    db.PageEditError.ID_LOCKED.value: N_("Can't change the page ID: this page already has ads. "
                                         "Add the new ID as a separate page instead."),
    db.PageEditError.ID_TAKEN.value: N_("That page ID is already registered."),
    "apify_token_missing": N_("Add your Apify API token to collect through Apify."),
    "apify_parallel_invalid": N_("Simultaneous Actor runs must be a number from 1 to 5."),
    "collect_busy": N_("A collection is already running or about to start: wait for it to finish."),
}


def _admin_back(err: str = "", detail: str = "") -> RedirectResponse:
    url = "/admin"
    if err:
        url += f"?err={quote(err)}" + (f"&detail={quote(detail[:300])}" if detail else "")
    return RedirectResponse(url, status_code=302)


def _country(code: str) -> str | None:
    """A valid ISO-2 country code, or None (a Page then uses the default country)."""
    code = code.strip().upper()
    return code if code in dict(i18n.countries(i18n.DEFAULT_LANGUAGE)) else None


def _page_id_error(page_id: str) -> str:
    """Why a typed Facebook page ID can't be saved: an ERRORS code, '' if it can."""
    if not page_id:
        return "page_id_empty"
    return "" if page_id.isdigit() else "page_id_digits"


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _unauthorized_json() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def parse_parallel_runs(value: str) -> int | None:
    """Simultaneous Apify Actor runs from a form: 1..MAX_PARALLEL_RUNS, else None."""
    value = value.strip()
    if not value.isdigit() or not 1 <= int(value) <= apify.MAX_PARALLEL_RUNS:
        return None
    return int(value)


def public_settings(settings: dict | None) -> dict:
    """The settings row as templates may see it: the Apify token only masked."""
    settings = dict(settings or {})
    settings["apify_token_mask"] = apify.mask_token(settings.pop("apify_token", None))
    return settings


def _run_status_json(**extra) -> JSONResponse:
    payload = {**jsonable_encoder(db.get_run_status()), **extra}
    return JSONResponse(payload, headers={"cache-control": "no-store"})


@router.get("", response_class=HTMLResponse)
def admin_home(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    run_status = jsonable_encoder(db.get_run_status())
    settings = public_settings(db.get_app_settings())
    err = ERRORS.get(request.query_params.get("err", ""), "")
    return templates.TemplateResponse(request, "admin.html", {
        "companies": db.list_companies_with_pages(),
        "proxies": db.list_proxies(),
        "settings": settings,
        "max_parallel_runs": apify.MAX_PARALLEL_RUNS,
        **source_pricing(request_lang(request)),
        "countries": i18n.countries(request_lang(request)),
        "country_names": dict(i18n.countries(request_lang(request))),
        "run_status_json": json.dumps(run_status).replace("</", "<\\/"),
        "err": err,
        "err_detail": request.query_params.get("detail", "") if err else "",
    })


@router.get("/run-status")
def admin_run_status(request: Request):
    """Live pipeline state, polled by the Settings page."""
    if not _authed(request):
        return _unauthorized_json()
    return _run_status_json()


@router.post("/collect-now")
def admin_collect_now(request: Request):
    if not _authed(request):
        if _wants_json(request):
            return _unauthorized_json()
        return RedirectResponse("/login", status_code=302)
    # Single-flight: False while a run is queued or running. The status sent back
    # says which, and the page says so (static/js/admin.js).
    accepted = db.request_collect_now()
    if _wants_json(request):
        return _run_status_json(accepted=accepted)
    return _admin_back("" if accepted else "collect_busy")


@router.post("/companies")
def admin_company_create(request: Request, name: str = Form(""), domain: str = Form(""),
                         page_id: str = Form(""), collect_country: str = Form("")):
    """A company, and its first Page when a page ID is given."""
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    name, page_id = name.strip(), page_id.strip()
    if not name:
        return _admin_back("company_name_empty")
    if page_id and (err := _page_id_error(page_id)):
        return _admin_back(err)
    company = db.create_company(name, domain.strip() or None)
    if page_id:
        db.create_page(company["id"], page_id, None, _country(collect_country))
    return _admin_back()


@router.post("/companies/{company_id}/edit")
def admin_company_edit(request: Request, company_id: int,
                       name: str = Form(""), domain: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if not name.strip():
        return _admin_back("company_name_empty")
    db.update_company(company_id, name.strip(), domain.strip() or None)
    return _admin_back()


@router.post("/companies/{company_id}/delete")
def admin_company_delete(request: Request, company_id: int):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    ok = db.delete_company(company_id)
    return _admin_back("" if ok else "company_has_ads")


@router.post("/companies/{company_id}/pages")
def admin_page_create(request: Request, company_id: int, page_id: str = Form(""),
                      page_name: str = Form(""), collect_country: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if err := _page_id_error(page_id.strip()):
        return _admin_back(err)
    db.create_page(company_id, page_id.strip(), page_name.strip() or None, _country(collect_country))
    return _admin_back()


@router.post("/pages/{page_pk}/edit")
def admin_page_edit(request: Request, page_pk: int, page_id: str = Form(""),
                    page_name: str = Form(""), collect_country: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if code := _page_id_error(page_id.strip()):
        return _admin_back(code)
    err = db.update_page(page_pk, page_id.strip(), page_name.strip() or None, _country(collect_country))
    return _admin_back(err.value if err else "")


@router.post("/pages/{page_pk}/toggle")
def admin_page_toggle(request: Request, page_pk: int):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    db.toggle_page_tracked(page_pk)
    return _admin_back()


@router.post("/pages/{page_pk}/delete")
def admin_page_delete(request: Request, page_pk: int):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    ok = db.delete_page(page_pk)
    return _admin_back("" if ok else "page_has_ads")


@router.post("/proxies")
def admin_proxy_create(request: Request, url: str = Form("")):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    if url.strip():
        db.create_proxy(url.strip())
    return _admin_back()


@router.post("/proxies/{proxy_id}/toggle")
def admin_proxy_toggle(request: Request, proxy_id: int):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    db.toggle_proxy_enabled(proxy_id)
    return _admin_back()


@router.post("/proxies/{proxy_id}/delete")
def admin_proxy_delete(request: Request, proxy_id: int):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    db.delete_proxy(proxy_id)
    return _admin_back()


@router.post("/settings")
def admin_settings_save(
    request: Request,
    collect_interval_hours: str = Form("6"),
    collect_country: str = Form("US"),
    language: str = Form(""),
    video_quality: str = Form(""),
    image_quality: str = Form(""),
):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    try:
        interval = max(1, int(collect_interval_hours))
    except ValueError:
        interval = 6
    country = _country(collect_country) or (db.get_app_settings() or {}).get("collect_country") or "US"
    db.update_app_settings(collect_interval_hours=interval, collect_country=country)
    if video_quality in db.MEDIA_QUALITIES and image_quality in db.MEDIA_QUALITIES:
        db.update_media_quality(video_quality=video_quality, image_quality=image_quality)
    if language in i18n.LANGUAGES:
        db.set_interface_language(language)
    return _admin_back()


@router.post("/source")
def admin_source_save(
    request: Request,
    collect_source: str = Form("self_hosted"),
    apify_token: str = Form(""),
    apify_max_parallel_runs: str = Form(str(apify.DEFAULT_PARALLEL_RUNS)),
):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    source = collect_source if collect_source in db.COLLECT_SOURCES else "self_hosted"
    parallel = parse_parallel_runs(apify_max_parallel_runs)
    if parallel is None:
        return _admin_back("apify_parallel_invalid")
    token = apify_token.strip()
    if source == "apify" and not token and not (db.get_app_settings() or {}).get("apify_token"):
        return _admin_back("apify_token_missing")
    db.update_collection_source(source, apify_max_parallel_runs=parallel, apify_token=token or None)
    return _admin_back()


@router.post("/apify/test")
def admin_apify_test(request: Request, apify_token: str = Form("")):
    """Check an Apify token (the one typed, else the stored one) without starting
    a run. Answers {ok, username} or {ok: false, error: <reason code>}; the page
    turns the code into a translated message."""
    if not _authed(request):
        return _unauthorized_json()
    token = apify_token.strip() or (db.get_app_settings() or {}).get("apify_token")
    if not token:
        return JSONResponse({"ok": False, "error": "missing"})
    try:
        username = apify.account_username(token)
    except apify.ApifyError as exc:
        return JSONResponse({"ok": False, "error": exc.reason})
    return JSONResponse({"ok": True, "username": username})
