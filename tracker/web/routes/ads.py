"""The Ads screen (the library, one page at a time) and the full-page ad detail."""

from __future__ import annotations

import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ... import db
from ...i18n import N_, translate
from ..deps import _authed, _needs_setup, _setup_pending, _tier, request_lang, templates

router = APIRouter()


def library_filters(request: Request) -> db.LibraryFilters:
    """The library's filters from the query string (the Ads screen and its CSV export)."""
    q = request.query_params
    days = q.get("min_days", "").strip()
    return db.LibraryFilters(
        company=q.get("company", ""), media=q.get("media", ""),
        status=q.get("status", "active"), min_days=int(days) if days.isdigit() else 0,
        q=q.get("q", ""),
    )


def _library_payload(request: Request) -> dict:
    filters = library_filters(request)
    page = request.query_params.get("page", "0")
    return {
        **db.get_library_page(filters, sort=request.query_params.get("sort", "dur"),
                              page=int(page) if page.isdigit() else 0),
        "champions": db.get_champions(filters.company),
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _authed(request):
        if _needs_setup():
            return RedirectResponse("/setup", status_code=302)
        if request.url.query:
            return RedirectResponse(
                "/login?next=" + quote("/?" + request.url.query, safe=""), status_code=302
            )
        return RedirectResponse("/login", status_code=302)
    if _setup_pending():
        return RedirectResponse("/setup/competitor", status_code=302)
    companies = db.get_library_companies()
    initial = {
        **_library_payload(request),
        "companies": companies,
        "company": request.query_params.get("company", ""),
        "has_ads": bool(companies),
    }
    data_json = json.dumps(initial, default=str).replace("</", "<\\/")
    return templates.TemplateResponse(
        request, "ads.html", {"data_json": data_json},
        headers={"cache-control": "no-store"},
    )


@router.get("/api/ads")
def library_api(request: Request):
    """One page of the library for the Ads screen's filters, sort and page."""
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(jsonable_encoder(_library_payload(request)),
                        headers={"cache-control": "no-store"})


@router.get("/ads/{ad_archive_id}", response_class=HTMLResponse)
def ad_detail(request: Request, ad_archive_id: str):
    if not _authed(request):
        return RedirectResponse(
            "/login?next=" + quote("/ads/" + ad_archive_id, safe=""), status_code=302
        )
    ad = db.get_ad_detail(ad_archive_id)
    if ad is None:
        message = translate(request_lang(request), N_("Ad not found."))
        return HTMLResponse(f"<p>{html.escape(message)}</p>", status_code=404)

    creatives = ad["creatives"]
    title = next((c["title"] for c in creatives if c["title"]), "")
    # the ad's own primary text; a card's text only when the ad has none
    body = ad["body"] or next((c["body"] for c in creatives if c["body"]), "")
    cta = next((c["cta_text"] for c in creatives if c["cta_text"]), "")
    tier_class, tier_label = _tier(ad["duration_days"])
    # Platform names are Meta's product names (Facebook, Instagram…): not translated.
    platforms_label = " · ".join(
        p.replace("_", " ").title() for p in ad["platforms"]
    )
    # The template shows sort_index, the ad's rank in Meta's impression-sorted
    # response for its own page: per advertiser, never a global position.
    return templates.TemplateResponse(request, "detail.html", {
        "ad": ad, "title": title, "body": body, "cta": cta,
        "tier_class": tier_class, "tier_label": tier_label,
        "platforms_label": platforms_label,
        "likes_label": compact_number(ad["page"]["like_count"], request_lang(request)),
    })


def compact_number(n: int | None, lang: str) -> str:
    """39538110 -> '39.5M' (',' as the decimal mark outside English); '' if None."""
    if n is None:
        return ""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= size:
            value = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return (value if lang == "en" else value.replace(".", ",")) + suffix
    return str(n)
