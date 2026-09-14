"""Admin CRUD: companies -> pages, proxies, config write, and the run status."""

from __future__ import annotations

from enum import Enum

import psycopg

from .base import connect, resolve_media_url
from .settings import STALE_MINUTES


def list_pages(company_id: int | None = None) -> list[dict]:
    """Tracked pages (all, or for one company). Feeds the admin/companies UI."""
    with connect() as conn:
        if company_id is None:
            return conn.execute(
                """SELECT p.id, p.company_id, c.name AS company_name, p.page_id,
                          p.page_name, p.page_url, p.is_tracked
                   FROM pages p JOIN companies c ON c.id = p.company_id
                   ORDER BY c.name, p.page_name""",
            ).fetchall()
        return conn.execute(
            """SELECT id, company_id, page_id, page_name, page_url, is_tracked
               FROM pages WHERE company_id = %s ORDER BY page_name""",
            (company_id,),
        ).fetchall()


def list_companies_with_pages() -> list[dict]:
    """Companies with their pages nested (for the admin UI)."""
    with connect() as conn:
        companies = conn.execute("SELECT id, name, domain FROM companies ORDER BY name").fetchall()
        # has_ads: the page ID is locked (see `update_page`)
        pages = conn.execute(
            """SELECT id, company_id, page_id, page_name, collect_country, is_tracked,
                      profile_picture_url, profile_picture_key, like_count, categories,
                      EXISTS (SELECT 1 FROM ads WHERE ads.page_ref_id = pages.id) AS has_ads
               FROM pages ORDER BY page_name"""
        ).fetchall()
    for p in pages:
        # the Page's picture: mirrored copy, else Meta's (expiring) URL, else None
        p["avatar_url"] = resolve_media_url(p.pop("profile_picture_url"), p.pop("profile_picture_key"))
    by_co: dict[int, list] = {}
    for p in pages:
        by_co.setdefault(p["company_id"], []).append(p)
    for c in companies:
        c["pages"] = by_co.get(c["id"], [])
    return companies


def create_company(name: str, domain: str | None = None) -> dict:
    with connect() as conn:
        return conn.execute(
            "INSERT INTO companies (name, domain) VALUES (%s, %s) RETURNING id, name",
            (name, domain or None),
        ).fetchone()


def update_company(company_id: int, name: str, domain: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE companies SET name = %s, domain = %s WHERE id = %s",
            (name, domain or None, company_id),
        )


def delete_company(company_id: int) -> bool:
    """Delete the company (cascades to pages). False if it still has associated ads."""
    try:
        with connect() as conn:
            conn.execute("DELETE FROM companies WHERE id = %s", (company_id,))
        return True
    except psycopg.errors.ForeignKeyViolation:
        return False


def create_page(company_id: int, page_id: str, page_name: str | None = None,
                collect_country: str | None = None) -> dict:
    """Register/relocate a Page. Re-adding a page_id moves it to this company
    (lets you 'unify' pages under one company). A name given here is the admin's
    and is kept over the name from Facebook. `collect_country` None = the default."""
    with connect() as conn:
        return conn.execute(
            """INSERT INTO pages (company_id, page_id, page_name, page_name_from_admin, collect_country)
               VALUES (%(company_id)s, %(page_id)s, %(name)s, %(name)s::text IS NOT NULL, %(country)s)
               ON CONFLICT (page_id) DO UPDATE SET
                   company_id = EXCLUDED.company_id,
                   page_name = COALESCE(EXCLUDED.page_name, pages.page_name),
                   page_name_from_admin = EXCLUDED.page_name_from_admin OR pages.page_name_from_admin,
                   collect_country = EXCLUDED.collect_country
               RETURNING id""",
            {"company_id": company_id, "page_id": page_id.strip(), "name": page_name or None,
             "country": collect_country or None},
        ).fetchone()


class PageEditError(str, Enum):
    """Why `update_page` refused an edit."""
    NOT_FOUND = "not_found"
    ID_LOCKED = "id_locked"    # the Page already has ads, which belong to its current ID
    ID_TAKEN = "id_taken"      # another Page already has that ID


def update_page(page_pk: int, page_id: str, page_name: str | None = None,
                collect_country: str | None = None) -> PageEditError | None:
    """Rename a Page, fix its page ID and/or set its country. Returns None on success.

    A name set here is the admin's and is kept over the name from Facebook; an
    empty name hands it back to Facebook. `collect_country` None = the default.
    """
    page_id = page_id.strip()
    with connect() as conn:
        # Lock the row first: inserting an ad takes a key-share lock on its Page, so
        # the worker can't add the Page's first ad between the check and the update.
        page = conn.execute(
            "SELECT page_id FROM pages WHERE id = %s FOR UPDATE", (page_pk,)
        ).fetchone()
        if page is None:
            return PageEditError.NOT_FOUND
        if page_id != page["page_id"] and conn.execute(
            "SELECT EXISTS (SELECT 1 FROM ads WHERE page_ref_id = %s) AS has_ads", (page_pk,)
        ).fetchone()["has_ads"]:
            return PageEditError.ID_LOCKED
        try:
            conn.execute(
                """UPDATE pages SET page_id = %(page_id)s, page_name = %(name)s,
                                    page_name_from_admin = %(name)s::text IS NOT NULL,
                                    collect_country = %(country)s
                   WHERE id = %(pk)s""",
                {"page_id": page_id, "name": page_name or None, "pk": page_pk,
                 "country": collect_country or None},
            )
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            return PageEditError.ID_TAKEN
    return None


def toggle_page_tracked(page_pk: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE pages SET is_tracked = NOT is_tracked WHERE id = %s", (page_pk,))


def delete_page(page_pk: int) -> bool:
    try:
        with connect() as conn:
            conn.execute("DELETE FROM pages WHERE id = %s", (page_pk,))
        return True
    except psycopg.errors.ForeignKeyViolation:
        return False


def list_proxies() -> list[dict]:
    with connect() as conn:
        return conn.execute("SELECT id, url, enabled FROM proxies ORDER BY id").fetchall()


def create_proxy(url: str) -> dict:
    with connect() as conn:
        return conn.execute(
            "INSERT INTO proxies (url) VALUES (%s) RETURNING id, url", (url.strip(),)
        ).fetchone()


def toggle_proxy_enabled(proxy_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE proxies SET enabled = NOT enabled WHERE id = %s", (proxy_id,))


def delete_proxy(proxy_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM proxies WHERE id = %s", (proxy_id,))


def update_app_settings(*, collect_interval_hours: int, collect_country: str) -> None:
    """The collection schedule and the default country."""
    with connect() as conn:
        conn.execute(
            """UPDATE app_settings SET
                   collect_interval_hours = %s, collect_country = %s, updated_at = now()
               WHERE id = 1""",
            (collect_interval_hours, collect_country),
        )


COLLECT_SOURCES = ("self_hosted", "apify")
MEDIA_QUALITIES = ("standard", "high")


def update_media_quality(*, video_quality: str, image_quality: str) -> None:
    """How new images and videos are stored (media already stored stays as it is)."""
    with connect() as conn:
        conn.execute(
            """UPDATE app_settings SET video_quality = %s, image_quality = %s, updated_at = now()
               WHERE id = 1""",
            (video_quality, image_quality),
        )


def update_collection_source(source: str, *, apify_max_parallel_runs: int,
                             apify_token: str | None = None) -> None:
    """The Collection source and the Apify source's settings. A missing token
    keeps the stored one (the form never shows it back)."""
    with connect() as conn:
        conn.execute(
            """UPDATE app_settings SET
                   collect_source = %s, apify_max_parallel_runs = %s,
                   apify_token = COALESCE(%s, apify_token), updated_at = now()
               WHERE id = 1""",
            (source, apify_max_parallel_runs, apify_token or None),
        )


def set_interface_language(language: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE app_settings SET language = %s, updated_at = now() WHERE id = 1", (language,)
        )



def complete_setup() -> None:
    """The first-run wizard is done (or was skipped)."""
    with connect() as conn:
        conn.execute("UPDATE app_settings SET setup_completed = TRUE, updated_at = now() WHERE id = 1")


_RUNS_SQL = """
    SELECT id, trigger, started_at, finished_at, total_ads_collected,
           new_ads_discovered, companies_with_results,
           pages_total, pages_done, current_step, current_page_ads, errors, last_error, cost_usd,
           EXTRACT(EPOCH FROM (COALESCE(finished_at, now()) - started_at))::int AS elapsed_s,
           EXTRACT(EPOCH FROM (now() - COALESCE(heartbeat_at, started_at)))::int AS idle_s,
           EXTRACT(EPOCH FROM (now() - finished_at))::int AS since_finish_s,
           (finished_at IS NULL
            AND COALESCE(heartbeat_at, started_at) < now() - make_interval(mins => %(stale)s)) AS crashed
    FROM collection_runs ORDER BY started_at DESC LIMIT %(limit)s
"""


def _run_state(collect_now: bool, request_pending: bool, latest: dict | None) -> str:
    """'running' | 'queued' | 'stalled' | 'idle' — see `get_run_status`."""
    if latest is not None and latest["finished_at"] is None and not latest["crashed"]:
        return "running"
    if collect_now or request_pending:
        return "queued"
    if latest is not None and latest["crashed"]:
        return "stalled"
    return "idle"


def get_run_status(limit: int = 10) -> dict:
    """What the collection pipeline is doing now, for the Settings UI (polled).

    state: 'running' (a run is in flight, post-collection steps included) |
    'queued' ('collect now' requested, not picked up yet) | 'stalled' (the latest
    run stopped sending heartbeats) | 'idle'. Also carries the latest run's live
    progress and the recent runs.
    """
    with connect() as conn:
        s = conn.execute(
            """SELECT collect_now, collect_now_requested_at, collect_interval_hours,
                      EXTRACT(EPOCH FROM (now() - collect_now_requested_at))::int AS requested_ago_s
               FROM app_settings WHERE id = 1"""
        ).fetchone()
        runs = conn.execute(_RUNS_SQL, {"stale": STALE_MINUTES, "limit": limit}).fetchall()

    latest = runs[0] if runs else None
    requested_at = s["collect_now_requested_at"]
    # The worker clears the flag a moment before it opens the run row; count that
    # gap as still queued so the UI never flickers back to idle.
    request_pending = (
        requested_at is not None and s["requested_ago_s"] < 60
        and (latest is None or requested_at > latest["started_at"])
    )
    state = _run_state(bool(s["collect_now"]), request_pending, latest)

    next_run_in_s = None
    if state == "idle" and latest is not None and latest["since_finish_s"] is not None:
        interval_s = max(1, int(s["collect_interval_hours"] or 6)) * 3600
        next_run_in_s = max(0, interval_s - latest["since_finish_s"])

    return {
        "state": state,
        "requested_ago_s": s["requested_ago_s"] if state == "queued" else None,
        "interval_hours": s["collect_interval_hours"],
        "next_run_in_s": next_run_in_s,
        "stale_minutes": STALE_MINUTES,
        "run": latest,
        "runs": runs,
    }
