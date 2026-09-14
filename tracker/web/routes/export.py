"""CSV exports of the Ads library and the Landing-pages catalog.

The filters mirror the screens (the Ads page links here with its current
filters), so "Export CSV" downloads what you're looking at. Both are streamed
row by row. Column names are stable English identifiers, whatever the interface
language, so spreadsheets and automations that read them don't break.
"""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from ... import db
from ..deps import _authed
from .ads import library_filters
from .landing import selected_company_ids

router = APIRouter()

AD_COLUMNS = [
    "ad_archive_id", "company", "page_id", "page_name", "status", "duration_days",
    "started", "stopped", "first_seen", "last_seen", "media", "creatives", "platforms",
    "languages", "impression_rank", "versions_group", "title", "body", "cta",
    "destination_url", "landing_page_url", "ad_library_url",
]
LANDING_COLUMNS = [
    "company", "title", "description", "url", "ads", "longest_running_days", "active",
    "first_ad_seen", "last_ad_seen", "title_checked",
]

# A cell starting with one of these runs as a formula in Excel/Sheets. Ad copy is
# written by other people, so neutralise it (OWASP "CSV injection").
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def csv_line(values) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerow([_cell(v) for v in values])
    return buf.getvalue()


def csv_stream(columns: list[str], rows):
    """The CSV as it is written, one row at a time. Starts with a BOM: Excel
    otherwise opens UTF-8 (accents in ad copy) as Latin-1."""
    yield ("\ufeff" + csv_line(columns)).encode("utf-8")
    for row in rows:
        yield csv_line(row.get(col) for col in columns).encode("utf-8")


def _csv_response(name: str, columns: list[str], rows) -> StreamingResponse:
    filename = f"{name}-{date.today().isoformat()}.csv"
    return StreamingResponse(
        csv_stream(columns, rows), media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"',
                 "cache-control": "no-store"},
    )


@router.get("/ads.csv")
def ads_csv(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    filters = library_filters(request)
    if "status" not in request.query_params:
        filters = replace(filters, status="all")
    return _csv_response("ads", AD_COLUMNS, db.iter_ads_for_export(filters))


@router.get("/landing-pages.csv")
def landing_pages_csv(request: Request, sort: str = "ads"):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    rows = db.list_landing_pages_for_export()
    if selected := selected_company_ids(request):
        rows = [r for r in rows if r["company_id"] in selected]
    if sort == "duration":
        rows = sorted(rows, key=lambda r: r["longest_running_days"], reverse=True)
    return _csv_response("landing-pages", LANDING_COLUMNS, rows)
