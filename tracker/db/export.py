"""Spreadsheet export of the landing pages (the ads export streams from `library`).

Rows carry plain values (no media URLs: mirrored media is only served to signed-in
users, and Meta's links expire).
"""

from __future__ import annotations

from .landing import get_catalog_data


def list_landing_pages_for_export() -> list[dict]:
    """The landing-page catalog as flat rows (same order as the catalog)."""
    return [
        {
            "company": r["company_name"],
            "company_id": r["company_id"],
            "title": r["headline"] or "",
            "description": r["description"] or "",
            "url": r["url_canonical"],
            "ads": r["ad_count"] or 0,
            "longest_running_days": r["max_duration_days"] or 0,
            "active": bool(r["currently_active"]),
            "first_ad_seen": r["window_start"] or "",
            "last_ad_seen": r["window_end"] or "",
            "title_checked": r["snapshot_captured_at"] or "",
        }
        for r in get_catalog_data()["rows"]
    ]
