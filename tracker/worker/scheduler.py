"""Worker scheduler: runs the collection on the `app_settings` interval AND when the
admin UI asks for it ('collect now', via a flag in the database). A `docker compose up`
starts this as the `worker` service.
"""

from __future__ import annotations

import logging
import sys
import time

from .. import db
from . import persist
from .collector import run_collection
from .persist import RunProgress

logger = logging.getLogger(__name__)

POLL_SECONDS = 5  # granularity of the "collect now" trigger check


def _interval_seconds() -> int:
    settings = db.get_app_settings() or {}
    hours = settings.get("collect_interval_hours") or 6
    return max(1, int(hours)) * 3600


def _should_run(now: float, last_run: float, interval_seconds: int, collect_now: bool) -> bool:
    """Pure (testable): run if explicitly requested OR if the interval elapsed."""
    return collect_now or (now - last_run) >= interval_seconds


def _post_collection_steps(step) -> None:
    """Media, linking, landing-page titles. `step(label)` reports what is running."""
    # Copy the run's new images and videos to storage, several at a time. Runs even
    # if the collection failed, to finish media left pending by earlier runs.
    step("Saving images and videos")
    try:
        from .collector import default_download_session
        from .persist import mirror_pending_media

        def media_progress(done: int, total: int) -> None:
            if done == total or done % 5 == 0:  # every few files, not every one
                step(f"Saving images and videos ({done}/{total})")

        settings = db.get_app_settings() or {}
        started = time.monotonic()
        with db.connect() as conn:
            saved, total = mirror_pending_media(
                conn, make_session=default_download_session,
                video_quality=settings.get("video_quality") or "standard",
                image_quality=settings.get("image_quality") or "standard",
                on_progress=media_progress)
        if total:
            logger.info("media: %d of %d file(s) saved in %.0fs", saved, total,
                        time.monotonic() - started)
    except Exception as exc:
        logger.error("saving media failed: %s", exc)
    # Link creatives -> landing pages (no network). Runs even if the
    # collection failed, to drain creatives left pending from earlier runs.
    step("Linking ads to landing pages")
    try:
        from .linking import run_linking
        n = run_linking()
        if n:
            logger.info("linking: %d creative(s) linked to landing pages", n)
    except Exception as exc:
        logger.error("linking failed: %s", exc)
    # Then fetch each landing page's title.
    step("Fetching landing-page titles")
    try:
        from .resolver import run_resolve
        res = run_resolve(
            on_progress=lambda done, total: step(f"Fetching landing-page titles ({done}/{total})")
        )
        if res:
            logger.info("landing pages: %s", res)
    except Exception as exc:
        logger.error("landing-page titles failed: %s", exc)


def _run_once(trigger: str) -> None:
    """One full run: collect, link, fetch titles, then close the run. The run stays
    open (and its progress visible in Settings) until every step is done."""
    run_id, progress = None, RunProgress()
    try:
        run_id, progress = run_collection(trigger=trigger)
    except Exception as exc:  # never kills the loop
        logger.error("collection failed: %s", exc)

    try:
        with db.connect() as conn:
            def step(label: str) -> None:
                if run_id is not None:
                    progress.current_step = label
                    persist.update_run_progress(conn, run_id, progress)

            _post_collection_steps(step)
            if run_id is not None:
                persist.finish_collection_run(conn, run_id, progress)
                logger.info("run %s finished: %d ads (%d new), %d error(s)",
                            run_id, progress.total, progress.new, progress.errors)
    except Exception as exc:
        logger.error("run %s: post-collection steps failed: %s", run_id, exc)
        if run_id is not None:
            _finish_after_failure(run_id, progress, exc)


def _finish_after_failure(run_id: int, progress: RunProgress, exc: Exception) -> None:
    """Close a run whose post-collection steps blew up (e.g. the connection dropped),
    on a fresh connection, so it doesn't sit as 'stalled'. If even that fails, the
    orphan check closes it once its heartbeat is stale."""
    progress.errors += 1
    progress.last_error = f"post-collection: {exc}"
    try:
        with db.connect() as conn:
            persist.finish_collection_run(conn, run_id, progress)
    except Exception as exc2:
        logger.error("run %s: could not close the run: %s", run_id, exc2)


def _close_orphan_runs() -> None:
    try:
        with db.connect() as conn:
            n = persist.close_orphan_runs(conn)
        if n:
            logger.warning("closed %d run(s) that stopped sending heartbeats", n)
    except Exception as exc:
        logger.error("could not close orphan runs: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger.info("scheduler started (poll every %ds)", POLL_SECONDS)
    _close_orphan_runs()
    _run_once("startup")
    last_run = time.time()
    while True:
        time.sleep(POLL_SECONDS)
        # a run left open by a crash is only closed once its heartbeat goes stale
        _close_orphan_runs()
        collect_now = db.consume_collect_now()
        if _should_run(time.time(), last_run, _interval_seconds(), collect_now):
            if collect_now:
                logger.info("collect-now requested from the UI: starting a run")
            else:
                logger.info("interval elapsed: starting a scheduled run")
            _run_once("manual" if collect_now else "schedule")
            last_run = time.time()


if __name__ == "__main__":
    main()
