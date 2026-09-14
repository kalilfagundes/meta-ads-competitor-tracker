"""Collection orchestration (async), for either Collection source.

  - the Pages to collect come from the database (`pages.is_tracked`);
  - each Page is read in its own country, or the default country from `app_settings`;
  - Self-hosted: the async client of `meta-ads-collector` reads one Page at a time,
    with a ProxyPool (proxies from the db);
  - Apify: the Pages go in Batches (up to 5 Pages of one country, as Ad Library
    URLs, which is what the Actor takes) to runs of the Apify Actor, several at once
    (`app_settings.apify_max_parallel_runs`); a failed Actor run is retried once,
    and what Apify charged is summed into the run. Each Actor run is recorded as
    it starts, so a run whose worker stopped can be resumed by the next worker
    (`resume=`): it follows the Actor runs that were going on Apify, stores the
    ads of those that finished, and starts only the Batches never started;
  - either way each ad becomes a `NormalizedAd` and is stored by `persist`.

While a run is in flight it writes its progress (pages done, ads so far, current
Page or Batch, errors) plus a heartbeat to `collection_runs`, and logs a progress
line every few ads — so both the Settings UI and `docker compose logs worker`
show that it is moving. The run is left open for the scheduler, which runs the
post-collection steps and then finishes it.

Images and videos are not fetched here: the scheduler's first post-collection
step (`persist.mirror_pending_media`) mirrors them, several at a time.

`make_collector` and `make_apify` are injectable for tests (no network).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import apify, db
from . import persist
from .normalize import NormalizedAd, from_apify_item, from_library_ad
from .persist import RunProgress

logger = logging.getLogger(__name__)

_RATE_LIMIT_DELAY = 3.0
_JITTER = 1.0
_TIMEOUT = 20
_PROGRESS_LOG_EVERY = 10  # one progress log line every N ads

BATCH_SIZE = 5                  # Ad Library URLs the Actor takes in one run
APIFY_POLL_SECONDS = 10         # how often a running Actor run is checked
APIFY_MAX_RUN_SECONDS = 3 * 3600  # an Actor run still going after this is aborted
APIFY_ATTEMPTS = 2              # a failed Actor run is tried once more


def _default_make_collector(proxy):
    # The package does NOT export AsyncMetaAdsCollector from __init__ (only the sync one);
    # import it from the submodule.
    from meta_ads_collector.async_collector import AsyncMetaAdsCollector

    return AsyncMetaAdsCollector(
        proxy=proxy, rate_limit_delay=_RATE_LIMIT_DELAY, jitter=_JITTER, timeout=_TIMEOUT
    )


def _default_make_apify(token: str):
    return apify.ApifyRuns(token)


def default_download_session():
    """The HTTP session media is downloaded with (see persist.mirror_pending_media)."""
    from curl_cffi import requests as curl

    return curl.Session(impersonate="chrome")


def _fmt_secs(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


# ── Shared: storing the ads of a Page ───────────────────────────────────────

@dataclass
class _PageTally:
    """What one Page gave in this run."""
    page: dict
    seen: int = 0
    new: int = 0
    page_name: str | None = None
    profile_done: bool = False


@dataclass
class _Run:
    conn: object
    run_id: int
    now: datetime
    progress: RunProgress
    companies_seen: set[int] = field(default_factory=set)
    # Apify source: what each Actor run has cost and read so far (by Actor run id)
    actor_costs: dict[str, float] = field(default_factory=dict)
    actor_items: dict[str, int] = field(default_factory=dict)

    def beat(self) -> None:
        persist.update_run_progress(self.conn, self.run_id, self.progress)

    def store(self, tally: _PageTally, ad: NormalizedAd) -> None:
        """Store one ad of `tally`'s Page and count it (once per run: a resumed run
        may store a Batch again). Its position among the Page's ads is
        `sort_index` (the results are sorted by impressions)."""
        pg = tally.page
        stored = persist.upsert_ad(
            self.conn, pg["company_id"], pg["id"], self.run_id, ad, self.now,
            sort_index=tally.seen,
        )
        tally.seen += 1
        if stored.counted:
            self.progress.total += 1
        if stored.is_new:
            tally.new += 1
            self.progress.new += 1
        if tally.page_name is None and ad.page.name:
            tally.page_name = ad.page.name
        if not tally.profile_done:  # once per Page per run
            persist.update_page_profile(self.conn, pg["id"], ad.page)
            tally.profile_done = True

    def page_done(self, tally: _PageTally, complete: bool) -> None:
        """After a Page: only when it was read in full are the ads that didn't show
        up gone. Its name follows Facebook's."""
        pg = tally.page
        if complete:
            gone = persist.mark_disappeared_ads(self.conn, pg["id"], self.run_id)
            if gone:
                logger.info("run %s: page %s: %d ad(s) disappeared", self.run_id, pg["page_id"], gone)
            if tally.page_name:
                db.update_page_name(pg["id"], tally.page_name)
        if tally.seen:
            self.companies_seen.add(pg["company_id"])
        self.progress.companies = len(self.companies_seen)

    def fail(self, message: str) -> None:
        self.progress.errors += 1
        self.progress.last_error = message
        try:
            self.conn.rollback()  # a DB error leaves the transaction aborted
        except Exception:
            pass


# ── Self-hosted ─────────────────────────────────────────────────────────────

async def _collect_self_hosted(run: _Run, pages: list[dict], default_country: str,
                               proxies: list[str], make_collector) -> None:
    collector = make_collector(proxies or None)  # list[str] -> internal ProxyPool; None -> no proxy
    async with collector:
        for n, pg in enumerate(pages, start=1):
            label = pg.get("page_name") or pg["page_id"]
            country = (pg.get("collect_country") or default_country).upper()
            where = f"page {n}/{len(pages)} {pg['page_id']} ({label}, {country})"
            tally = _PageTally(pg)
            ok = False
            t0 = time.monotonic()
            logger.info("run %s: %s: fetching ads", run.run_id, where)
            run.progress.current_step, run.progress.current_page_ads = label, 0
            run.beat()
            try:
                async for ad in collector.search(
                    country=country, ad_type="ALL", status="ACTIVE",
                    search_type="PAGE", page_ids=[pg["page_id"]],
                    sort_by="SORT_BY_TOTAL_IMPRESSIONS",
                    page_size=20,  # no max_results: every active ad of the Page
                ):
                    run.store(tally, from_library_ad(ad))
                    run.progress.current_page_ads = tally.seen
                    run.beat()
                    if tally.seen % _PROGRESS_LOG_EVERY == 0:
                        logger.info("run %s: %s: %d ads so far (%d new)",
                                    run.run_id, where, tally.seen, tally.new)
                ok = True
            except Exception as exc:
                run.fail(f"page {pg['page_id']}: {exc}")
                logger.error("run %s: %s failed after %d ad(s): %s", run.run_id, where, tally.seen, exc)

            run.page_done(tally, ok)
            run.progress.pages_done = n
            run.progress.current_step, run.progress.current_page_ads = None, 0
            logger.info("run %s: %s: done, %d ads (%d new) in %s",
                        run.run_id, where, tally.seen, tally.new, _fmt_secs(time.monotonic() - t0))
            run.beat()


# ── Apify ───────────────────────────────────────────────────────────────────

def make_batches(pages: list[dict], default_country: str) -> list[tuple[str, list[dict]]]:
    """Pure (testable): the Pages grouped by the country they're read in, in
    chunks of at most BATCH_SIZE, since one Actor run reads one country."""
    by_country: dict[str, list[dict]] = {}
    for pg in pages:
        by_country.setdefault((pg.get("collect_country") or default_country).upper(), []).append(pg)
    return [(country, group[i:i + BATCH_SIZE])
            for country, group in by_country.items()
            for i in range(0, len(group), BATCH_SIZE)]


def ad_library_url(country: str, page_id: str) -> str:
    """The Ad Library search this Page is read with: its active ads in `country`."""
    return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country={country}&view_all_page_id={page_id}")


def actor_input(country: str, pages: list[dict]) -> dict:
    """What one Actor run is asked for: every active ad of these Pages in this
    country, sorted by impressions (as the Self-hosted source reads them). The
    Actor takes one search per URL and runs them in parallel."""
    return {
        "urls": [{"url": ad_library_url(country, pg["page_id"])} for pg in pages],
        "maxItems": 0,             # no cap
        "activeStatus": "active",
        "sortBy": "impressions",
        "fetchDetails": False,
        "maxConcurrency": min(len(pages), apify.MAX_PARALLEL_RUNS),
    }


def _track_actor(run: _Run, actor_run: dict, items: int | None = None) -> None:
    """Record an Actor run's cost so far (Apify charges as it goes) and, while
    Actor runs are working, how many ads they have read: the Settings UI shows
    both from the Collection run's progress."""
    run.actor_costs[actor_run["id"]] = apify.run_cost_usd(actor_run)
    run.progress.cost_usd = sum(run.actor_costs.values())
    if items is not None:
        run.actor_items[actor_run["id"]] = items
        run.progress.current_step = f"Reading ads on Apify ({sum(run.actor_items.values())})"
        run.progress.current_page_ads = 0


async def _items_so_far(runs, actor_run: dict) -> int | None:
    try:
        return await runs.dataset_item_count(actor_run["defaultDatasetId"])
    except Exception:
        return None  # only for display


def refusal(actor_run: dict) -> str:
    """Why the Actor refused this input, if it did. It refuses an input it can't
    run (more searches than it takes, no residential proxy) by finishing as
    SUCCEEDED with an empty dataset and saying so in its status message; the same
    input would be refused again."""
    message = (actor_run.get("statusMessage") or "").strip()
    return message if message.lower().startswith("run refused") else ""


def _time_left(actor_run: dict) -> float:
    """How much longer an Actor run may go on: APIFY_MAX_RUN_SECONDS from its start
    (one followed again after a restart has been going for a while)."""
    try:
        started = datetime.fromisoformat(actor_run["startedAt"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return APIFY_MAX_RUN_SECONDS
    return max(0.0, APIFY_MAX_RUN_SECONDS - (datetime.now(timezone.utc) - started).total_seconds())


async def _run_actor(runs, run_input: dict, run: _Run, label: str, *,
                     follow: str | None = None, on_start=None) -> tuple[dict | None, str, bool]:
    """One Actor run, followed to its end: started now with `run_input` (and handed
    to `on_start`), or, with `follow`, the one of that id a stopped worker started.
    Returns (the Actor run as last seen, None if there is none; why it didn't
    succeed, '' when it did; whether running it again could work). Its cost and the
    ads it has read are kept up to date in the run's progress on every check."""
    actor_run = None
    try:
        if follow:
            actor_run = await runs.get_run(follow)
        else:
            actor_run = await runs.start_run(run_input)
            if on_start:
                on_start(actor_run)
        deadline = time.monotonic() + _time_left(actor_run)
        while actor_run.get("status") not in apify.RUN_DONE:
            if time.monotonic() > deadline:
                await runs.abort_run(actor_run["id"])
                actor_run = await runs.get_run(actor_run["id"])
                break
            _track_actor(run, actor_run, await _items_so_far(runs, actor_run))
            run.beat()  # the Collection run stays alive while the Actor works
            await asyncio.sleep(APIFY_POLL_SECONDS)
            actor_run = await runs.get_run(actor_run["id"])
    except apify.ApifyError as exc:
        why = f"{exc.reason}: {exc}" if str(exc) != exc.reason else exc.reason
        return actor_run, why, exc.reason != "unauthorized"
    except Exception as exc:
        return actor_run, str(exc), True
    run.actor_items.pop(actor_run.get("id"), None)  # done reading
    _track_actor(run, actor_run)
    run.beat()
    status = actor_run.get("status")
    logger.info("run %s: %s: Actor run %s %s (US$%.4f so far)", run.run_id, label,
                actor_run.get("id"), status, run.progress.cost_usd or 0)
    if status == "SUCCEEDED":
        why = refusal(actor_run)
        return actor_run, why, not why
    return actor_run, f"Actor run {actor_run.get('id')} {status}", True


async def _read_everything(runs, run: _Run, actor_run: dict, label: str) -> bool:
    """Did the Actor read the Batch in full? It stops early on its own (its own
    time limit, or pages that stopped giving new ads) and says so in its run
    summary. A Batch read in part keeps its Pages' other ads as they are: they
    weren't seen, which doesn't mean they stopped running."""
    try:
        summary = await runs.run_summary(actor_run["defaultKeyValueStoreId"]) or {}
    except Exception:
        return True  # no summary to read: take the run at its word
    stopped = summary.get("stoppedEarlyBecause")
    if not stopped:
        return True
    logger.warning("run %s: %s: the Actor stopped early (%s); ads not seen are left as they are",
                   run.run_id, label, stopped)
    return False


async def _settle_cost(runs, run: _Run, actor_run: dict) -> None:
    """Read a finished Actor run's cost again once its results are stored: Apify
    keeps updating the platform usage for a few seconds after the run ends."""
    try:
        _track_actor(run, await runs.get_run(actor_run["id"]))
    except Exception:
        pass  # the figure from when it finished is close enough


async def _collect_batch(runs, run: _Run, country: str, pages: list[dict],
                         slots: asyncio.Semaphore, store_lock: asyncio.Lock, token: str,
                         attached: dict | None = None) -> None:
    """One Batch: its Actor run, then its ads. `attached`: the Actor run a stopped
    worker started for it (a resumed run), followed again instead of a new one."""
    ids = ", ".join(pg["page_id"] for pg in pages)
    where = f"batch [{ids}] ({country})"
    run_input = actor_input(country, pages)

    def recorder(attempt: int):
        def record(actor_run: dict) -> None:
            try:
                persist.record_actor_run(run.conn, run.run_id, actor_run, country,
                                         [pg["id"] for pg in pages], attempt)
            except Exception as exc:  # it goes on; only a resume wouldn't find it
                logger.warning("run %s: %s: could not record Actor run %s: %s",
                               run.run_id, where, actor_run.get("id"), exc)
        return record

    async with slots:
        actor_run, why, retry_ok = None, "", True
        attempt = attached["attempt"] - 1 if attached else 0
        while attempt < APIFY_ATTEMPTS and retry_ok:
            attempt += 1
            follow = attached["id"] if attached and attempt == attached["attempt"] else None
            if follow:
                logger.info("run %s: %s: following Actor run %s again", run.run_id, where, follow)
            else:
                logger.info("run %s: %s: starting Actor run (attempt %d)", run.run_id, where, attempt)
            actor_run, why, retry_ok = await _run_actor(runs, run_input, run, where,
                                                        follow=follow, on_start=recorder(attempt))
            if not why:
                break
            logger.warning("run %s: %s: %s", run.run_id, where, apify.redact(why, token))

    async with store_lock:  # one Batch at a time writes (one connection)
        tallies = {pg["page_id"]: _PageTally(pg) for pg in pages}
        ok = actor_run is not None and not why
        complete = False
        if ok:
            total = await _items_so_far(runs, actor_run)
            run.progress.current_step = "Saving ads" + (f" (0/{total})" if total else "")
            run.progress.current_page_ads = 0
            run.beat()
            batch_seen = 0
            try:
                async for item in runs.iter_items(actor_run["defaultDatasetId"]):
                    tally = tallies.get(str(item.get("page_id") or ""))
                    if tally is None:  # not a Page of this Batch
                        continue
                    run.store(tally, from_apify_item(item))
                    batch_seen += 1
                    if total:
                        run.progress.current_step = f"Saving ads ({batch_seen}/{total})"
                    run.beat()
            except Exception as exc:
                ok, why = False, f"reading results: {exc}"
            complete = ok and await _read_everything(runs, run, actor_run, where)
            await _settle_cost(runs, run, actor_run)
        if not ok:
            # every Page of the Batch failed: count each, and don't mark any ad gone
            for pg in pages:
                run.fail(apify.redact(f"page {pg['page_id']}: {why}", token))
        for tally in tallies.values():
            run.page_done(tally, complete)
        run.progress.pages_done += len(pages)
        run.progress.current_step, run.progress.current_page_ads = None, 0
        logger.info("run %s: %s: done, %d ads (%d new)%s", run.run_id, where,
                    sum(t.seen for t in tallies.values()), sum(t.new for t in tallies.values()),
                    "" if ok else " — failed")
        run.beat()
        # a resume leaves this Batch be from now on (its latest Actor run tells)
        last = (actor_run or attached or {}).get("id")
        if last:
            try:
                persist.conclude_actor_run(run.conn, last, "stored" if ok else "failed")
            except Exception as exc:
                logger.warning("run %s: %s: could not record the Batch as done: %s", run.run_id, where, exc)


async def _collect_apify(run: _Run, plan: list[tuple[str, list[dict], dict | None]],
                         settings: dict, make_apify, earlier: list[dict] = ()) -> None:
    """The Batches of `plan` (country, Pages, and the Actor run to follow again or
    None), several at a time. `earlier`: a resumed run's Actor runs so far, whose
    cost counts too."""
    token = settings.get("apify_token")
    run.progress.cost_usd = 0.0
    if not token:
        run.fail("Apify: no API token saved (Settings → Where ads are read from)")
        return
    parallel = max(1, min(apify.MAX_PARALLEL_RUNS,
                          int(settings.get("apify_max_parallel_runs") or apify.DEFAULT_PARALLEL_RUNS)))
    logger.info("run %s: Apify: %d batch(es), up to %d Actor run(s) at once",
                run.run_id, len(plan), parallel)
    runs = make_apify(token)
    slots, store_lock = asyncio.Semaphore(parallel), asyncio.Lock()
    try:
        for actor_run in earlier:
            try:
                _track_actor(run, await runs.get_run(actor_run["id"]))
            except Exception:
                pass  # its cost is left out
        await asyncio.gather(*(
            _collect_batch(runs, run, country, group, slots, store_lock, token, attached)
            for country, group, attached in plan
        ))
    finally:
        await runs.aclose()


def _resume(conn, run_id: int, tracked: list[dict], default_country: str):
    """A run through Apify a stopped worker left open, picked up where it was. Of
    each Batch it started, the latest Actor run is followed again, unless the Batch
    was dealt with (stored, or failed and counted); the tracked Pages it never
    started get Batches of their own. Its counters carry on from the ones it saved.
    Returns the run, the plan for `_collect_apify` and its Actor runs so far."""
    earlier = persist.actor_runs_of(conn, run_id)
    latest = {}
    for actor_run in earlier:  # oldest first: a retry replaces the attempt before it
        latest[(actor_run["country"], tuple(sorted(actor_run["page_ids"])))] = actor_run
    started = {pk for actor_run in earlier for pk in actor_run["page_ids"]}
    by_id = {pg["id"]: pg for pg in persist.pages_by_id(conn, started)}
    plan, done = [], 0
    for (country, pks), actor_run in latest.items():
        batch = [by_id[pk] for pk in pks if pk in by_id]
        if actor_run["outcome"] or not batch:
            done += len(pks)
        else:
            plan.append((country, batch, actor_run))
    fresh = [pg for pg in tracked if pg["id"] not in started]
    plan += [(country, group, None) for country, group in make_batches(fresh, default_country)]
    row = persist.resume_collection_run(conn, run_id, pages_total=len(started) + len(fresh))
    run = _Run(conn, run_id, row["started_at"], RunProgress(
        total=row["total_ads_collected"] or 0, new=row["new_ads_discovered"] or 0,
        pages_done=done, errors=row["errors"] or 0, last_error=row["last_error"]))
    run.companies_seen = persist.companies_seen_in(conn, run_id)
    run.progress.companies = len(run.companies_seen)
    return run, plan, earlier


# ── The run ─────────────────────────────────────────────────────────────────

async def _collect_run(conn, *, make_collector=_default_make_collector, make_apify=_default_make_apify,
                       trigger: str = "manual", resume: int | None = None) -> tuple[int, RunProgress]:
    settings = db.get_app_settings() or {}
    source = "apify" if resume is not None else settings.get("collect_source") or "self_hosted"
    default_country = (settings.get("collect_country") or "US").upper()
    pages = db.list_tracked_pages()
    proxies = db.list_enabled_proxies() if source == "self_hosted" else []

    if resume is None:
        run_id = persist.start_collection_run(conn, trigger=trigger, pages_total=len(pages))
        logger.info("run %s (%s): %d page(s), source=%s, default country=%s, proxies=%d",
                    run_id, trigger, len(pages), source, default_country, len(proxies))
        run = _Run(conn, run_id, datetime.now(timezone.utc), RunProgress())
        plan = [(country, group, None) for country, group in make_batches(pages, default_country)]
        earlier = []
    else:
        run, plan, earlier = _resume(conn, resume, pages, default_country)
        run_id = run.run_id
        logger.info("run %s: resumed: %d Batch(es) to follow or start, %d page(s) already done",
                    run_id, len(plan), run.progress.pages_done)

    # From here on nothing may raise: the run is open, and the caller needs its id
    # back to finish it.
    try:
        if source == "apify":
            await _collect_apify(run, plan, settings, make_apify, earlier)
        else:
            await _collect_self_hosted(run, pages, default_country, proxies, make_collector)
    except Exception as exc:
        token = settings.get("apify_token")
        run.progress.errors += 1
        run.progress.last_error = apify.redact(f"collector: {exc}", token)
        logger.error("run %s: collector failed: %s", run_id, apify.redact(str(exc), token))

    progress = run.progress
    logger.info("run %s: collected %d ads (%d new) across %d page(s), %d error(s)%s",
                run_id, progress.total, progress.new, progress.pages_done, progress.errors,
                f", Apify cost US${progress.cost_usd:.4f}" if progress.cost_usd is not None else "")
    return run_id, progress


def run_collection(*, make_collector=_default_make_collector, make_apify=_default_make_apify,
                   trigger: str = "manual", resume: int | None = None) -> tuple[int, RunProgress]:
    """Collect every tracked Page (sync on the outside; async on the inside), or,
    with `resume`, carry on the run of that id through Apify that a stopped worker
    left open (see `_resume`).

    Returns the run id and its progress. The run is left OPEN: the caller runs the
    post-collection steps and closes it with `persist.finish_collection_run`.
    """
    conn = db.connect()
    try:
        return asyncio.run(
            _collect_run(conn, make_collector=make_collector, make_apify=make_apify,
                         trigger=trigger, resume=resume)
        )
    finally:
        conn.close()
