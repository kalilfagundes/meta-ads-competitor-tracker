"""The ads library, computed in the database: one page of cards at a time.

`ad_summary` turns each ad and its creatives into one row: the media type
(an ad shown by its cards, a video or an image — cards from Meta's display
format, or from the creatives' text when the record has none),
its kind (catalog / dynamic creative, else the media), how many formats or cards
it has, the ad's copy (its own primary text, else the first card's; catalog
{{templates}} and blank text skipped), its hero image and its Page's picture,
its latest impression rank, and how many
Versions Meta groups it with. Filters apply to single ads; Versions
(`collation_id`) are then folded into one card fronted by the active,
longest-running member, carrying the group's best numbers.

Only the requested page of cards ever reaches Python, so the web app's memory
doesn't grow with the size of the library. The CSV export streams the same
filtered rows through a server-side cursor.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ads import ad_kind
from .base import _date_only, connect, resolve_media_url

PAGE_SIZE = 48
CHAMPION_SLOTS = 8
SORTS = ("dur", "new", "rank")

# One row per ad that has creatives. Creatives are read in display order
# (creative_index, then id); "first non-empty" copies the library's card text.
_AD_SUMMARY = """
WITH ad_summary AS (
    SELECT a.id, a.ad_archive_id, COALESCE(a.is_active, FALSE) AS is_active,
           COALESCE(a.duration_days, 0) AS days,
           COALESCE(a.delivery_start_time, a.first_seen_at) AS started,
           a.delivery_start_time, a.delivery_stop_time, a.first_seen_at, a.last_seen_at,
           a.collation_id, a.publisher_platforms, a.languages,
           c.name AS company, p.page_id, p.page_name,
           p.profile_picture_url AS avatar_raw, p.profile_picture_key AS avatar_key,
           a.display_format,
           latest_rank.sort_index,
           cr.n_creatives, cr.title,
           COALESCE(CASE WHEN btrim(a.body) <> '' AND strpos(a.body, '{{') = 0 THEN btrim(a.body) END,
                    cr.body) AS body,
           cr.cta, cr.link_url, cr.landing_page_url,
           cr.thumb_url, cr.thumb_key, cr.image_url, cr.image_key,
           CASE WHEN shows.cards THEN 'carousel'
                WHEN cr.has_video THEN 'video' ELSE 'image' END AS media,
           CASE WHEN shows.cards THEN 1 ELSE cr.n_creatives END AS formats,
           CASE WHEN shows.cards THEN cr.n_creatives ELSE 1 END AS cards,
           CASE WHEN a.collation_id IS NULL THEN 1
                ELSE COUNT(*) OVER (PARTITION BY a.collation_id) END AS versions,
           COALESCE(a.collation_id, 'ad:' || a.ad_archive_id) AS grp
    FROM ads a
    JOIN companies c ON c.id = a.company_id
    LEFT JOIN pages p ON p.id = a.page_ref_id
    JOIN LATERAL (
        SELECT COUNT(*) AS n_creatives,
               -- (records without a display format) a real carousel has distinct cards;
               -- formats of one ad share text and destination (ignoring
               -- per-placement query strings)
               COUNT(*) > 1 AND COUNT(DISTINCT concat_ws(chr(31),
                   COALESCE(x.title, ''), COALESCE(x.body, ''),
                   split_part(split_part(COALESCE(x.link_url, ''), '#', 1), '?', 1),
                   COALESCE(x.cta_text, ''))) > 1 AS is_carousel,
               (array_agg(COALESCE(x.media_video_key, x.video_url) ORDER BY x.creative_index NULLS LAST, x.id))[1]
                   IS NOT NULL AS has_video,
               (array_agg(x.title ORDER BY x.creative_index NULLS LAST, x.id)
                   FILTER (WHERE btrim(x.title) <> '' AND strpos(x.title, '{{') = 0))[1] AS title,
               (array_agg(btrim(x.body) ORDER BY x.creative_index NULLS LAST, x.id)
                   FILTER (WHERE btrim(x.body) <> '' AND strpos(x.body, '{{') = 0))[1] AS body,
               (array_agg(x.cta_text ORDER BY x.creative_index NULLS LAST, x.id) FILTER (WHERE x.cta_text <> ''))[1] AS cta,
               (array_agg(x.link_url ORDER BY x.creative_index NULLS LAST, x.id) FILTER (WHERE x.link_url <> ''))[1] AS link_url,
               (array_agg(lp.url_canonical ORDER BY x.creative_index NULLS LAST, x.id)
                   FILTER (WHERE lp.url_canonical IS NOT NULL))[1] AS landing_page_url,
               (array_agg(x.thumbnail_url ORDER BY x.creative_index NULLS LAST, x.id))[1] AS thumb_url,
               (array_agg(x.media_thumb_key ORDER BY x.creative_index NULLS LAST, x.id))[1] AS thumb_key,
               (array_agg(x.image_url ORDER BY x.creative_index NULLS LAST, x.id))[1] AS image_url,
               (array_agg(x.media_image_key ORDER BY x.creative_index NULLS LAST, x.id))[1] AS image_key
        FROM creatives x
        LEFT JOIN landing_pages lp ON lp.id = x.landing_page_id
        WHERE x.ad_id = a.id
    ) cr ON cr.n_creatives > 0
    -- shown by its cards: from Meta's display format (see db.ads.CARD_FORMATS)
    CROSS JOIN LATERAL (
        SELECT CASE WHEN a.display_format IS NULL THEN cr.is_carousel
                    ELSE a.display_format IN ('CAROUSEL', 'DCO', 'DPA') AND cr.n_creatives > 1
               END AS cards
    ) shows
    LEFT JOIN LATERAL (
        SELECT sort_index FROM ad_appearances
        WHERE ad_id = a.id AND is_active = TRUE
        ORDER BY seen_at DESC LIMIT 1
    ) latest_rank ON TRUE
)
"""

_ORDER = {
    "dur": "days DESC, company, ad_archive_id",
    "new": "started DESC NULLS LAST, days ASC, ad_archive_id",
    # ranks are per company, so companies stay apart
    "rank": "company, rank ASC NULLS LAST, days DESC, ad_archive_id",
}


@dataclass(frozen=True)
class LibraryFilters:
    company: str = ""
    media: str = ""          # "" | image | video | carousel
    status: str = "active"   # active | inactive | all
    min_days: int = 0
    q: str = ""

    def where(self) -> tuple[str, dict]:
        clauses, params = ["TRUE"], {}
        if self.company:
            clauses.append("company = %(company)s")
            params["company"] = self.company
        if self.media in ("image", "video", "carousel"):
            clauses.append("media = %(media)s")
            params["media"] = self.media
        if self.status == "active":
            clauses.append("is_active")
        elif self.status == "inactive":
            clauses.append("NOT is_active")
        if self.min_days > 0:
            clauses.append("days >= %(min_days)s")
            params["min_days"] = self.min_days
        if self.q.strip():
            clauses.append(
                "strpos(lower(company || ' ' || COALESCE(title, '') || ' ' || COALESCE(body, '')), %(q)s) > 0"
            )
            params["q"] = self.q.strip().lower()
        return " AND ".join(clauses), params


def _hero(row: dict) -> str:
    return (resolve_media_url(row["thumb_url"], row["thumb_key"])
            or resolve_media_url(row["image_url"], row["image_key"]) or "")


def get_library_page(filters: LibraryFilters, sort: str = "dur", page: int = 0,
                     page_size: int = PAGE_SIZE) -> dict:
    """One page of cards (Versions folded) plus the total, for the current filters."""
    where, params = filters.where()
    order = _ORDER.get(sort, _ORDER["dur"])
    with connect() as conn:
        total = conn.execute(
            _AD_SUMMARY + f"SELECT COUNT(DISTINCT grp) AS n FROM ad_summary WHERE {where}", params
        ).fetchone()["n"]
        pages = max(1, -(-total // page_size))
        page = min(max(0, page), pages - 1)
        rows = conn.execute(
            _AD_SUMMARY + f"""
            , filtered AS (SELECT * FROM ad_summary WHERE {where})
            , grouped AS (
                SELECT grp, BOOL_OR(is_active) AS active, MAX(days) AS days,
                       MIN(sort_index) AS rank, MIN(started) AS started
                FROM filtered GROUP BY grp
            )
            , fronts AS (
                SELECT DISTINCT ON (grp) * FROM filtered
                ORDER BY grp, is_active DESC, days DESC, ad_archive_id
            )
            SELECT * FROM (
                SELECT f.id, f.ad_archive_id, f.company, f.title, f.body, f.cta, f.media,
                       f.formats, f.cards, f.display_format, f.page_name, f.avatar_raw, f.avatar_key,
                       f.versions, f.thumb_url, f.thumb_key, f.image_url, f.image_key,
                       g.active, g.days, g.rank, g.started
                FROM fronts f JOIN grouped g USING (grp)
            ) cards
            ORDER BY {order}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {**params, "limit": page_size, "offset": page * page_size},
        ).fetchall()


    cards = []
    for r in rows:
        hero = _hero(r)
        cards.append({
            "id": r["ad_archive_id"], "co": r["company"], "days": r["days"], "rank": r["rank"],
            "active": r["active"], "title": r["title"] or "", "body": r["body"] or "",
            "cta": r["cta"] or "", "media": r["media"], "formats": r["formats"],
            "kind": ad_kind(r["display_format"], r["media"]), "cards": r["cards"],
            "page": r["page_name"] or "", "avatar": resolve_media_url(r["avatar_raw"], r["avatar_key"]) or "",
            "versions": r["versions"], "hero": hero,
        })
    return {"cards": cards, "total": total, "page": page, "pages": pages, "page_size": page_size}


def get_champions(company: str = "") -> list[dict]:
    """"What's working": active ads (Versions folded), shared out between
    competitors in rounds — each company's longest runner, then each one's second,
    and so on — so one company can't fill every slot."""
    params = {"company": company, "slots": CHAMPION_SLOTS}
    with connect() as conn:
        rows = conn.execute(
            _AD_SUMMARY + """
            , active AS (
                SELECT * FROM ad_summary
                WHERE is_active AND (%(company)s = '' OR company = %(company)s)
            )
            , grouped AS (SELECT grp, MAX(days) AS days, MIN(sort_index) AS rank FROM active GROUP BY grp)
            , fronts AS (
                SELECT DISTINCT ON (grp) grp, ad_archive_id, company,
                       thumb_url, thumb_key, image_url, image_key
                FROM active ORDER BY grp, days DESC, ad_archive_id
            )
            , cards AS (
                SELECT f.*, g.days, g.rank,
                       ROW_NUMBER() OVER (PARTITION BY f.company ORDER BY g.days DESC, f.ad_archive_id) AS round
                FROM fronts f JOIN grouped g USING (grp)
            )
            SELECT * FROM cards
            ORDER BY round, days DESC, ad_archive_id
            LIMIT GREATEST(%(slots)s, (SELECT COUNT(DISTINCT company) FROM cards))
            """,
            params,
        ).fetchall()
    return [{"id": r["ad_archive_id"], "co": r["company"], "days": r["days"],
             "rank": r["rank"], "hero": _hero(r)} for r in rows]


def get_library_companies() -> list[str]:
    """Names of the companies that have at least one ad in the library."""
    with connect() as conn:
        return [r["name"] for r in conn.execute(
            """SELECT DISTINCT c.name FROM companies c
               WHERE EXISTS (SELECT 1 FROM ads a JOIN creatives cr ON cr.ad_id = a.id
                             WHERE a.company_id = c.id)
               ORDER BY c.name"""
        ).fetchall()]


def iter_ads_for_export(filters: LibraryFilters):
    """Every filtered ad as a flat row, longest-running first, streamed from a
    server-side cursor (one ad per row: Versions are not folded)."""
    where, params = filters.where()
    with connect() as conn:
        with conn.cursor(name="ads_export") as cur:
            cur.itersize = 500
            cur.execute(_AD_SUMMARY + f"SELECT * FROM ad_summary WHERE {where} ORDER BY {_ORDER['dur']}", params)
            for r in cur:
                yield {
                    "ad_archive_id": r["ad_archive_id"],
                    "company": r["company"],
                    "page_id": r["page_id"] or "",
                    "page_name": r["page_name"] or "",
                    "status": "active" if r["is_active"] else "inactive",
                    "duration_days": r["days"],
                    "started": _date_only(r["delivery_start_time"]) or "",
                    "stopped": _date_only(r["delivery_stop_time"]) or "",
                    "first_seen": _date_only(r["first_seen_at"]) or "",
                    "last_seen": _date_only(r["last_seen_at"]) or "",
                    "media": r["media"],
                    "creatives": r["n_creatives"],
                    "platforms": ";".join(p.lower() for p in r["publisher_platforms"] or []),
                    "languages": ";".join(r["languages"] or []),
                    "impression_rank": (r["sort_index"] + 1
                                        if r["is_active"] and r["sort_index"] is not None else ""),
                    "versions_group": r["collation_id"] or "",
                    "title": r["title"] or "",
                    "body": r["body"] or "",
                    "cta": r["cta"] or "",
                    "destination_url": r["link_url"] or "",
                    "landing_page_url": r["landing_page_url"] or "",
                    "ad_library_url": f"https://www.facebook.com/ads/library/?id={r['ad_archive_id']}",
                }
