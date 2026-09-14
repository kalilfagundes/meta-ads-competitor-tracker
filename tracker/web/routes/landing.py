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


@router.get("/landing-pages", response_class=HTMLResponse)
def landing_pages(request: Request, company: str = "", sort: str = "ads"):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    # The filter form submits company="" for "All", so it can't be typed as int
    # (FastAPI would answer 422); anything that isn't a number means "All".
    company = int(company) if company.strip().isdigit() else None
    data = db.get_catalog_data()
    rows = data["rows"]
    if company:
        rows = [r for r in rows if r["company_id"] == company]
    if sort == "duration":
        rows = sorted(rows, key=lambda r: (r["max_duration_days"] or 0), reverse=True)
    # (the default already comes ad_count-desc from get_catalog_data)
    return templates.TemplateResponse(request, "landing_pages.html", {
        "rows": rows, "companies": data["companies"],
        "sel_company": company, "sort": sort,
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
