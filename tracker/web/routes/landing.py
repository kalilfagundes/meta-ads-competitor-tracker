"""Landing-pages catalog and the landing-page detail (aggregation axis)."""

from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ... import db
from ...i18n import N_, translate
from ..deps import _authed, request_lang, templates

router = APIRouter()


def selected_company_ids(request: Request) -> set[int]:
    """The companies ticked in the Company filter (?company=1&company=2). Read from
    the query string rather than typed as ints, so that a stray value (company="")
    is ignored instead of answered with a 422; none ticked means every company."""
    return {int(c) for c in request.query_params.getlist("company") if c.strip().isdigit()}


@router.get("/landing-pages", response_class=HTMLResponse)
def landing_pages(request: Request, sort: str = "ads"):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    selected = selected_company_ids(request)
    data = db.get_catalog_data()
    rows = data["rows"]
    if selected:
        rows = [r for r in rows if r["company_id"] in selected]
    if sort == "duration":
        rows = sorted(rows, key=lambda r: (r["max_duration_days"] or 0), reverse=True)
    # (the default already comes ad_count-desc from get_catalog_data)
    return templates.TemplateResponse(request, "landing_pages.html", {
        "rows": rows, "sort": sort,
        "company_choices": [{"value": c["id"], "name": c["name"], "checked": c["id"] in selected}
                            for c in data["companies"]],
    })


@router.get("/landing-pages/{lp_id}", response_class=HTMLResponse)
def landing_page_detail(request: Request, lp_id: int):
    if not _authed(request):
        return RedirectResponse(
            "/login?next=" + quote(f"/landing-pages/{lp_id}", safe=""), status_code=302
        )
    data = db.get_product_detail(lp_id)
    if data is None:
        message = translate(request_lang(request), N_("Landing page not found."))
        return HTMLResponse(f"<p>{html.escape(message)}</p>", status_code=404)
    latest = data["snapshots"][0] if data["snapshots"] else None
    return templates.TemplateResponse(request, "product.html", {
        "lp": data["landing_page"], "latest": latest,
        "snapshots": data["snapshots"], "ads": data["ads"],
    })
