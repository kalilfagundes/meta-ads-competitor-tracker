"""Operational config read by the worker: app settings, enabled proxies,
tracked pages, and the 'collect now' signal. The admin write side lives in `admin`.
"""

from __future__ import annotations

from .base import connect

# An unfinished run with no heartbeat for this long is treated as stalled. The
# worker beats after every ad and every landing page, each well under this.
STALE_MINUTES = 15


def get_app_settings() -> dict:
    """The single operational-config row."""
    with connect() as conn:
        return conn.execute("SELECT * FROM app_settings WHERE id = 1").fetchone()


def list_enabled_proxies() -> list[str]:
    """URLs of the enabled proxies (the pool the collector rotates)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT url FROM proxies WHERE enabled = TRUE ORDER BY id"
        ).fetchall()
        return [r["url"] for r in rows]


def list_tracked_pages() -> list[dict]:
    """Pages the admin wants to collect (is_tracked), each with its own country
    (None = the default country). Feeds each run."""
    with connect() as conn:
        return conn.execute(
            """SELECT id, company_id, page_id, page_name, collect_country
               FROM pages WHERE is_tracked = TRUE ORDER BY id"""
        ).fetchall()


def update_page_name(page_pk: int, page_name: str) -> None:
    """Take the Page name from Meta, unless the admin set it (on create or via Edit):
    that name is kept, so an edit isn't undone by the next run. A name Meta filled
    follows later renames on Facebook."""
    with connect() as conn:
        conn.execute(
            "UPDATE pages SET page_name = %s WHERE id = %s AND NOT page_name_from_admin",
            (page_name, page_pk),
        )


def request_collect_now() -> bool:
    """Ask for an immediate run (the scheduler consumes the signal).

    Single-flight, in one atomic statement: returns False and changes nothing while
    a request is pending (flag set, or taken by the worker less than a minute ago
    but its run not opened yet) or a run is in flight. A stalled run doesn't block.
    """
    with connect() as conn:
        row = conn.execute(
            """UPDATE app_settings SET collect_now = TRUE, collect_now_requested_at = now()
               WHERE id = 1
                 AND collect_now = FALSE
                 AND (collect_now_requested_at IS NULL
                      OR collect_now_requested_at <= now() - interval '60 seconds'
                      OR EXISTS (SELECT 1 FROM collection_runs r
                                 WHERE r.started_at >= app_settings.collect_now_requested_at))
                 AND NOT EXISTS (SELECT 1 FROM collection_runs r
                                 WHERE r.finished_at IS NULL
                                   AND COALESCE(r.heartbeat_at, r.started_at)
                                       >= now() - make_interval(mins => %s))
               RETURNING 1 AS ok""",
            (STALE_MINUTES,),
        ).fetchone()
        return row is not None


def consume_collect_now() -> bool:
    """Consume the signal atomically: returns True if it was set (and resets it)."""
    with connect() as conn:
        row = conn.execute(
            "UPDATE app_settings SET collect_now = FALSE WHERE id = 1 AND collect_now = TRUE RETURNING 1 AS ok"
        ).fetchone()
        return row is not None
