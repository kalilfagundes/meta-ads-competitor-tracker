"""The full-page ad detail (the library's cards live in `library`).

  - media URLs resolve to `/media/<media_key>?fallback=<cdn>` (the web app serves
    them via `tracker.storage`);
  - `ads.publisher_platforms`/`languages` are `TEXT[]` (ready-made lists).
"""

from __future__ import annotations

from .base import _date_only, connect, resolve_media_url
from .landing import _display_url


def get_ad_detail(ad_archive_id: str) -> dict | None:
    """One ad with all its creatives (for the full-page ad detail).

    Media URLs are resolved the same way as the library's cards. Returns None when the
    ad is unknown. `sort_index` is the ad's position in Meta's latest active
    impression-sorted response (0 = top); it is null for inactive ads.

    Two groupings, both by id (never by text):
      - `formats`: the ad's own creatives when they are placement variants (the
        same ad in 9:16 / 1:1 / 1.91:1) — empty for an ad shown by its cards;
      - `versions`: other ads Meta groups with this one (same `collation_id`).

    Whether the creatives are cards or Formats comes from Meta's display format
    (`shows_cards`); an ad whose record had none falls back to comparing the
    creatives' text. Catalog `{{templates}}` and blank text are dropped from
    every text field (`clean_text`). `page` is the Page as Meta shows it.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT cr.id AS creative_id, cr.creative_index,
                   cr.body, cr.title, cr.description, cr.caption,
                   cr.link_url, cr.cta_text, cr.cta_type,
                   cr.image_url, cr.video_url, cr.thumbnail_url,
                   cr.media_image_key, cr.media_video_key, cr.media_thumb_key,
                   cr.landing_page_id, lp.url_canonical AS lp_url,
                   a.ad_archive_id, a.is_active, a.duration_days,
                   a.delivery_start_time, a.delivery_stop_time,
                   a.publisher_platforms, a.languages,
                   a.snapshot_url, a.ad_snapshot_url,
                   a.first_seen_at, a.last_seen_at, a.collation_id, a.collation_count,
                   a.display_format, a.body AS ad_body, a.page_name AS ad_page_name,
                   p.page_name, p.profile_picture_url, p.profile_picture_key,
                   p.like_count, p.categories AS page_categories, p.profile_uri,
                   c.id AS company_id, c.name AS company_name, c.domain AS company_domain,
                   latest_rank.sort_index
            FROM creatives cr
            JOIN ads a ON cr.ad_id = a.id
            JOIN companies c ON a.company_id = c.id
            LEFT JOIN pages p ON p.id = a.page_ref_id
            LEFT JOIN landing_pages lp ON lp.id = cr.landing_page_id
            LEFT JOIN LATERAL (
                SELECT sort_index FROM ad_appearances
                WHERE ad_id = a.id AND is_active = TRUE
                ORDER BY seen_at DESC LIMIT 1
            ) latest_rank ON true
            WHERE a.ad_archive_id = %s
            ORDER BY cr.creative_index ASC NULLS LAST, cr.id ASC
            """,
            (ad_archive_id,),
        ).fetchall()

        versions = []
        collation_id = rows[0]["collation_id"] if rows else None
        if collation_id:
            versions = conn.execute(
                """
                SELECT a.ad_archive_id, a.is_active, a.duration_days,
                       cr.image_url, cr.thumbnail_url, cr.media_image_key, cr.media_thumb_key
                FROM ads a
                LEFT JOIN LATERAL (
                    SELECT image_url, thumbnail_url, media_image_key, media_thumb_key
                    FROM creatives WHERE ad_id = a.id
                    ORDER BY creative_index ASC NULLS LAST, id ASC LIMIT 1
                ) cr ON true
                WHERE a.collation_id = %s AND a.ad_archive_id <> %s
                ORDER BY a.is_active DESC NULLS LAST, a.duration_days DESC NULLS LAST
                """,
                (collation_id, ad_archive_id),
            ).fetchall()

    if not rows:
        return None

    for v in versions:
        thumb = resolve_media_url(v.pop("thumbnail_url"), v.pop("media_thumb_key"))
        image = resolve_media_url(v.pop("image_url"), v.pop("media_image_key"))
        v["thumb_url"] = thumb or image
        v["duration_days"] = v["duration_days"] or 0

    for r in rows:
        r["image_url"] = resolve_media_url(r.pop("image_url"), r.pop("media_image_key"))
        r["video_url"] = resolve_media_url(r.pop("video_url"), r.pop("media_video_key"))
        r["thumbnail_url"] = resolve_media_url(r.pop("thumbnail_url"), r.pop("media_thumb_key"))
        for key in ("title", "body", "description", "cta_text"):
            r[key] = clean_text(r[key])

    first = rows[0]
    has_video = any(r["video_url"] for r in rows)
    is_carousel = shows_cards(first["display_format"], rows)
    media = "carousel" if is_carousel else ("video" if first["video_url"] else "image")
    # Placement variants (same ad in square/story/3:4) collapse to one creative;
    # an ad shown by its cards keeps them all.
    display_creatives = rows if is_carousel else rows[:1]
    return {
        "ad_archive_id": ad_archive_id,
        "company_id": first["company_id"],
        "company_name": first["company_name"],
        "company_domain": first["company_domain"],
        "is_active": first["is_active"],
        "duration_days": first["duration_days"] or 0,
        "sort_index": first["sort_index"],
        "platforms": first["publisher_platforms"] or [],
        "media": media,
        "kind": ad_kind(first["display_format"], media),
        "is_carousel": is_carousel,
        "body": clean_text(first["ad_body"]),
        "page": {
            "name": first["page_name"] or first["ad_page_name"] or "",
            "avatar_url": resolve_media_url(first["profile_picture_url"], first["profile_picture_key"]),
            "like_count": first["like_count"],
            "categories": first["page_categories"] or [],
            "profile_uri": first["profile_uri"],
        },
        "has_video": has_video,
        "delivery_start": _date_only(first["delivery_start_time"]),
        "delivery_stop": _date_only(first["delivery_stop_time"]),
        "first_seen": _date_only(first["first_seen_at"]),
        "last_seen": _date_only(first["last_seen_at"]),
        "snapshot_url": first["snapshot_url"] or first["ad_snapshot_url"],
        "creatives": display_creatives,
        "formats": [] if is_carousel else rows,
        "collation_id": first["collation_id"],
        "collation_count": first["collation_count"],
        "versions": versions,
        "destinations": _destinations(rows),
    }


def _destinations(creatives: list[dict]) -> list[dict]:
    """Where the ad leads, one entry per distinct destination (a carousel's cards may
    point at different pages; an ad's formats usually share one). `landing_page_id`
    links to the Landing pages view; it is None until the page has been recorded."""
    seen, out = set(), []
    for c in creatives:
        url = c["lp_url"] or c["link_url"]
        if not url:
            continue
        key = c["landing_page_id"] or _link_key(url)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "landing_page_id": c["landing_page_id"],
            "url": url,
            "display_url": _display_url(url),
        })
    return out


def _link_key(url: str | None) -> str:
    """A destination without its query string/fragment, so per-placement UTM
    tags don't make the formats of one ad look like distinct cards."""
    return (url or "").split("#", 1)[0].split("?", 1)[0]


# Display formats Meta shows as a set of cards (carousel, dynamic creative,
# catalog). Any other format with several creatives is showing Formats.
CARD_FORMATS = ("CAROUSEL", "DCO", "DPA")


def shows_cards(display_format: str | None, creatives: list[dict]) -> bool:
    """Show the ad as its cards? From Meta's display format when the record has
    one; otherwise `_is_real_carousel` decides from the creatives' text."""
    if display_format:
        return display_format in CARD_FORMATS and len(creatives) > 1
    return _is_real_carousel(creatives)


def ad_kind(display_format: str | None, media: str) -> str:
    """What the UI calls the ad: catalog / dynamic for those display formats,
    else its media (image / video / carousel)."""
    return {"DPA": "catalog", "DCO": "dynamic"}.get(display_format or "", media)


def clean_text(text: str | None) -> str:
    """Ad text worth showing: '' for blank text (carousel cards carry a single
    space) and for catalog templates Meta fills per product ('{{product.name}}')."""
    text = (text or "").strip()
    return "" if "{{" in text and "}}" in text else text


def _is_real_carousel(creatives: list[dict]) -> bool:
    """Tell a true carousel from the formats of one ad.

    The creatives are always one ad (same ad_archive_id); this only decides how to
    show them. Meta returns the same ad in several formats (square / story / 1.91:1)
    as creatives that share text and destination and differ only in the image. A
    real carousel has distinct cards (different link, title or body). So: >1
    creative AND >1 distinct (title, body, destination, cta).
    """
    if len(creatives) <= 1:
        return False
    keys = {
        (c.get("title") or "", c.get("body") or "", _link_key(c.get("link_url")), c.get("cta_text") or "")
        for c in creatives
    }
    return len(keys) > 1
