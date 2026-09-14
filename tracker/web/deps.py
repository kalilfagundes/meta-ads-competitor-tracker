"""Shared web helpers used by the route modules: templates (with translations),
session/auth, the duration tier, and the media-fallback allowlist. Kept separate
from `app` so the routers can import them without a cycle.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import jinja2
from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from .. import db, i18n
from ..auth import (
    SESSION_COOKIE_NAME,
    SESSION_DURATION_SECONDS,
    create_session_value,
    verify_session_value,
)
from ..config import get_settings
from ..media_urls import is_meta_media_url

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def request_lang(request: Request) -> str:
    """The interface language: a preview a route set on `request.state` (the setup
    wizard's language step), else the instance setting, else the browser's."""
    chosen = getattr(request.state, "lang", None) or (db.get_app_settings() or {}).get("language")
    return i18n.negotiate(chosen, request.headers.get("accept-language"))


def _i18n_context(request: Request) -> dict:
    lang = request_lang(request)
    return {
        "lang": lang,
        "languages": i18n.LANGUAGES,
        "catalog_version": i18n.catalog_version(lang),
    }


@jinja2.pass_context
def _gettext(ctx, message: str) -> str:
    return i18n.gettext(ctx.get("lang", i18n.DEFAULT_LANGUAGE), message)


@jinja2.pass_context
def _ngettext(ctx, singular: str, plural: str, n: int) -> str:
    return i18n.ngettext(ctx.get("lang", i18n.DEFAULT_LANGUAGE), singular, plural, n)


_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,
    extensions=["jinja2.ext.i18n"],
)
# newstyle: `{{ _("Page %(n)s", n=2) }}` formats and autoescapes the parameters.
_env.install_gettext_callables(_gettext, _ngettext, newstyle=True)
templates = Jinja2Templates(env=_env, context_processors=[_i18n_context])


# The media proxy may fetch the original CDN URL when a mirrored object is
# missing. That URL comes from stored creative data, but the route accepts it as a
# query param, so restrict it to Meta CDN hosts over https — otherwise an
# authenticated user could turn the proxy into an SSRF against internal hosts.
_allowed_media_fallback = is_meta_media_url


def safe_href(url: str | None) -> str:
    """A link target taken from collected ad data, only if it is http(s): a
    `javascript:` or `data:` URL from the data must never become a clickable link.
    Templates render the link only when this is non-empty."""
    try:
        scheme = urlparse((url or "").strip()).scheme.lower()
    except ValueError:
        return ""
    return url.strip() if scheme in ("http", "https") else ""


_env.filters["safe_href"] = safe_href


@lru_cache(maxsize=None)
def static_url(path: str) -> str:
    """/static/<path> with a version taken from the file's content. /static has no
    Cache-Control, so a browser may keep reusing an old stylesheet or script after
    an upgrade; a new version is a new URL. Computed once per process (the files
    only change with a new image)."""
    try:
        digest = hashlib.sha256((_STATIC_DIR / path).read_bytes()).hexdigest()[:10]
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={digest}"


_env.globals["static_url"] = static_url


def _tier(days: int) -> tuple[str, str]:
    """Duration tier (class, English label) on the sequential ramp used across the
    UI. Templates translate the label."""
    d = days or 0
    if d >= 90:
        return "ever", i18n.N_("Evergreen")
    if d >= 60:
        return "gold", i18n.N_("Gold")
    if d >= 30:
        return "win", i18n.N_("Winner")
    if d >= 8:
        return "test", i18n.N_("Testing")
    return "new", i18n.N_("New")


def _authed(request: Request) -> bool:
    return verify_session_value(
        request.cookies.get(SESSION_COOKIE_NAME), get_settings().session_secret
    )


def _needs_setup() -> bool:
    """Instance with no users yet → first run (wizard)."""
    return db.count_users() == 0


def _setup_pending() -> bool:
    """The admin exists but hasn't finished the wizard (first competitor, collection)."""
    return not (db.get_app_settings() or {}).get("setup_completed", True)


def _sanitize_next(nxt: str | None) -> str:
    """Same-origin relative paths only — never becomes an open redirect."""
    if not nxt or not nxt.startswith("/") or nxt.startswith("//"):
        return ""
    return nxt


def _set_session_cookie(resp: Response, request: Request) -> None:
    value = create_session_value(get_settings().session_secret)
    resp.set_cookie(
        SESSION_COOKIE_NAME, value,
        max_age=SESSION_DURATION_SECONDS, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), path="/",
    )
