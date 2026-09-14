"""Persistence of a collection (psycopg + S3 storage).

  - `upsert_ad` / creatives: take a `NormalizedAd` (see `normalize`), whatever the
    Collection source; ads are linked to their Page (`page_ref_id`);
    `publisher_platforms`/`languages` are `TEXT[]`, `categories` is `JSONB`.
  - media is mirrored via `tracker.storage` under the `media_*_key` keys.
  - `mark_disappeared_ads` works per Page: a company has N Pages, and marking per
    company would affect Pages that weren't collected in this run.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import psycopg
from psycopg.types.json import Jsonb

from .. import db, storage
from ..media_urls import is_meta_media_url
from .normalize import NormalizedAd, NormalizedPage, media_id

try:
    from meta_ads_collector.media import (
        detect_extension_from_content_type,
        detect_extension_from_url,
    )
except Exception:  # pragma: no cover - fallback if the package changes
    def detect_extension_from_url(url):  # type: ignore
        return None

    def detect_extension_from_content_type(ct):  # type: ignore
        return None

logger = logging.getLogger(__name__)


# ── Collection runs ─────────────────────────────────────────────────────────

@dataclass
class RunProgress:
    """Counters of an in-flight collection run, written to `collection_runs` as the
    run goes (the Settings UI polls them). `current_step` is what the run is doing
    now: the Page (or Batch of Pages) being collected, or a post-collection step."""
    total: int = 0
    new: int = 0
    pages_done: int = 0
    current_step: str | None = None
    current_page_ads: int = 0
    companies: int = 0
    errors: int = 0
    last_error: str | None = None
    cost_usd: float | None = None   # what Apify charged so far; None when Self-hosted


_PROGRESS_SET = """total_ads_collected = %(total)s, new_ads_discovered = %(new)s,
                   cost_usd = %(cost_usd)s,
                   pages_done = %(pages_done)s, current_step = %(current_step)s,
                   current_page_ads = %(current_page_ads)s,
                   companies_with_results = %(companies)s,
                   errors = %(errors)s, last_error = %(last_error)s, heartbeat_at = now()"""


def start_collection_run(conn: psycopg.Connection, trigger: str = "manual",
                         pages_total: int = 0) -> int:
    row = conn.execute(
        """INSERT INTO collection_runs (started_at, heartbeat_at, trigger, pages_total)
           VALUES (now(), now(), %s, %s) RETURNING id""",
        (trigger, pages_total),
    ).fetchone()
    conn.commit()
    return row["id"]


def update_run_progress(conn: psycopg.Connection, run_id: int, progress: RunProgress) -> None:
    """Live progress of an in-flight run + heartbeat."""
    conn.execute(f"UPDATE collection_runs SET {_PROGRESS_SET} WHERE id = %(run_id)s",
                 {**asdict(progress), "run_id": run_id})
    conn.commit()


def finish_collection_run(conn: psycopg.Connection, run_id: int, progress: RunProgress) -> None:
    done = replace(progress, current_step=None, current_page_ads=0)
    conn.execute(
        f"UPDATE collection_runs SET {_PROGRESS_SET}, finished_at = now() WHERE id = %(run_id)s",
        {**asdict(done), "run_id": run_id},
    )
    conn.commit()


def close_orphan_runs(conn: psycopg.Connection, stale_minutes: int = db.STALE_MINUTES) -> int:
    """Close unfinished runs that stopped sending heartbeats (a worker crashed or
    restarted mid-run), so they don't show as running forever. A run still beating
    is left alone: it may belong to another worker (e.g. during a rolling
    deploy). Returns how many were closed."""
    rows = conn.execute(
        """UPDATE collection_runs
           SET finished_at = COALESCE(heartbeat_at, started_at), errors = errors + 1,
               last_error = 'Interrupted: the worker stopped before this run finished.',
               current_step = NULL, current_page_ads = 0
           WHERE finished_at IS NULL
             AND COALESCE(heartbeat_at, started_at) < now() - make_interval(mins => %s)
           RETURNING id""",
        (stale_minutes,),
    ).fetchall()
    conn.commit()
    return len(rows)


def calc_duration_days(start_time, stop_time, is_active: bool) -> int | None:
    if not start_time:
        return None
    end = stop_time if stop_time else datetime.now(timezone.utc)
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    return max(0, (end - start_time).days)


# ── Ads / creatives ─────────────────────────────────────────────────────────

def upsert_ad(conn: psycopg.Connection, company_id: int, page_ref_id: int, run_id: int,
              ad: NormalizedAd, now: datetime,
              sort_index: int | None = None) -> tuple[int, bool]:
    """Insert/update an ad. Returns (ad_db_id, is_new). Its media is mirrored
    later, by `mirror_pending_media`, so storing a run's ads takes seconds."""
    duration = calc_duration_days(ad.delivery_start_time, ad.delivery_stop_time, ad.is_active)
    platforms = list(ad.publisher_platforms) if ad.publisher_platforms else None
    languages = list(ad.languages) if ad.languages else None
    categories = Jsonb(ad.categories) if ad.categories else None
    # Meta's grouping of ads that are versions of the same ad.
    collation_id = ad.collation_id or None
    collation_count = ad.collation_count

    existing = conn.execute(
        "SELECT id FROM ads WHERE ad_archive_id = %s", (ad.id,)
    ).fetchone()
    is_new = existing is None

    if is_new:
        row = conn.execute(
            """INSERT INTO ads (
                   ad_archive_id, company_id, page_ref_id, page_id, page_name,
                   is_active, ad_status, delivery_start_time, delivery_stop_time, duration_days,
                   snapshot_url, ad_snapshot_url, publisher_platforms, languages, ad_type, categories,
                   collation_id, collation_count, display_format, body,
                   first_seen_at, last_seen_at, last_collection_run_id
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                ad.id, company_id, page_ref_id,
                ad.page.id, ad.page.name,
                ad.is_active, ad.ad_status,
                ad.delivery_start_time, ad.delivery_stop_time, duration,
                ad.snapshot_url, ad.ad_snapshot_url, platforms, languages, ad.ad_type, categories,
                collation_id, collation_count, ad.display_format, ad.body,
                now, now, run_id,
            ),
        ).fetchone()
        ad_db_id = row["id"]
    else:
        ad_db_id = existing["id"]
        conn.execute(
            """UPDATE ads SET
                   page_ref_id = COALESCE(%s, page_ref_id),
                   page_name = COALESCE(%s, page_name),
                   is_active = %s, ad_status = %s,
                   delivery_start_time = COALESCE(%s, delivery_start_time),
                   delivery_stop_time = CASE WHEN %s THEN NULL
                                             ELSE COALESCE(%s, delivery_stop_time) END,
                   duration_days = COALESCE(%s, duration_days),
                   snapshot_url = COALESCE(%s, snapshot_url),
                   ad_snapshot_url = COALESCE(%s, ad_snapshot_url),
                   publisher_platforms = COALESCE(%s::text[], publisher_platforms),
                   languages = COALESCE(%s::text[], languages),
                   ad_type = COALESCE(%s, ad_type),
                   categories = COALESCE(%s::jsonb, categories),
                   collation_id = COALESCE(%s, collation_id),
                   collation_count = COALESCE(%s, collation_count),
                   display_format = COALESCE(%s, display_format),
                   body = COALESCE(%s, body),
                   last_seen_at = %s, last_collection_run_id = %s
               WHERE id = %s""",
            (
                page_ref_id, ad.page.name,
                ad.is_active, ad.ad_status,
                ad.delivery_start_time, bool(ad.is_active), ad.delivery_stop_time, duration,
                ad.snapshot_url, ad.ad_snapshot_url, platforms, languages, ad.ad_type, categories,
                collation_id, collation_count, ad.display_format, ad.body,
                now, run_id, ad_db_id,
            ),
        )

    _upsert_creatives(conn, ad_db_id, ad)

    conn.execute(
        """INSERT INTO ad_appearances (ad_id, collection_run_id, is_active, seen_at, sort_index)
           VALUES (%s, %s, %s, %s, %s)""",
        (ad_db_id, run_id, ad.is_active, now, sort_index),
    )
    conn.commit()
    return ad_db_id, is_new


# ── Pages ───────────────────────────────────────────────────────────────────

def update_page_profile(conn: psycopg.Connection, page_ref_id: int, page: NormalizedPage) -> None:
    """Refresh the Page as Meta shows it: picture, like count, categories, profile
    URL. A new picture file drops the mirrored copy of the old one, so
    `mirror_pending_media` fetches it (under a new storage key, since /media serves
    objects as immutable). A value Meta didn't send keeps the stored one. The name
    is `db.update_page_name`'s job."""
    row = conn.execute(
        "SELECT profile_picture_url, profile_picture_key FROM pages WHERE id = %s",
        (page_ref_id,),
    ).fetchone()
    if row is None:
        return
    url, key = page.profile_picture_url, row["profile_picture_key"]
    if url and media_id(url) != media_id(row["profile_picture_url"]):
        key = None  # another picture: don't keep showing the old one
    conn.execute(
        """UPDATE pages SET
               profile_picture_url = COALESCE(%s, profile_picture_url),
               profile_picture_key = %s,
               profile_uri = COALESCE(%s, profile_uri),
               like_count = COALESCE(%s, like_count),
               categories = COALESCE(%s::text[], categories)
           WHERE id = %s""",
        (url, key, page.profile_uri, page.like_count, page.categories or None, page_ref_id),
    )
    conn.commit()


def _media_prefix(ad_archive_id: str, card_key: str) -> str:
    """Storage key prefix of a card's media, named after the card (not its
    position: a new card at a reused position must not overwrite another's media)."""
    digest = hashlib.sha1(card_key.encode("utf-8")).hexdigest()[:12]
    return f"ads/{ad_archive_id}/{digest}"


_CREATIVE_COLUMNS = (
    "card_key", "creative_index", "body", "title", "description", "caption",
    "link_url", "image_url", "video_url", "video_hd_url", "video_sd_url",
    "thumbnail_url", "cta_text", "cta_type",
    "media_image_key", "media_video_key", "media_thumb_key",
)


def _upsert_creatives(conn: psycopg.Connection, ad_db_id: int, ad: NormalizedAd) -> None:
    """Store the Ad's cards, matched to stored rows by card key (Meta reorders
    cards between reads), so mirrored media stays with its card.
    `creative_index` is only the display order. New media is left for
    `mirror_pending_media` (its key stays empty)."""
    if not ad.creatives:
        return
    rows = conn.execute(
        """SELECT id, card_key, media_image_key, media_video_key, media_thumb_key
           FROM creatives WHERE ad_id = %s""",
        (ad_db_id,),
    ).fetchall()
    by_key = {r["card_key"]: r for r in rows}
    # Park the positions, so reordering can't collide on (ad_id, creative_index).
    conn.execute("UPDATE creatives SET creative_index = -id WHERE ad_id = %s", (ad_db_id,))

    kept: list[int] = []
    for idx, creative in enumerate(ad.creatives):
        row = by_key.get(creative.card_key)
        img_key = row["media_image_key"] if row else None
        vid_key = row["media_video_key"] if row else None
        thumb_key = row["media_thumb_key"] if row else None

        values = (
            creative.card_key, idx, creative.body, creative.title, creative.description,
            creative.caption, creative.link_url, creative.image_url, creative.video_url,
            creative.video_hd_url, creative.video_sd_url, creative.thumbnail_url,
            creative.cta_text, creative.cta_type, img_key, vid_key, thumb_key,
        )
        # landing_page_id is NOT set here — `linking.py` sets it; leaving it out
        # of the UPDATE preserves a link already made.
        if row:
            assignments = ", ".join(f"{col} = %s" for col in _CREATIVE_COLUMNS)
            conn.execute(f"UPDATE creatives SET {assignments} WHERE id = %s", (*values, row["id"]))
            kept.append(row["id"])
        else:
            placeholders = ",".join(["%s"] * (len(_CREATIVE_COLUMNS) + 1))
            new = conn.execute(
                f"""INSERT INTO creatives (ad_id, {", ".join(_CREATIVE_COLUMNS)})
                    VALUES ({placeholders}) RETURNING id""",
                (ad_db_id, *values),
            ).fetchone()
            kept.append(new["id"])

    conn.execute("DELETE FROM creatives WHERE ad_id = %s AND id != ALL(%s)", (ad_db_id, kept))


_FFMPEG_WARNED = False


def _compress_video(src: Path, label: str) -> Path | None:
    """Compress a video file with FFmpeg (~640px wide), next to `src`. Returns the
    compressed file, or None if ffmpeg is missing, fails, or doesn't shrink it."""
    global _FFMPEG_WARNED
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if not _FFMPEG_WARNED:
            logger.warning("ffmpeg missing — videos are stored without compression.")
            _FFMPEG_WARNED = True
        return None
    out = src.with_name(src.stem + "_compressed.mp4")
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", str(src), "-vf", "scale='min(640,iw)':-2",
             "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
             "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)],
            capture_output=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not out.exists():
        return None
    return out if out.stat().st_size < src.stat().st_size else None


# Media quality, chosen in Settings (app_settings.image_quality / video_quality).
MEDIA_QUALITIES = ("standard", "high")
# image quality -> (max width in px, WebP quality, WebP method: higher = slower, smaller)
_IMAGE_PRESETS = {"standard": (1280, 80, 4), "high": (2048, 90, 6)}


def _compress_image(data: bytes, label: str, quality: str = "standard") -> tuple[bytes, str] | None:
    """Downscale (to the quality's max width) and re-encode to WebP. Returns None if
    Pillow is missing, if it fails, or if it doesn't shrink the file (then the
    original is kept)."""
    max_width, webp_quality, method = _IMAGE_PRESETS.get(quality, _IMAGE_PRESETS["standard"])
    try:
        import io

        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        if im.width > max_width:
            new_h = max(1, round(im.height * max_width / im.width))
            im = im.resize((max_width, new_h), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="WEBP", quality=webp_quality, method=method)
        comp = out.getvalue()
    except Exception as exc:
        logger.warning("image compression failed (%s): %s", label, exc)
        return None
    return (comp, "image/webp") if len(comp) < len(data) else None


# Media larger than this isn't mirrored (the ad keeps Meta's link). Images are
# decoded in memory, so they get a lower cap than videos, which stay on disk.
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
_DOWNLOAD_CHUNK = 256 * 1024


class MediaTooLarge(Exception):
    pass


class MediaNotAllowed(Exception):
    """The URL (or a redirect) leaves Meta's CDN: not fetched."""


_MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _get_meta_media(session, url: str):
    """GET `url`, following redirects by hand so every hop is checked BEFORE it
    is requested: media URLs come from collected data (with the Apify source, from
    an Actor outside this instance), so one must not make the worker fetch this
    server's own network."""
    for _ in range(_MAX_REDIRECTS + 1):
        if not is_meta_media_url(url):
            raise MediaNotAllowed(url)
        resp = session.get(url, timeout=30, stream=True, allow_redirects=False)
        location = resp.headers.get("location")
        if getattr(resp, "status_code", 200) not in _REDIRECT_CODES or not location:
            return resp
        resp.close()
        url = urljoin(url, location)
    raise MediaNotAllowed(f"too many redirects: {url}")


def _download(session, url: str, dest: Path) -> str:
    """Stream `url` into `dest`, never holding the body in memory. Returns the
    content type. Raises on HTTP errors, on media over the size cap and on a URL
    (or redirect) outside Meta's CDN."""
    resp = _get_meta_media(session, url)
    try:
        resp.raise_for_status()
        content_type = resp.headers.get("content-type") or ""
        limit = MAX_VIDEO_BYTES if content_type.startswith("video/") else MAX_IMAGE_BYTES
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise MediaTooLarge(f"{int(declared)} bytes")
        written = 0
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=_DOWNLOAD_CHUNK):
                written += len(chunk)
                if written > limit:
                    raise MediaTooLarge(f"over {limit} bytes")
                fh.write(chunk)
        return content_type
    finally:
        resp.close()


def mirror_media(session, url: str, key_without_ext: str, compress_video: bool = True,
                 image_quality: str = "standard") -> str | None:
    """Download `url` via `session` and upload it to storage. Returns the key, or None
    (never raises). A video is re-encoded smaller unless `compress_video` is False
    (Meta's SD file is already small); an image is encoded at `image_quality`."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "media"
        try:
            content_type = _download(session, url, path)
        except MediaTooLarge as exc:
            logger.warning("not mirrored, too large (%s): %s", exc, url)
            return None
        except MediaNotAllowed as exc:
            logger.warning("not mirrored, not a Meta CDN URL: %s", exc)
            return None
        except Exception as exc:
            logger.warning("download failed %s: %s", url, exc)
            return None

        data = None
        ext = detect_extension_from_url(url) or detect_extension_from_content_type(content_type)
        if content_type.startswith("video/"):
            compressed = _compress_video(path, key_without_ext) if compress_video else None
            if compressed:
                path, ext, content_type = compressed, ".mp4", "video/mp4"
            else:
                ext = ext or ".mp4"
        elif content_type.startswith("image/"):
            data = path.read_bytes()  # under MAX_IMAGE_BYTES
            result = _compress_image(data, key_without_ext, image_quality)
            if result:
                (data, content_type), ext = result, ".webp"

        key = f"{key_without_ext}{ext or '.bin'}"
        try:
            if data is not None:
                storage.upload_bytes(key, data, content_type)
            else:
                storage.upload_file(key, path, content_type)
        except Exception as exc:
            logger.warning("upload failed %s: %s", key, exc)
            return None
        return key


# ── Mirroring, after the ads are stored ─────────────────────────────────────
# A run first stores its ads (seconds); then this step copies their new images and
# videos to storage, several at a time, since downloads wait on the network.
# Meta's media URLs expire a few days after they were read, so only media of
# recently seen ads is attempted.

MIRROR_WORKERS = max(2, min(4, os.cpu_count() or 2))
_MIRROR_RECENT_DAYS = 5
# Video quality (Settings). "standard": Meta's SD file as it is (360px wide; already
# smaller than an HD file re-encoded to 640px, and ~15x faster to store). "high": the
# HD file re-encoded to 640px with ffmpeg: sharper, but the slow part of a run.


@dataclass(frozen=True)
class MediaJob:
    table: str      # creatives | pages
    row_id: int
    column: str     # the media key column this fills
    url: str
    key: str        # storage key, without extension
    compress_video: bool = True
    image_quality: str = "standard"


def pending_media(conn: psycopg.Connection, video_quality: str = "standard",
                  image_quality: str = "standard") -> list[MediaJob]:
    """Media not mirrored yet: a creative's image, video or video preview, and a
    Page's picture, to be stored at the given qualities."""
    jobs: list[MediaJob] = []
    rows = conn.execute(
        """SELECT cr.id, cr.card_key, a.ad_archive_id, cr.image_url, cr.video_url, cr.video_sd_url,
                  cr.thumbnail_url, cr.media_image_key, cr.media_video_key, cr.media_thumb_key
           FROM creatives cr JOIN ads a ON a.id = cr.ad_id
           WHERE a.last_seen_at > now() - make_interval(days => %s)
             AND ((cr.image_url IS NOT NULL AND cr.media_image_key IS NULL)
               OR (cr.video_url IS NOT NULL AND cr.media_video_key IS NULL)
               OR (cr.thumbnail_url IS NOT NULL AND cr.media_thumb_key IS NULL))
           ORDER BY a.last_seen_at DESC, cr.id""",
        (_MIRROR_RECENT_DAYS,),
    ).fetchall()
    for r in rows:
        prefix = _media_prefix(r["ad_archive_id"], r["card_key"] or f"creative-{r['id']}")
        for kind, url_col, key_col in (("image", "image_url", "media_image_key"),
                                       ("thumb", "thumbnail_url", "media_thumb_key")):
            if r[url_col] and not r[key_col]:
                jobs.append(MediaJob("creatives", r["id"], key_col, r[url_col], f"{prefix}_{kind}",
                                     image_quality=image_quality))
        if r["video_url"] and not r["media_video_key"]:
            sd = video_quality != "high" and r["video_sd_url"]
            jobs.append(MediaJob("creatives", r["id"], "media_video_key",
                                 sd or r["video_url"], f"{prefix}_video", compress_video=not sd))
    for r in conn.execute(
        """SELECT id, page_id, profile_picture_url FROM pages
           WHERE profile_picture_url IS NOT NULL AND profile_picture_key IS NULL"""
    ).fetchall():
        name = media_id(r["profile_picture_url"]) or "picture"
        jobs.append(MediaJob("pages", r["id"], "profile_picture_key", r["profile_picture_url"],
                             f"pages/{r['page_id']}/{name}", image_quality=image_quality))
    return jobs


def mirror_pending_media(conn: psycopg.Connection, *, make_session,
                         video_quality: str = "standard", image_quality: str = "standard",
                         workers: int = MIRROR_WORKERS, on_progress=None) -> tuple[int, int]:
    """Mirror every pending media (`pending_media`) at the given qualities, with
    `workers` threads, each with its own HTTP session from `make_session()`. Keys are
    written on `conn`, in this thread, as each file is done. Returns (mirrored,
    attempted); a failure leaves the key empty, and the page keeps using Meta's URL."""
    jobs = pending_media(conn, video_quality, image_quality)
    if not jobs:
        return 0, 0
    try:
        storage._client()  # create the S3 client here: boto3 can't create it from several threads at once
    except Exception:
        pass  # each upload then reports its own error
    local, sessions, lock = threading.local(), [], threading.Lock()

    def work(job: MediaJob) -> tuple[MediaJob, str | None]:
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = make_session()
            with lock:
                sessions.append(session)
        return job, mirror_media(session, job.url, job.key, compress_video=job.compress_video,
                                 image_quality=job.image_quality)

    done = mirrored = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(work, job) for job in jobs]):
                job, key = future.result()
                done += 1
                if key:
                    # table/column come from MediaJob's fixed values, never from data
                    conn.execute(
                        f"UPDATE {job.table} SET {job.column} = %s WHERE id = %s AND {job.column} IS NULL",
                        (key, job.row_id),
                    )
                    conn.commit()
                    mirrored += 1
                if on_progress:
                    on_progress(done, len(jobs))
    finally:
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
    return mirrored, len(jobs)


def mark_disappeared_ads(conn: psycopg.Connection, page_ref_id: int, run_id: int) -> int:
    """Mark inactive any ACTIVE ad of this page that didn't show up in this run.
    Uses last_seen_at as an approximation of the end. Returns how many were marked."""
    result = conn.execute(
        """UPDATE ads SET
               is_active = FALSE,
               delivery_stop_time = COALESCE(delivery_stop_time, last_seen_at),
               duration_days = GREATEST(0, (
                   EXTRACT(EPOCH FROM (
                       COALESCE(delivery_stop_time, last_seen_at) - delivery_start_time
                   )) / 86400
               )::int)
           WHERE page_ref_id = %s
             AND is_active = TRUE
             AND last_collection_run_id != %s
             AND delivery_start_time IS NOT NULL
           RETURNING id""",
        (page_ref_id, run_id),
    )
    gone = len(result.fetchall())
    conn.commit()
    return gone
