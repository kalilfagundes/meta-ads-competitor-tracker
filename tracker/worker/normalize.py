"""One shape for a collected Ad, whatever the Collection source.

Both sources hand us the same thing underneath: Meta's Ad Library record for one
ad (a `collated_results[]` node, with the creative under `snapshot`).
  - Self-hosted: `meta_ads_collector`'s `Ad` keeps that record in `ad.raw_data`
    (the snapshot keys are also copied to the top level).
  - Apify: each dataset item *is* that record, with `snapshot` nested.

So both adapters go through one parser of the record, `from_meta_record`, and
`persist.upsert_ad` only ever sees a `NormalizedAd`. The library's own model is a
fallback for an `Ad` without `raw_data`; it drops fields we need
(display_format, caption, the Page's picture/likes/categories).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class NormalizedCreative:
    """One card of an Ad (or its single creative). `image_url` is the full-size
    upload (Meta's 600px copy only when there is none; for a carousel it can even be
    a different crop). `card_key` identifies the card across reads (see `card_key`)."""
    body: str | None = None
    title: str | None = None
    description: str | None = None
    caption: str | None = None
    link_url: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    video_hd_url: str | None = None
    video_sd_url: str | None = None
    thumbnail_url: str | None = None
    cta_text: str | None = None
    cta_type: str | None = None
    card_key: str | None = None


@dataclass
class NormalizedPage:
    id: str | None = None
    name: str | None = None
    profile_picture_url: str | None = None
    profile_uri: str | None = None
    like_count: int | None = None
    categories: list[str] = field(default_factory=list)


@dataclass
class NormalizedAd:
    id: str
    page: NormalizedPage
    is_active: bool | None = None
    ad_status: str | None = None
    delivery_start_time: datetime | None = None
    delivery_stop_time: datetime | None = None
    snapshot_url: str | None = None
    ad_snapshot_url: str | None = None
    publisher_platforms: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    ad_type: str | None = None
    categories: list[str] = field(default_factory=list)
    collation_id: str | None = None
    collation_count: int | None = None
    display_format: str | None = None
    body: str | None = None   # the ad's primary text (cards may have their own)
    creatives: list[NormalizedCreative] = field(default_factory=list)


# ── Parsing Meta's record ───────────────────────────────────────────────────

def _text(value: Any) -> str | None:
    """Card `body` is a string; the ad-level one is `{"text": ...}`."""
    if isinstance(value, dict):
        value = value.get("text")
    return value if isinstance(value, str) else None


def _epoch(value: Any) -> datetime | None:
    """Meta's dates are epoch seconds (UTC)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None


def _card(card: dict, snap: dict) -> NormalizedCreative:
    hd, sd = card.get("video_hd_url"), card.get("video_sd_url")
    return NormalizedCreative(
        body=_text(card.get("body")),
        title=card.get("title"),
        description=card.get("link_description"),
        caption=card.get("caption") or snap.get("caption"),
        link_url=card.get("link_url"),
        image_url=card.get("original_image_url") or card.get("resized_image_url"),
        video_url=hd or sd,
        video_hd_url=hd,
        video_sd_url=sd,
        thumbnail_url=card.get("video_preview_image_url"),
        cta_text=card.get("cta_text") or snap.get("cta_text"),
        cta_type=card.get("cta_type"),
    )


def _single(snap: dict) -> NormalizedCreative | None:
    """An ad without cards: one creative from the ad-level fields and the first
    image/video (the rest are not used, as before)."""
    if not any(snap.get(k) is not None for k in ("body", "title", "videos", "images")):
        return None
    video = (snap.get("videos") or [{}])[0] or {}
    image = (snap.get("images") or [{}])[0] or {}
    hd, sd = video.get("video_hd_url"), video.get("video_sd_url")
    return NormalizedCreative(
        body=_text(snap.get("body")),
        title=snap.get("title"),
        description=snap.get("link_description"),
        caption=snap.get("caption"),
        link_url=snap.get("link_url"),
        image_url=image.get("original_image_url") or image.get("resized_image_url"),
        video_url=hd or sd,
        video_hd_url=hd,
        video_sd_url=sd,
        thumbnail_url=video.get("video_preview_image_url"),
        cta_text=snap.get("cta_text"),
        cta_type=snap.get("cta_type"),
    )


def from_meta_record(rec: dict) -> NormalizedAd:
    """Parse one Ad Library record (Apify item, or the library's `raw_data`)."""
    snap = rec.get("snapshot") or rec
    cards = snap.get("cards") or []
    if cards:
        creatives = [_card(c, snap) for c in cards]
    else:
        single = _single(snap)
        creatives = [single] if single else []

    platforms = rec.get("publisher_platform") or rec.get("publisher_platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    is_active = rec.get("is_active")
    status = rec.get("ad_status")
    if is_active is None and status:
        is_active = status == "ACTIVE"

    return NormalizedAd(
        id=str(rec.get("ad_archive_id") or rec.get("id") or ""),
        page=NormalizedPage(
            id=str(rec.get("page_id") or snap.get("page_id") or "") or None,
            name=rec.get("page_name") or snap.get("page_name"),
            profile_picture_url=snap.get("page_profile_picture_url"),
            profile_uri=snap.get("page_profile_uri"),
            like_count=snap.get("page_like_count"),
            categories=list(snap.get("page_categories") or []),
        ),
        is_active=is_active,
        ad_status=status,
        delivery_start_time=_epoch(rec.get("start_date")),
        # Meta sets an active ad's end_date to the day it was read: not an end.
        delivery_stop_time=None if is_active else _epoch(rec.get("end_date")),
        snapshot_url=rec.get("snapshot_url"),
        ad_snapshot_url=rec.get("ad_snapshot_url"),
        publisher_platforms=list(platforms),
        languages=list(rec.get("languages") or []),
        ad_type=rec.get("ad_type"),
        categories=list(rec.get("categories") or []),
        collation_id=rec.get("collation_id") or None,
        collation_count=rec.get("collation_count"),
        display_format=snap.get("display_format") or None,
        body=_text(snap.get("body")),
        creatives=_with_card_keys(creatives),
    )


# ── Adapters ────────────────────────────────────────────────────────────────

def from_apify_item(item: dict) -> NormalizedAd:
    """An item of the Apify Actor's dataset. Extra keys the Actor adds
    (`daysRunning`, `url`, `_details`) are ignored: we compute our own duration."""
    return from_meta_record(item)


def from_library_ad(ad) -> NormalizedAd:
    """A `meta_ads_collector` `Ad` (Self-hosted source)."""
    raw = getattr(ad, "raw_data", None)
    if isinstance(raw, dict) and (raw.get("ad_archive_id") or raw.get("id")):
        return from_meta_record(raw)
    return _from_library_model(ad)


def _from_library_model(ad) -> NormalizedAd:
    page = getattr(ad, "page", None)
    return NormalizedAd(
        id=str(ad.id),
        page=NormalizedPage(
            id=getattr(page, "id", None) or None,
            name=getattr(page, "name", None) or None,
            profile_picture_url=getattr(page, "profile_picture_url", None),
            profile_uri=getattr(page, "page_url", None),
            like_count=getattr(page, "likes", None),
        ),
        is_active=ad.is_active,
        ad_status=ad.ad_status,
        delivery_start_time=ad.delivery_start_time,
        delivery_stop_time=None if ad.is_active else ad.delivery_stop_time,
        snapshot_url=ad.snapshot_url,
        ad_snapshot_url=ad.ad_snapshot_url,
        publisher_platforms=list(ad.publisher_platforms or []),
        languages=list(ad.languages or []),
        ad_type=ad.ad_type,
        categories=list(ad.categories or []),
        collation_id=getattr(ad, "collation_id", None) or None,
        collation_count=getattr(ad, "collation_count", None),
        creatives=_with_card_keys([
            NormalizedCreative(
                body=c.body, title=c.title, description=c.description,
                caption=getattr(c, "caption", None), link_url=c.link_url,
                image_url=c.image_url,
                video_url=c.video_url, video_hd_url=c.video_hd_url,
                video_sd_url=c.video_sd_url, thumbnail_url=c.thumbnail_url,
                cta_text=c.cta_text, cta_type=c.cta_type,
            )
            for c in (ad.creatives or [])
        ]),
    )


# ── Card identity ───────────────────────────────────────────────────────────
# Meta reorders an Ad's cards between reads, so a card's position can't identify
# it. Its media file can: the file name in the fbcdn path is the same on every
# read, whichever CDN host served it and whatever the expiring query (`oe=`...).

_MEDIA_NAME = re.compile(r"/([^/?#]+)\.(?:jpe?g|png|webp|gif|mp4)(?:[?#]|$)", re.I)


def media_id(url: str | None) -> str:
    """The media file's name in its URL ('' if none), stable across reads."""
    match = _MEDIA_NAME.search(url or "")
    return match.group(1) if match else ""


def _link_key(url: str | None) -> str:
    """A destination without its query string/fragment (per-placement UTM tags)."""
    return (url or "").split("#", 1)[0].split("?", 1)[0]


def card_key(creative: NormalizedCreative) -> str:
    """Media file id + destination. A video card is keyed by its preview image
    (the video's own URL is an opaque token)."""
    media = (media_id(creative.image_url) or media_id(creative.thumbnail_url)
             or media_id(creative.video_url))
    return f"{media}|{_link_key(creative.link_url)}"


def _with_card_keys(creatives: list[NormalizedCreative]) -> list[NormalizedCreative]:
    """Set each card's key; a repeated key (same media and destination) gets its
    occurrence number, so every card of the Ad has its own."""
    seen: dict[str, int] = {}
    for creative in creatives:
        key = card_key(creative)
        n = seen.get(key, 0)
        seen[key] = n + 1
        creative.card_key = key if n == 0 else f"{key}#{n}"
    return creatives
