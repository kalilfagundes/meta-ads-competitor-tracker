"""Worker scheduler: runs the collection on the `app_settings` interval AND when the
admin UI asks for it ('collect now', via a flag in the database). A `docker compose up`
starts this as the `worker` service.

Only one worker collects at a time: it holds the worker lock (`WorkerLock`) for as
long as it runs, and a second one waits for it. So a worker that gets the lock knows
any run still open was left by a worker that is gone. A run through Apify is then
resumed: its Actor runs went on on Apify, so the new worker follows them, stores
their ads and starts only what was never started, rather than paying for it all
again. Any other run left open is closed as interrupted. A worker that is stopped (a
restart, a deploy) closes its own open run on the way out, or, when it's a run
through Apify, leaves it paused for the next worker.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

import psycopg

from .. import db
from . import persist
from .collector import run_collection
from .persist import RunProgress

logger = logging.getLogger(__name__)

POLL_SECONDS = 5  # granularity of the "collect now" trigger check
# The worker lock's pg_advisory_lock key: any bigint no other code in the database uses.
WORKER_LOCK_KEY = 4_730_216_519_874_332
# A run through Apify left open longer ago than this is closed, not resumed: its
# results are stale by then, and Apify may no longer have them.
RESUME_WITHIN_HOURS = 24

# The post-collection steps, as the Settings screen shows them (static/js/admin.js
# translates them, so keep the two in step).
STEP_MEDIA = "Saving images and videos"
STEP_LINKING = "Linking ads to landing pages"
STEP_TITLES = "Fetching landing-page titles"


class WorkerLock:
    """A Postgres advisory lock the worker holds, on a connection of its own, for as
    long as it runs. Postgres lets go of it as soon as that connection is gone (the
    process exited, crashed or was killed), so whoever gets it knows no other worker
    is collecting."""

    def __init__(self) -> None:
        self._conn: psycopg.Connection | None = None

    @property
    def held(self) -> bool:
        return self._conn is not None

    def try_take(self) -> bool:
        conn = db.connect(autocommit=True, application_name="tracker-worker")
        try:
            # If this worker's machine vanishes without closing the connection,
            # Postgres notices within a minute or so and frees the lock.
            for setting in ("tcp_keepalives_idle = 30", "tcp_keepalives_interval = 10",
                            "tcp_keepalives_count = 3"):
                try:
                    conn.execute(f"SET {setting}")
                except psycopg.Error:
                    pass  # not supported where the database runs: its defaults apply
            got = conn.execute("SELECT pg_try_advisory_lock(%s::bigint) AS got",
                               (WORKER_LOCK_KEY,)).fetchone()["got"]
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return False
        self._conn = conn
        return True

    def take(self) -> None:
        """Wait until this worker holds the lock."""
        waited = 0
        while True:
            try:
                if self.try_take():
                    return
                if waited % 60 == 0:
                    logger.warning("another worker is running: waiting for it to stop")
            except Exception as exc:
                if waited % 60 == 0:
                    logger.error("could not take the worker lock: %s", exc)
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    def still_held(self) -> bool:
        """False once the lock's connection is gone: a database restart drops it, and
        the lock with it."""
        if self._conn is None:
            return False
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            self.release()
            return False

    def release(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def _interval_seconds() -> int:
    settings = db.get_app_settings() or {}
    hours = settings.get("collect_interval_hours") or 6
    return max(1, int(hours)) * 3600


def _should_run(now: float, last_run: float, interval_seconds: int, collect_now: bool) -> bool:
    """Pure (testable): run if explicitly requested OR if the interval elapsed."""
    return collect_now or (now - last_run) >= interval_seconds


def _post_collection_steps(step, fail) -> None:
    """Media, linking, landing-page titles. `step(label)` reports what is running;
    `fail(label, detail)` reports a step that went wrong, as an error of the run."""
    # Copy the run's new images and videos to storage, several at a time. Runs even
    # if the collection failed, to finish media left pending by earlier runs.
    step(STEP_MEDIA)
    media = None
    try:
        from .collector import default_download_session
        from .persist import mirror_pending_media

        def media_progress(done: int, total: int) -> None:
            if done == total or done % 5 == 0:  # every few files, not every one
                step(f"{STEP_MEDIA} ({done}/{total})")

        settings = db.get_app_settings() or {}
        started = time.monotonic()
        with db.connect() as conn:
            media = mirror_pending_media(
                conn, make_session=default_download_session,
                video_quality=settings.get("video_quality") or "standard",
                image_quality=settings.get("image_quality") or "standard",
                on_progress=media_progress)
        if media.total:
            logger.info("media: %d of %d file(s) saved in %.0fs", media.saved, media.total,
                        time.monotonic() - started)
    except Exception as exc:
        logger.error("saving media failed: %s", exc)
        fail(STEP_MEDIA, exc)
    if media and media.not_stored:
        fail(STEP_MEDIA, f"storage refused {media.not_stored} of {media.total} file(s): "
                         f"{media.store_error}")
    # Link creatives -> landing pages (no network). Runs even if the
    # collection failed, to drain creatives left pending from earlier runs.
    step(STEP_LINKING)
    try:
        from .linking import run_linking
        n = run_linking()
        if n:
            logger.info("linking: %d creative(s) linked to landing pages", n)
    except Exception as exc:
        logger.error("linking failed: %s", exc)
        fail(STEP_LINKING, exc)
    # Then fetch each landing page's title.
    step(STEP_TITLES)
    try:
        from .resolver import run_resolve
        res = run_resolve(
            on_progress=lambda done, total: step(f"{STEP_TITLES} ({done}/{total})")
        )
        if res:
            logger.info("landing pages: %s", res)
    except Exception as exc:
        logger.error("landing-page titles failed: %s", exc)
        fail(STEP_TITLES, exc)


def _run_once(trigger: str, resume: int | None = None) -> None:
    """One full run: collect (or, with `resume`, carry on the run of that id through
    Apify that a stopped worker left), link, fetch titles, then close the run. The
    run stays open (and its progress visible in Settings) until every step is done,
    and what goes wrong on the way is counted as an error of the run."""
    run_id, progress = None, RunProgress()
    try:
        run_id, progress = run_collection(trigger=trigger, resume=resume)
    except Exception as exc:  # never kills the loop
        logger.error("collection failed: %s", exc)
        if resume is not None:  # closed with the counters it had
            _close_open_runs(f"Interrupted: it couldn't be resumed ({exc})")
            return
        progress.errors, progress.last_error = 1, f"collector: {exc}"
        run_id = _open_run(trigger)

    try:
        with db.connect() as conn:
            def report() -> None:
                if run_id is not None:
                    persist.update_run_progress(conn, run_id, progress)

            def step(label: str) -> None:
                progress.current_step = label
                report()

            def fail(label: str, detail: object) -> None:
                progress.errors += 1
                progress.last_error = f"{label}: {detail}"
                report()

            _post_collection_steps(step, fail)
            if run_id is not None:
                persist.finish_collection_run(conn, run_id, progress)
                logger.info("run %s finished: %d ads (%d new), %d error(s)",
                            run_id, progress.total, progress.new, progress.errors)
    except Exception as exc:
        logger.error("run %s: post-collection steps failed: %s", run_id, exc)
        if run_id is not None:
            _finish_after_failure(run_id, progress, exc)


def _open_run(trigger: str) -> int | None:
    """A run for a collection that failed before it opened one, so the failure
    shows in Settings instead of the request just vanishing."""
    try:
        with db.connect() as conn:
            return persist.start_collection_run(conn, trigger=trigger)
    except Exception as exc:
        logger.error("could not record the failed collection: %s", exc)
        return None


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


def _close_open_runs(reason: str, keep=(), pause_apify: bool = False) -> None:
    """Close every unfinished run as interrupted, except those in `keep` and, with
    `pause_apify`, those through Apify, left paused for the next worker to resume.
    Only for the lock holder: any run still open is then its own or a dead worker's."""
    try:
        with db.connect(connect_timeout=5) as conn:
            conn.execute("SET statement_timeout = 5000")  # never hang a shutdown on a row lock
            paused = persist.pause_apify_runs(conn) if pause_apify else []
            n = persist.close_orphan_runs(conn, stale_minutes=None, reason=reason,
                                          keep=[*keep, *paused])
        if paused:
            logger.warning("left run(s) %s paused: the next worker resumes their Actor runs",
                           ", ".join(map(str, paused)))
        if n:
            logger.warning("closed %d unfinished run(s) as interrupted", n)
    except Exception as exc:
        logger.error("could not close unfinished runs: %s", exc)


def _resumable_run() -> int | None:
    """The run through Apify a worker that is gone left open, if this one can carry
    it on: ads are still read through Apify, and it started recently enough."""
    try:
        settings = db.get_app_settings() or {}
        if settings.get("collect_source") != "apify" or not settings.get("apify_token"):
            return None
        with db.connect() as conn:
            return persist.resumable_run(conn, within_hours=RESUME_WITHIN_HOURS)
    except Exception as exc:
        logger.error("could not look for a run to resume: %s", exc)
        return None


def _take_the_lock(lock: WorkerLock) -> int | None:
    """Hold the worker lock, then deal with the runs a worker that is gone left
    open: returns the run through Apify to resume, if there is one, and closes the
    others."""
    lock.take()
    logger.info("holding the worker lock: no other worker is collecting")
    resume = _resumable_run()
    _close_open_runs(persist.RUN_INTERRUPTED, keep=[resume] if resume is not None else [])
    if resume is not None:
        logger.info("run %s: a stopped worker left it open with Actor runs on Apify: resuming it", resume)
    return resume


def _stop_handler(lock: WorkerLock):
    """On SIGTERM (docker stop: a restart, a deploy) or Ctrl+C: exit at once, closing
    the run in flight as interrupted rather than leaving it looking alive until its
    heartbeat goes stale; a run through Apify is left paused instead, since its
    Actor runs go on and the next worker resumes it. Downloads under way are
    dropped; the next run picks their files up again."""
    def stop(signum, frame) -> None:
        logger.warning("stopping (%s)", signal.Signals(signum).name)
        if lock.held:
            _close_open_runs(persist.RUN_STOPPED, pause_apify=True)
        os._exit(0)
    return stop


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    lock = WorkerLock()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _stop_handler(lock))
    logger.info("scheduler started (poll every %ds)", POLL_SECONDS)
    # a resumed run stands for the startup run: collecting again would pay twice
    _run_once("startup", resume=_take_the_lock(lock))
    last_run = time.time()
    while True:
        time.sleep(POLL_SECONDS)
        try:
            if not lock.still_held():
                logger.warning("lost the worker lock (did the database restart?): taking it again")
                resume = _take_the_lock(lock)
                if resume is not None:
                    _run_once("startup", resume=resume)
                    last_run = time.time()
            # a run this worker couldn't close (the database was down) is closed once
            # its heartbeat goes stale
            _close_orphan_runs()
            collect_now = db.consume_collect_now()
            if _should_run(time.time(), last_run, _interval_seconds(), collect_now):
                if collect_now:
                    logger.info("collect-now requested from the UI: starting a run")
                else:
                    logger.info("interval elapsed: starting a scheduled run")
                _run_once("manual" if collect_now else "schedule")
                last_run = time.time()
        except Exception as exc:  # a database hiccup must not stop the worker
            logger.error("scheduler: %s", exc)


if __name__ == "__main__":
    main()
