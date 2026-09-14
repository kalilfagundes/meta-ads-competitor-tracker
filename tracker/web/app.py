"""Web app (FastAPI). Assembles the route modules under tracker/web/routes/.

Endpoints are SYNCHRONOUS on purpose: psycopg and boto3 are blocking; FastAPI runs
`def` functions in a threadpool, so they don't block the event loop.
"""

from __future__ import annotations

import mimetypes

# Serve self-hosted fonts with the right MIME type (Python's default table doesn't
# include woff/woff2, and StaticFiles derives Content-Type from it). Must run before
# StaticFiles is mounted below.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..auth import MIN_SECRET_LENGTH, secret_is_usable
from ..config import get_settings
from .deps import _STATIC_DIR
from .routes import admin, ads, auth, collections, export, help, i18n, landing, media, setup

# An internal tool: keep search engines and AI crawlers out. The header covers every
# response (pages, media, JSON, CSV exports); robots.txt stops compliant bots from
# fetching anything at all; base.html repeats it as a <meta> tag.
ROBOTS_TAG = "noindex, nofollow, noarchive, nosnippet, noimageindex, noai, noimageai"
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def create_app() -> FastAPI:
    # Refuse to start rather than accept logins nobody can trust (see tracker.auth).
    if not secret_is_usable(get_settings().session_secret):
        raise RuntimeError(
            f"SESSION_SECRET is missing or shorter than {MIN_SECRET_LENGTH} characters. "
            "Run ./setup.sh (or .\\setup.ps1), or set it to e.g. `openssl rand -hex 32`."
        )
    app = FastAPI(title="Meta Ads Competitor Tracker", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def _no_robots(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = ROBOTS_TAG
        return response

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt():
        return PlainTextResponse(ROBOTS_TXT)

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(auth.router)
    app.include_router(setup.router)
    app.include_router(help.router)
    app.include_router(i18n.router)
    app.include_router(export.router)
    app.include_router(ads.router)
    app.include_router(landing.router)
    app.include_router(media.router)
    app.include_router(collections.router)
    app.include_router(admin.router)
    return app


app = create_app()
