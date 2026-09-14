"""Landing-pages axis: the catalog and the landing-page detail."""

from __future__ import annotations

import re

from .base import _date_only, _now_utc_label, connect, resolve_media_url

_SCHEME_RE = re.compile(r"^https?://(www\.)?", re.IGNORECASE)


def _display_url(url: str | None) -> str:
    """URL without scheme/www, for showing a page that has no title yet."""
    return _SCHEME_RE.sub("", url or "")


def get_catalog_data() -> dict:
    """Landing-page catalog: activity window + latest snapshot (title) + hero."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT lp.id AS landing_page_id, lp.url_canonical, lp.company_id,
                   lp.resolution_status,
                   c.name AS company_name, c.domain AS company_domain,
                   la.ad_count, la.window_start, la.window_end, la.currently_active,
                   la.max_duration_days, la.snapshot_count,
                   ps.headline, ps.description,
                   ps.captured_at AS snapshot_captured_at,
                   hero.raw_url AS hero_raw_url, hero.media_key AS hero_media_key
            FROM landing_pages lp
            JOIN companies c ON c.id = lp.company_id
            JOIN lp_activity la ON la.landing_page_id = lp.id
            LEFT JOIN LATERAL (
                SELECT headline, description, captured_at FROM page_snapshots s
                WHERE s.landing_page_id = lp.id
                ORDER BY s.captured_at DESC, s.id DESC LIMIT 1
            ) ps ON true
            LEFT JOIN LATERAL (
                SELECT COALESCE(cr.thumbnail_url, cr.image_url) AS raw_url,
                       COALESCE(cr.media_thumb_key, cr.media_image_key) AS media_key
                FROM creatives cr JOIN ads a ON a.id = cr.ad_id
                WHERE cr.landing_page_id = lp.id
                  AND (cr.thumbnail_url IS NOT NULL OR cr.image_url IS NOT NULL)
                ORDER BY a.duration_days DESC NULLS LAST, cr.creative_index ASC
                LIMIT 1
            ) hero ON true
            ORDER BY la.ad_count DESC NULLS LAST, lp.id DESC
            """
        ).fetchall()

    companies: dict[int, str] = {}
    for r in rows:
        r["window_start"] = _date_only(r["window_start"])
        r["window_end"] = _date_only(r["window_end"])
        r["snapshot_captured_at"] = _date_only(r["snapshot_captured_at"])
        r["display_url"] = _display_url(r["url_canonical"])
        r["hero_image_url"] = resolve_media_url(r.pop("hero_raw_url"), r.pop("hero_media_key"))
        companies.setdefault(r["company_id"], r["company_name"])

    return {
        "generated_at": _now_utc_label(),
        "rows": rows,
        "companies": sorted(
            ({"id": i, "name": n} for i, n in companies.items()),
            key=lambda x: x["name"],
        ),
    }


def get_product_detail(lp_id: int) -> dict | None:
    """Landing-page detail: title history (snapshots) + ads grouped by ad (carousel)."""
    with connect() as conn:
        lp = conn.execute(
            """SELECT lp.id, lp.url_canonical, lp.company_id, c.name AS company_name,
                      lp.first_seen, lp.last_seen, lp.resolution_status
               FROM landing_pages lp JOIN companies c ON c.id = lp.company_id
               WHERE lp.id = %s""",
            (lp_id,),
        ).fetchone()
        if not lp:
            return None

        snapshots = conn.execute(
            """SELECT id, content_hash, headline, description,
                      raw->>'final_url' AS final_url, captured_at
               FROM page_snapshots WHERE landing_page_id = %s
               ORDER BY captured_at DESC, id DESC""",
            (lp_id,),
        ).fetchall()

        ads_raw = conn.execute(
            """SELECT a.id AS ad_id, a.ad_archive_id, a.is_active,
                      a.delivery_start_time, a.delivery_stop_time, a.duration_days,
                      a.snapshot_url, a.ad_snapshot_url, a.publisher_platforms,
                      cr.id AS creative_id, cr.creative_index, cr.body, cr.title,
                      cr.cta_text, cr.cta_type,
                      cr.media_image_key, cr.image_url,
                      cr.media_video_key, cr.video_url,
                      cr.media_thumb_key, cr.thumbnail_url,
                      c.name AS company_name
               FROM creatives cr
               JOIN ads a ON a.id = cr.ad_id
               JOIN companies c ON c.id = a.company_id
               WHERE cr.landing_page_id = %s
               ORDER BY a.duration_days DESC NULLS LAST, a.id DESC,
                        cr.creative_index ASC NULLS LAST, cr.id ASC""",
            (lp_id,),
        ).fetchall()

    ads_by_id: dict[int, dict] = {}
    for a in ads_raw:
        ad = ads_by_id.get(a["ad_id"])
        if ad is None:
            ad = {
                "ad_id": a["ad_id"],
                "ad_archive_id": a["ad_archive_id"],
                "is_active": a["is_active"],
                "delivery_start": _date_only(a["delivery_start_time"]),
                "delivery_stop": _date_only(a["delivery_stop_time"]),
                "duration_days": a["duration_days"],
                "snapshot_url": a["snapshot_url"] or a["ad_snapshot_url"],
                "publisher_platforms": a["publisher_platforms"] or [],
                "company_name": a["company_name"],
                "creatives": [],
            }
            ads_by_id[a["ad_id"]] = ad
        ad["creatives"].append({
            "creative_id": a["creative_id"],
            "creative_index": a["creative_index"] if a["creative_index"] is not None else len(ad["creatives"]),
            "body": a["body"], "title": a["title"],
            "cta_text": a["cta_text"], "cta_type": a["cta_type"],
            "image_url": resolve_media_url(a["image_url"], a["media_image_key"]),
            "video_url": resolve_media_url(a["video_url"], a["media_video_key"]),
            "thumbnail_url": resolve_media_url(a["thumbnail_url"], a["media_thumb_key"]),
        })

    for s in snapshots:
        s["captured_at"] = _date_only(s["captured_at"]) or ""

    return {
        "landing_page": {
            "id": lp["id"], "url_canonical": lp["url_canonical"],
            "display_url": _display_url(lp["url_canonical"]),
            "company_id": lp["company_id"], "company_name": lp["company_name"],
            "first_seen": _date_only(lp["first_seen"]), "last_seen": _date_only(lp["last_seen"]),
            "resolution_status": lp["resolution_status"],
        },
        "snapshots": snapshots,
        "ads": list(ads_by_id.values()),
    }
