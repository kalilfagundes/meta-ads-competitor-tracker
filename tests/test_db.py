"""Data-layer tests (need a Postgres — see conftest). Exercise the queries the UI
depends on: the library (cards, filters, pagination, Versions, champions, export
rows), ad detail (carousel vs variants vs inactive), the
landing-page catalog and detail, admin reads, collections, and page relocation.
"""

from __future__ import annotations

import pytest


def _page(**filters):
    from tracker import db

    sort = filters.pop("sort", "dur")
    page = filters.pop("page", 0)
    page_size = filters.pop("page_size", 48)
    return db.get_library_page(db.LibraryFilters(**filters), sort=sort, page=page, page_size=page_size)


def test_library_page_cards(seeded):
    data = _page(status="all")
    assert data["total"] == 4 and data["pages"] == 1
    cards = {c["id"]: c for c in data["cards"]}
    assert [c["id"] for c in data["cards"]] == ["AD_EVERGREEN", "AD_CAROUSEL", "AD_VARIANTS", "AD_CEMETERY"]

    ev = cards["AD_EVERGREEN"]
    assert (ev["media"], ev["days"], ev["rank"], ev["active"]) == ("video", 112, 0, True)
    assert ev["hero"].startswith("/media/ads/t.jpg")               # resolved to the proxy path
    assert (cards["AD_CAROUSEL"]["media"], cards["AD_CAROUSEL"]["formats"]) == ("carousel", 1)
    assert cards["AD_CAROUSEL"]["cards"] == 2
    assert (cards["AD_VARIANTS"]["media"], cards["AD_VARIANTS"]["formats"]) == ("image", 3)
    assert cards["AD_CEMETERY"]["rank"] is None


def test_library_filters(seeded):
    ids = lambda **f: [c["id"] for c in _page(**f)["cards"]]  # noqa: E731
    assert ids() == ["AD_EVERGREEN", "AD_CAROUSEL", "AD_VARIANTS"]          # active by default
    assert ids(status="inactive") == ["AD_CEMETERY"]
    assert ids(status="all", companies=("Contoso Academy",)) == ["AD_CAROUSEL"]
    assert ids(status="all", companies=("Contoso Academy", "Northwind Prep")) == ids(status="all")
    assert ids(media="carousel") == ["AD_CAROUSEL"]
    assert ids(min_days=50) == ["AD_EVERGREEN", "AD_CAROUSEL"]
    assert ids(status="all", q="ESSAY") == ["AD_CEMETERY"]                   # title/body, any case
    assert ids(q="northwind") == ["AD_EVERGREEN", "AD_VARIANTS"]            # company name
    assert ids(sort="rank") == ["AD_CAROUSEL", "AD_EVERGREEN", "AD_VARIANTS"]  # per company


def test_library_pagination(seeded):
    first, second = _page(status="all", page_size=3), _page(status="all", page_size=3, page=1)
    assert (first["pages"], len(first["cards"]), len(second["cards"])) == (2, 3, 1)
    assert _page(status="all", page_size=3, page=99)["page"] == 1           # clamped to the last page


def test_library_folds_versions_into_one_card(seeded, db):
    db.execute("UPDATE ads SET collation_id = 'C1' WHERE ad_archive_id IN ('AD_VARIANTS', 'AD_CEMETERY')")
    db.commit()
    data = _page(status="all")
    assert data["total"] == 3
    card = next(c for c in data["cards"] if c["id"] == "AD_VARIANTS")      # the active member fronts
    assert (card["versions"], card["active"], card["days"]) == (2, True, 45)
    # a filter matches each Version on its own, but the count covers them all
    inactive = _page(status="inactive")["cards"]
    assert [(c["id"], c["versions"]) for c in inactive] == [("AD_CEMETERY", 2)]


def test_champions_share_slots_between_companies(seeded, db):
    from tracker import db as dbmod

    champs = dbmod.get_champions()
    assert [c["id"] for c in champs] == ["AD_EVERGREEN", "AD_CAROUSEL", "AD_VARIANTS"]
    assert [c["id"] for c in dbmod.get_champions(("Contoso Academy",))] == ["AD_CAROUSEL"]
    assert dbmod.get_champions(("Contoso Academy", "Northwind Prep")) == champs
    assert dbmod.get_library_companies() == ["Contoso Academy", "Northwind Prep"]


def test_export_rows_stream_filtered_ads(seeded):
    from tracker import db

    rows = list(db.iter_ads_for_export(db.LibraryFilters(status="all")))
    assert [r["ad_archive_id"] for r in rows] == ["AD_EVERGREEN", "AD_CAROUSEL", "AD_VARIANTS", "AD_CEMETERY"]
    ev = rows[0]
    assert (ev["media"], ev["impression_rank"], ev["platforms"]) == ("video", 1, "facebook;instagram")
    assert ev["landing_page_url"] == "https://northwindprep.example/exam-prep"


def test_ad_detail_carousel(seeded):
    from tracker import db

    ad = db.get_ad_detail("AD_CAROUSEL")
    assert ad["is_carousel"] is True
    assert ad["media"] == "carousel"
    assert len(ad["creatives"]) == 2


def test_ad_detail_placement_variants_not_carousel(seeded):
    from tracker import db

    ad = db.get_ad_detail("AD_VARIANTS")
    assert ad["is_carousel"] is False
    assert ad["media"] == "image"
    assert len(ad["creatives"]) == 1        # collapsed to a single representative image
    assert len(ad["formats"]) == 3          # ...but every size stays reachable


def test_ad_detail_carousel_has_no_formats(seeded):
    from tracker import db

    assert db.get_ad_detail("AD_CAROUSEL")["formats"] == []


def test_ad_detail_versions_by_collation_id(seeded, db):
    from tracker import db as dbmod

    db.execute("UPDATE ads SET collation_id = 'C1' WHERE ad_archive_id IN ('AD_VARIANTS', 'AD_CEMETERY')")
    db.commit()
    ad = dbmod.get_ad_detail("AD_VARIANTS")
    assert [v["ad_archive_id"] for v in ad["versions"]] == ["AD_CEMETERY"]
    assert ad["versions"][0]["thumb_url"].startswith("/media/ads/dead.jpg")
    assert dbmod.get_ad_detail("AD_EVERGREEN")["versions"] == []   # no collation id


def test_ad_detail_inactive_has_no_rank(seeded):
    from tracker import db

    ad = db.get_ad_detail("AD_CEMETERY")
    assert ad["is_active"] is False
    assert ad["sort_index"] is None


def test_ad_detail_destinations(seeded):
    from tracker import db

    # linked to a recorded landing page -> its canonical URL and id
    assert db.get_ad_detail("AD_EVERGREEN")["destinations"] == [{
        "landing_page_id": seeded["lp"],
        "url": "https://northwindprep.example/exam-prep",
        "display_url": "northwindprep.example/exam-prep",
    }]
    # three formats sharing one link -> one destination, not linked to a page yet
    dests = db.get_ad_detail("AD_VARIANTS")["destinations"]
    assert [(d["landing_page_id"], d["url"]) for d in dests] == [
        (None, "https://northwindprep.example/promo")]
    assert db.get_ad_detail("AD_CEMETERY")["destinations"] == []   # no link at all


def test_ad_detail_unknown(seeded):
    from tracker import db

    assert db.get_ad_detail("DOES_NOT_EXIST") is None


def test_catalog_data(seeded):
    from tracker import db

    data = db.get_catalog_data()
    row = next(r for r in data["rows"] if r["url_canonical"].endswith("/exam-prep"))
    assert row["headline"] == "Approval guaranteed"
    assert row["description"] == "Pass the exam in 90 days."
    assert row["display_url"] == "northwindprep.example/exam-prep"
    assert row["ad_count"] == 1
    assert row["hero_image_url"].startswith("/media/")


def test_product_detail(seeded):
    from tracker import db

    data = db.get_product_detail(seeded["lp"])
    assert data["landing_page"]["url_canonical"].endswith("/exam-prep")
    assert "AD_EVERGREEN" in {a["ad_archive_id"] for a in data["ads"]}
    assert db.get_product_detail(999999) is None


def test_landing_title_crawl(seeded, db, monkeypatch):
    from tracker.worker import resolver

    class Resp:
        def __init__(self, html, status=200):
            self.text, self.status_code, self.url = html, status, "https://northwindprep.example/exam-prep"

    lp = {"id": seeded["lp"], "url_canonical": "https://northwindprep.example/exam-prep"}
    html = '<html><head><title>Exam Prep | Northwind</title></head><body>Hello</body></html>'
    monkeypatch.setattr(resolver, "_fetch", lambda url: Resp(html))

    def snapshots():
        return db.execute(
            "SELECT headline FROM page_snapshots WHERE landing_page_id = %s ORDER BY id",
            (seeded["lp"],),
        ).fetchall()

    assert resolver.crawl_landing_page(db, lp) == "ok"
    assert [s["headline"] for s in snapshots()] == ["Approval guaranteed", "Exam Prep | Northwind"]
    resolver.crawl_landing_page(db, lp)              # same content -> no new version
    assert len(snapshots()) == 2

    # same page text, new og:title (meta tags aren't page text): refreshed in place
    retitled = html.replace("<head>", '<head><meta property="og:title" content="Exam Prep">')
    monkeypatch.setattr(resolver, "_fetch", lambda url: Resp(retitled))
    resolver.crawl_landing_page(db, lp)
    assert [s["headline"] for s in snapshots()] == ["Approval guaranteed", "Exam Prep"]

    monkeypatch.setattr(resolver, "_fetch", lambda url: Resp("gone", status=404))
    assert resolver.crawl_landing_page(db, lp) == "fetch_failed"
    assert len(snapshots()) == 2

    # progress is reported per page for the run status
    db.execute("UPDATE landing_pages SET last_resolved_at = NULL")
    db.commit()
    monkeypatch.setattr(resolver, "_fetch", lambda url: Resp(html))
    seen = []
    resolver.crawl_pending(db, on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 1)]


def test_admin_queries(seeded):
    from tracker import db

    cos = db.list_companies_with_pages()
    northwind = next(c for c in cos if c["name"] == "Northwind Prep")
    assert any(p["page_id"] == "111" and p["is_tracked"] for p in northwind["pages"])

    assert [p["url"] for p in db.list_proxies()] == ["proxy.example:8080"]
    assert db.list_enabled_proxies() == ["proxy.example:8080"]
    assert db.get_app_settings()["collect_interval_hours"] == 6
    assert len(db.get_run_status()["runs"]) == 1
    assert {p["page_id"] for p in db.list_tracked_pages()} == {"111"}   # is_tracked only


def test_collections_crud(seeded):
    from tracker import db

    col = db.create_collection("Favs")
    assert db.toggle_collection_item(col["id"], "AD_EVERGREEN") == "added"
    assert db.toggle_collection_item(col["id"], "AD_EVERGREEN") == "removed"
    db.toggle_collection_item(col["id"], "AD_CAROUSEL")

    fav = next(c for c in db.list_collections() if c["name"] == "Favs")
    assert "AD_CAROUSEL" in fav["ad_archive_ids"]

    db.delete_collection(col["id"])
    assert not any(c["name"] == "Favs" for c in db.list_collections())


def test_update_company(seeded):
    from tracker import db

    db.update_company(seeded["companies"]["northwind"], "Northwind", "nw.example")
    co = next(c for c in db.list_companies_with_pages() if c["id"] == seeded["companies"]["northwind"])
    assert (co["name"], co["domain"]) == ("Northwind", "nw.example")


def test_update_page(seeded, db):
    from tracker import db as dbmod

    pk = db.execute("SELECT id FROM pages WHERE page_id = '222'").fetchone()["id"]
    assert dbmod.update_page(pk, "333", "Renamed") is None
    row = db.execute("SELECT page_id, page_name FROM pages WHERE id = %s", (pk,)).fetchone()
    assert (row["page_id"], row["page_name"]) == ("333", "Renamed")
    # a page ID already taken by another page is refused, not a 500
    assert dbmod.update_page(pk, "111", "x") is dbmod.PageEditError.ID_TAKEN
    # once a page has ads its ID is locked (the ads belong to the old ID)
    db.execute("UPDATE ads SET page_ref_id = %s WHERE ad_archive_id = 'AD_CAROUSEL'", (pk,))
    db.commit()
    assert dbmod.update_page(pk, "444", "Renamed") is dbmod.PageEditError.ID_LOCKED
    assert dbmod.update_page(pk, "333", "Renamed again") is None   # renaming is still fine
    assert dbmod.update_page(999999, "1", None) is dbmod.PageEditError.NOT_FOUND


def test_page_id_lock_is_listed(seeded, db):
    from tracker import db as dbmod

    db.execute("UPDATE ads SET page_ref_id = (SELECT id FROM pages WHERE page_id = '111') "
               "WHERE ad_archive_id = 'AD_EVERGREEN'")
    db.commit()
    pages = {p["page_id"]: p for c in dbmod.list_companies_with_pages() for p in c["pages"]}
    assert (pages["111"]["has_ads"], pages["222"]["has_ads"]) == (True, False)


def test_meta_page_name_does_not_override_admin_name(seeded, db):
    from tracker import db as dbmod

    def name(pk):
        return db.execute("SELECT page_name FROM pages WHERE id = %s", (pk,)).fetchone()["page_name"]

    pk = db.execute("SELECT id FROM pages WHERE page_id = '111'").fetchone()["id"]
    # a name that came from Facebook follows renames on Facebook
    dbmod.update_page_name(pk, "Name from Facebook")
    dbmod.update_page_name(pk, "Renamed on Facebook")
    assert name(pk) == "Renamed on Facebook"
    # a name the admin set is kept
    dbmod.update_page(pk, "111", "Admin name")
    dbmod.update_page_name(pk, "Renamed again on Facebook")
    assert name(pk) == "Admin name"
    # admin clears it -> Facebook's name again
    dbmod.update_page(pk, "111", None)
    dbmod.update_page_name(pk, "Name from Facebook")
    assert name(pk) == "Name from Facebook"
    # a name given when adding the page is the admin's too
    new = dbmod.create_page(seeded["companies"]["contoso"], "555", "Given on create")["id"]
    dbmod.update_page_name(new, "Name from Facebook")
    assert name(new) == "Given on create"


def test_run_status_lifecycle(seeded, db):
    from tracker import db as dbmod
    from tracker.worker import persist
    from tracker.worker.persist import RunProgress

    assert dbmod.get_run_status()["state"] == "idle"
    assert dbmod.request_collect_now() is True
    assert dbmod.get_run_status()["state"] == "queued"
    assert dbmod.request_collect_now() is False          # single-flight while queued
    assert dbmod.consume_collect_now() is True
    # between the worker taking the flag and opening the run: still 'queued', still refused
    assert dbmod.get_run_status()["state"] == "queued"
    assert dbmod.request_collect_now() is False

    run_id = persist.start_collection_run(db, trigger="manual", pages_total=2)
    persist.update_run_progress(db, run_id, RunProgress(
        total=7, new=2, pages_done=1, current_step="Page B", current_page_ads=3))
    st = dbmod.get_run_status()
    assert st["state"] == "running"
    assert (st["run"]["id"], st["run"]["total_ads_collected"], st["run"]["current_step"]) == (run_id, 7, "Page B")
    assert dbmod.request_collect_now() is False          # single-flight while running

    persist.finish_collection_run(db, run_id, RunProgress(
        total=9, new=2, pages_done=2, current_step="Fetching landing-page titles",
        companies=1, errors=1, last_error="page 222: boom"))
    st = dbmod.get_run_status()
    assert st["state"] == "idle"
    assert (st["run"]["errors"], st["run"]["last_error"], st["run"]["current_step"]) == (1, "page 222: boom", None)
    assert st["next_run_in_s"] is not None
    assert dbmod.request_collect_now() is True


def test_stalled_and_orphan_runs(seeded, db):
    from tracker import db as dbmod
    from tracker.worker import persist

    db.execute(
        """INSERT INTO collection_runs (started_at, heartbeat_at)
           VALUES (now() - interval '30 minutes', now() - interval '20 minutes')"""
    )
    db.commit()
    assert dbmod.get_run_status()["state"] == "stalled"
    assert persist.close_orphan_runs(db) == 1
    st = dbmod.get_run_status()
    assert st["state"] == "idle" and st["run"]["errors"] == 1

    # a run still sending heartbeats (e.g. another worker's) is left alone
    live = persist.start_collection_run(db, trigger="schedule", pages_total=1)
    assert persist.close_orphan_runs(db) == 0
    st = dbmod.get_run_status()
    assert (st["state"], st["run"]["id"]) == ("running", live)


def test_a_run_left_open_behind_a_newer_one_shows_and_blocks_collect_now(seeded, db):
    from tracker import db as dbmod
    from tracker.worker import persist
    from tracker.worker.persist import RunProgress

    # a worker was killed mid-run (its heartbeat is still fresh); the next one then
    # ran a run of its own to the end
    left_open = persist.start_collection_run(db, trigger="manual", pages_total=2)
    newer = persist.start_collection_run(db, trigger="startup", pages_total=2)
    persist.finish_collection_run(db, newer, RunProgress(total=5, pages_done=2))
    st = dbmod.get_run_status()
    assert (st["state"], st["run"]["id"]) == ("running", left_open)    # never "idle" while refused
    assert dbmod.request_collect_now() is False


def test_the_worker_lock_lets_one_worker_collect_at_a_time(db):
    from tracker.worker.scheduler import WorkerLock

    first, second = WorkerLock(), WorkerLock()
    try:
        assert first.try_take() and first.still_held()
        assert not second.try_take()                  # a second worker waits
        first.release()                               # the first one's process is gone
        assert second.try_take()
    finally:
        first.release()
        second.release()


def test_a_worker_taking_the_lock_closes_runs_a_dead_worker_left_open(seeded, db):
    from tracker import db as dbmod
    from tracker.worker import persist, scheduler

    left_open = persist.start_collection_run(db, trigger="manual", pages_total=3)
    lock = scheduler.WorkerLock()
    try:
        scheduler._take_the_lock(lock)
    finally:
        lock.release()
    st = dbmod.get_run_status()
    assert (st["state"], st["run"]["id"]) == ("idle", left_open)
    assert (st["run"]["errors"], st["run"]["last_error"]) == (1, persist.RUN_INTERRUPTED)
    assert dbmod.request_collect_now() is True


def test_a_stopped_worker_closes_its_run_on_the_way_out(seeded, db, monkeypatch):
    import signal

    from tracker import db as dbmod
    from tracker.worker import persist, scheduler

    exits = []
    monkeypatch.setattr(scheduler.os, "_exit", exits.append)
    run_id = persist.start_collection_run(db, trigger="schedule", pages_total=1)
    lock = scheduler.WorkerLock()
    try:
        # without the lock the run can't be this worker's: it's left alone
        scheduler._stop_handler(lock)(signal.SIGTERM, None)
        assert dbmod.get_run_status()["state"] == "running"
        assert lock.try_take()
        scheduler._stop_handler(lock)(signal.SIGTERM, None)
    finally:
        lock.release()
    assert exits == [0, 0]
    run = dbmod.get_run_status()["run"]
    assert (run["id"], run["last_error"]) == (run_id, persist.RUN_STOPPED)
    assert run["finished_at"] is not None


def test_what_fails_after_collecting_is_an_error_of_the_run(seeded, db, monkeypatch):
    from tracker import db as dbmod
    from tracker.worker import linking, persist, resolver, scheduler
    from tracker.worker.persist import MirrorResult, RunProgress

    def fake_collection(trigger, resume=None):
        with dbmod.connect() as conn:
            return persist.start_collection_run(conn, trigger=trigger, pages_total=1), RunProgress(pages_done=1)

    def broken_linking():
        raise RuntimeError("linking broke")

    monkeypatch.setattr(scheduler, "run_collection", fake_collection)
    monkeypatch.setattr(persist, "mirror_pending_media",
                        lambda conn, **kw: MirrorResult(8, 10, 2, "AccessDenied"))
    monkeypatch.setattr(linking, "run_linking", lambda: 0)
    monkeypatch.setattr(resolver, "run_resolve", lambda on_progress=None: None)
    scheduler._run_once("manual")
    run = dbmod.get_run_status()["run"]
    assert (run["errors"], run["last_error"]) == (
        1, "Saving images and videos: storage refused 2 of 10 file(s): AccessDenied")

    monkeypatch.setattr(linking, "run_linking", broken_linking)
    scheduler._run_once("manual")
    run = dbmod.get_run_status()["run"]
    assert run["finished_at"] is not None and run["errors"] == 2
    assert run["last_error"] == "Linking ads to landing pages: linking broke"


def test_a_collection_that_fails_before_it_starts_is_still_reported(seeded, db, monkeypatch):
    from tracker import db as dbmod
    from tracker.worker import scheduler

    def broken_collection(trigger, resume=None):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(scheduler, "run_collection", broken_collection)
    monkeypatch.setattr(scheduler, "_post_collection_steps", lambda step, fail: None)
    scheduler._run_once("manual")
    run = dbmod.get_run_status()["run"]
    assert (run["trigger"], run["errors"], run["last_error"]) == ("manual", 1, "collector: database hiccup")
    assert run["finished_at"] is not None


def test_scheduler_run_stays_open_through_post_collection_steps(seeded, db, monkeypatch):
    from tracker import db as dbmod
    from tracker.worker import persist, scheduler
    from tracker.worker.persist import RunProgress

    def fake_collection(trigger, resume=None):
        with dbmod.connect() as conn:
            run_id = persist.start_collection_run(conn, trigger=trigger, pages_total=1)
        return run_id, RunProgress(total=3, new=1, pages_done=1)

    seen = []

    def fake_steps(step, fail):
        step("Linking ads to landing pages")
        st = dbmod.get_run_status()
        seen.append((st["state"], st["run"]["current_step"]))

    monkeypatch.setattr(scheduler, "run_collection", fake_collection)
    monkeypatch.setattr(scheduler, "_post_collection_steps", fake_steps)
    scheduler._run_once("manual")

    assert seen == [("running", "Linking ads to landing pages")]
    st = dbmod.get_run_status()
    assert st["state"] == "idle"
    assert (st["run"]["trigger"], st["run"]["total_ads_collected"], st["run"]["errors"]) == ("manual", 3, 0)


def test_scheduler_closes_run_when_post_collection_fails(seeded, db, monkeypatch):
    from tracker import db as dbmod
    from tracker.worker import persist, scheduler
    from tracker.worker.persist import RunProgress

    def fake_collection(trigger, resume=None):
        with dbmod.connect() as conn:
            run_id = persist.start_collection_run(conn, trigger=trigger, pages_total=1)
        return run_id, RunProgress(total=2, pages_done=1)

    def broken_steps(step, fail):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(scheduler, "run_collection", fake_collection)
    monkeypatch.setattr(scheduler, "_post_collection_steps", broken_steps)
    scheduler._run_once("schedule")

    st = dbmod.get_run_status()
    assert st["state"] == "idle"
    assert st["run"]["errors"] == 1 and "connection lost" in st["run"]["last_error"]


def test_collection_returns_run_even_if_setup_fails(seeded, db, monkeypatch):
    from tracker.worker import collector

    def no_collector(proxy):
        raise ImportError("meta_ads_collector missing")

    run_id, progress = collector.run_collection(make_collector=no_collector, trigger="manual")
    row = db.execute("SELECT finished_at FROM collection_runs WHERE id = %s", (run_id,)).fetchone()
    assert row["finished_at"] is None          # left open for the scheduler to finish
    assert progress.errors == 1 and "meta_ads_collector missing" in progress.last_error


def test_collection_reads_each_page_in_its_country(seeded, db):
    from tracker.worker import collector

    db.execute("UPDATE app_settings SET collect_country = 'BR'")
    db.execute("UPDATE pages SET is_tracked = TRUE")
    db.execute("UPDATE pages SET collect_country = 'US' WHERE page_id = '222'")
    db.commit()
    searches = []

    class FakeCollector:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search(self, **kw):
            searches.append((kw["page_ids"][0], kw["country"]))
            return
            yield


    collector.run_collection(make_collector=lambda proxy: FakeCollector(), trigger="manual")
    assert sorted(searches) == [("111", "BR"), ("222", "US")]          # default, then its own


def test_create_page_relocates_on_conflict(seeded):
    from tracker import db

    # page 111 is under Northwind Prep; re-adding it under Contoso Academy should move it.
    db.create_page(seeded["companies"]["contoso"], "111", "Moved")
    cos = db.list_companies_with_pages()
    owner = next(c for c in cos if any(p["page_id"] == "111" for p in c["pages"]))
    assert owner["name"] == "Contoso Academy"


def test_upsert_ad_stores_display_format_and_captions(seeded, db):
    from datetime import datetime, timezone

    from tests.test_unit import _meta_records
    from tracker.worker import persist
    from tracker.worker.normalize import from_apify_item

    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "DPA")
    page = db.execute("SELECT id, company_id FROM pages WHERE page_id = '111'").fetchone()
    run_id = persist.start_collection_run(db, trigger="manual", pages_total=1)
    ad_id, is_new, counted = persist.upsert_ad(db, page["company_id"], page["id"], run_id,
                                               from_apify_item(record), datetime.now(timezone.utc))
    assert is_new and counted
    # storing it again in the same run (a resumed run does) records one appearance
    again = persist.upsert_ad(db, page["company_id"], page["id"], run_id,
                              from_apify_item(record), datetime.now(timezone.utc))
    assert (again.ad_id, again.is_new, again.counted) == (ad_id, False, False)
    seen = db.execute("SELECT count(*) AS n FROM ad_appearances WHERE ad_id = %s", (ad_id,)).fetchone()
    assert seen["n"] == 1
    row = db.execute("SELECT display_format FROM ads WHERE id = %s", (ad_id,)).fetchone()
    assert row["display_format"] == "DPA"
    caps = db.execute("SELECT caption FROM creatives WHERE ad_id = %s ORDER BY creative_index",
                      (ad_id,)).fetchall()
    assert len(caps) == 6 and all(c["caption"] for c in caps)


def test_self_hosted_collection_stores_the_normalized_ad(seeded, db):
    from tests.test_unit import _as_library_ad, _meta_records
    from tracker.worker import collector

    db.execute("UPDATE pages SET is_tracked = (page_id = '111')")
    db.commit()
    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "CAROUSEL")

    class FakeCollector:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search(self, **kw):
            yield _as_library_ad(record)


    _, progress = collector.run_collection(make_collector=lambda proxy: FakeCollector(), trigger="manual")
    assert (progress.total, progress.errors) == (1, 0)
    row = db.execute("SELECT id, display_format FROM ads WHERE ad_archive_id = %s",
                     (record["ad_archive_id"],)).fetchone()
    assert row["display_format"] == "CAROUSEL"
    page = db.execute("SELECT like_count, categories, profile_picture_url FROM pages WHERE page_id = '111'").fetchone()
    snap = record["snapshot"]
    assert page["like_count"] == snap["page_like_count"] and page["categories"] == snap["page_categories"]
    assert page["profile_picture_url"] == snap["page_profile_picture_url"]
    n = db.execute("SELECT count(*) AS n FROM creatives WHERE ad_id = %s", (row["id"],)).fetchone()["n"]
    assert n == len(record["snapshot"]["cards"])


def _collect_dpa(db, monkeypatch, cards_order=None):
    """Upsert the fixture's DPA ad (optionally with its cards reordered), with
    mirroring stubbed. Returns (ad_db_id, mirrored urls)."""
    import copy
    from datetime import datetime, timezone

    from tests.test_unit import _meta_records
    from tracker.worker import persist
    from tracker.worker.normalize import from_apify_item

    record = copy.deepcopy(next(r for r in _meta_records() if r["snapshot"]["display_format"] == "DPA"))
    if cards_order:
        record["snapshot"]["cards"] = [record["snapshot"]["cards"][i] for i in cards_order]
    mirrored = []

    def fake_mirror(session, url, key, **kw):
        mirrored.append(url)
        return key + ".webp"

    monkeypatch.setattr(persist, "mirror_media", fake_mirror)
    page = db.execute("SELECT id, company_id FROM pages WHERE page_id = '111'").fetchone()
    run_id = persist.start_collection_run(db, trigger="manual", pages_total=1)
    ad_id = persist.upsert_ad(db, page["company_id"], page["id"], run_id,
                              from_apify_item(record), datetime.now(timezone.utc)).ad_id
    persist.mirror_pending_media(db, make_session=object)
    return ad_id, mirrored, record


def _cards(db, ad_id):
    return db.execute(
        """SELECT card_key, creative_index, image_url, media_image_key FROM creatives
           WHERE ad_id = %s ORDER BY creative_index""", (ad_id,)).fetchall()


def test_reordered_cards_keep_their_mirrored_media(seeded, db, monkeypatch):
    ad_id, mirrored, _ = _collect_dpa(db, monkeypatch)
    assert len(mirrored) == 6
    before = {c["card_key"]: c["media_image_key"] for c in _cards(db, ad_id)}
    assert len(set(before.values())) == 6

    ad_id, mirrored, record = _collect_dpa(db, monkeypatch, cards_order=[5, 3, 1, 0, 2, 4])
    assert mirrored == []                                  # nothing downloaded again
    after = _cards(db, ad_id)
    assert {c["card_key"]: c["media_image_key"] for c in after} == before
    # display order follows Meta's new order
    assert [c["image_url"] for c in after] == [
        card["original_image_url"] for card in record["snapshot"]["cards"]]


def test_active_ad_has_its_end_date_cleared(seeded, db, monkeypatch):
    ad_id, _, _ = _collect_dpa(db, monkeypatch)
    # an end date stored for an ad that is active (again)
    db.execute("UPDATE ads SET delivery_stop_time = now() - interval '2 days' WHERE id = %s", (ad_id,))
    db.commit()
    ad_id, _, _ = _collect_dpa(db, monkeypatch)
    row = db.execute("SELECT delivery_stop_time, duration_days, delivery_start_time FROM ads WHERE id = %s",
                     (ad_id,)).fetchone()
    assert row["delivery_stop_time"] is None
    from datetime import datetime, timezone
    assert row["duration_days"] == (datetime.now(timezone.utc) - row["delivery_start_time"]).days


def test_collection_reads_every_ad_of_a_page(seeded, db):
    from tracker.worker import collector

    db.execute("UPDATE pages SET is_tracked = (page_id = '111')")
    db.commit()
    calls = []

    class FakeCollector:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def search(self, **kw):
            calls.append(kw)
            return
            yield


    collector.run_collection(make_collector=lambda proxy: FakeCollector(), trigger="manual")
    assert len(calls) == 1 and calls[0].get("max_results") is None


def _update_profile(db, monkeypatch, picture_url, likes=100):
    from tracker.worker import persist
    from tracker.worker.normalize import NormalizedPage

    mirrored = []

    def fake_mirror(session, url, key, **kw):
        mirrored.append(url)
        return key + ".webp"

    monkeypatch.setattr(persist, "mirror_media", fake_mirror)
    pk = db.execute("SELECT id FROM pages WHERE page_id = '111'").fetchone()["id"]
    persist.update_page_profile(db, pk, NormalizedPage(
        id="111", name="Northwind", profile_picture_url=picture_url,
        profile_uri="https://www.facebook.com/northwind/", like_count=likes,
        categories=["Sportswear"]))
    persist.mirror_pending_media(db, make_session=object)
    row = db.execute("""SELECT profile_picture_key, like_count, categories, profile_uri
                        FROM pages WHERE id = %s""", (pk,)).fetchone()
    return row, mirrored


def test_page_picture_is_mirrored_once_until_it_changes(seeded, db, monkeypatch):
    pic = "https://scontent.xx.fbcdn.net/v/t39.35426-6/111_222_n.jpg?stp=dst-jpg_s60x60&oe={}"
    row, mirrored = _update_profile(db, monkeypatch, pic.format("6AB1B1C8"))
    assert mirrored and row["profile_picture_key"] == "pages/111/111_222_n.webp"
    assert (row["like_count"], row["categories"]) == (100, ["Sportswear"])
    assert row["profile_uri"] == "https://www.facebook.com/northwind/"
    # the same file, from a later read (new expiry): not downloaded again
    row, mirrored = _update_profile(db, monkeypatch, pic.format("6AB2FFFF"), likes=150)
    assert mirrored == [] and row["like_count"] == 150
    # a new picture: a new file, a new key
    new = "https://scontent.xx.fbcdn.net/v/t39.35426-6/999_888_n.jpg?oe=6AB2FFFF"
    row, mirrored = _update_profile(db, monkeypatch, new)
    assert mirrored == [new] and row["profile_picture_key"] == "pages/111/999_888_n.webp"


def test_admin_pages_carry_the_page_picture(seeded, db, monkeypatch):
    from tracker import db as dbmod

    _update_profile(db, monkeypatch, "https://scontent.xx.fbcdn.net/v/t1/111_222_n.jpg?oe=1")
    pages = {p["page_id"]: p for c in dbmod.list_companies_with_pages() for p in c["pages"]}
    assert pages["111"]["avatar_url"].startswith("/media/pages/111/111_222_n.webp?fallback=")
    assert pages["222"]["avatar_url"] is None


# ── Apify source ────────────────────────────────────────────────────────────

class FakeApify:
    """Stands in for `apify.ApifyRuns`: each started run finishes after one poll
    with the items given for its Page IDs; runs listed in `fail` fail."""

    def __init__(self, items_by_page, fail=0, cost=0.01, refuse=False, stopped_early=None):
        self.items_by_page, self.fail, self.cost = items_by_page, fail, cost
        self.refuse, self.stopped_early = refuse, stopped_early
        self.inputs, self.running, self.max_running = [], 0, 0
        self._runs = {}

    async def start_run(self, run_input):
        import asyncio

        self.inputs.append(run_input)
        run_id = f"r{len(self.inputs)}"
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        await asyncio.sleep(0)
        failed = self.fail > 0
        self.fail -= 1 if failed else 0
        pages = [u["url"].rsplit("view_all_page_id=", 1)[1] for u in run_input["urls"]]
        self._runs[run_id] = {"id": run_id, "status": "RUNNING", "defaultDatasetId": run_id,
                              "defaultKeyValueStoreId": run_id,
                              "_final": "FAILED" if failed else "SUCCEEDED",
                              "_pages": [] if self.refuse else pages, "usageTotalUsd": self.cost,
                              "statusMessage": "Run refused. This Actor takes fewer searches."
                              if self.refuse else "Finished"}
        return dict(self._runs[run_id])

    def adopt(self, run_id, pages, final="SUCCEEDED", running=True):
        """An Actor run a stopped worker started for `pages`: still going (it ends
        at the next check, as `final`) or already over."""
        self._runs[run_id] = {"id": run_id, "status": "RUNNING" if running else final,
                              "defaultDatasetId": run_id, "defaultKeyValueStoreId": run_id,
                              "_final": final, "_pages": list(pages), "usageTotalUsd": self.cost,
                              "statusMessage": "Finished"}
        self.running += running

    async def get_run(self, run_id):
        import asyncio

        await asyncio.sleep(0.01)
        run = self._runs[run_id]
        if run["status"] == "RUNNING":
            run["status"] = run["_final"]
            self.running -= 1
        return dict(run)

    async def abort_run(self, run_id):
        pass

    async def dataset_item_count(self, dataset_id):
        return sum(len(self.items_by_page.get(p, [])) for p in self._runs[dataset_id]["_pages"])

    async def run_summary(self, store_id):
        return {"adsScraped": 0, "finished": not self.stopped_early,
                "stoppedEarlyBecause": self.stopped_early}

    async def iter_items(self, dataset_id):
        for page_id in self._runs[dataset_id]["_pages"] + ["999"]:   # 999: not requested
            for item in self.items_by_page.get(page_id, []):
                yield item

    async def aclose(self):
        pass


def _apify_items(page_id, n):
    """n distinct ads of `page_id`, from the fixture's DPA record."""
    import copy

    from tests.test_unit import _meta_records

    base = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "DPA")
    items = []
    for i in range(n):
        item = copy.deepcopy(base)
        item["ad_archive_id"] = f"{page_id}{i:04d}"
        item["page_id"] = item["snapshot"]["page_id"] = page_id
        items.append(item)
    return items


def _apify_setup(db, monkeypatch, n_pages, parallel=2):
    from tracker.worker import collector

    monkeypatch.setattr(collector, "APIFY_POLL_SECONDS", 0)
    db.execute("UPDATE pages SET is_tracked = FALSE")
    cid = db.execute("SELECT id FROM companies LIMIT 1").fetchone()["id"]
    for i in range(n_pages):
        db.execute("INSERT INTO pages (company_id, page_id) VALUES (%s, %s)", (cid, f"70{i}"))
    db.execute("""UPDATE app_settings SET collect_source = 'apify', apify_token = 'apify_api_tok',
                  apify_max_parallel_runs = %s""", (parallel,))
    db.commit()


def test_apify_collection_stores_every_page_of_every_batch(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=7)
    fake = FakeApify({f"70{i}": _apify_items(f"70{i}", 2) for i in range(7)} | {"999": _apify_items("999", 1)})
    run_id, progress = collector.run_collection(make_apify=lambda token: fake)
    assert len(fake.inputs) == 2 and fake.max_running <= 2        # 5 + 2 pages
    assert (progress.total, progress.new, progress.errors, progress.pages_done) == (14, 14, 0, 7)
    assert progress.cost_usd == pytest.approx(0.02)
    stored = db.execute("SELECT count(*) AS n FROM ads WHERE page_id LIKE '70%'").fetchone()["n"]
    assert stored == 14 and not db.execute("SELECT 1 FROM ads WHERE page_id = '999'").fetchone()
    row = db.execute("SELECT cost_usd FROM collection_runs WHERE id = %s", (run_id,)).fetchone()
    assert float(row["cost_usd"]) == pytest.approx(0.02)


def test_apify_parallel_runs_follow_the_setting(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=15, parallel=1)
    fake = FakeApify({})
    collector.run_collection(make_apify=lambda token: fake)
    assert len(fake.inputs) == 3 and fake.max_running == 1


def test_apify_failed_run_is_retried_once(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=2)
    fake = FakeApify({"700": _apify_items("700", 1)}, fail=1)
    _, progress = collector.run_collection(make_apify=lambda token: fake)
    assert len(fake.inputs) == 2 and (progress.total, progress.errors) == (1, 0)
    assert progress.cost_usd == pytest.approx(0.02)                 # the failed run cost too


def test_apify_batch_failing_twice_marks_nothing_gone(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=2)
    ok = FakeApify({"700": _apify_items("700", 2), "701": _apify_items("701", 1)})
    collector.run_collection(make_apify=lambda token: ok)
    failing = FakeApify({}, fail=2)
    _, progress = collector.run_collection(make_apify=lambda token: failing)
    assert len(failing.inputs) == 2 and progress.errors == 2        # one per Page of the Batch
    assert "apify_api_tok" not in (progress.last_error or "")
    active = db.execute("SELECT count(*) AS n FROM ads WHERE page_id IN ('700','701') AND is_active").fetchone()
    assert active["n"] == 3                                         # nothing marked gone


def test_apify_without_a_token_is_an_error(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=1)
    db.execute("UPDATE app_settings SET apify_token = NULL")
    db.commit()
    _, progress = collector.run_collection(make_apify=lambda token: FakeApify({}))
    assert progress.errors == 1 and "token" in progress.last_error


# ── Resuming a run through Apify a stopped worker left ──────────────────────

def _left_open(db, batches, stored=()):
    """A run through Apify its worker stopped in the middle of: `batches` maps each
    Actor run it started to (its Pages' page_ids, attempt); `stored` are ads of
    it already saved, as the run's counters had them."""
    from datetime import datetime, timezone

    from tracker.worker import persist
    from tracker.worker.normalize import from_apify_item

    pages = {r["page_id"]: r for r in db.execute("SELECT id, company_id, page_id FROM pages").fetchall()}
    run_id = persist.start_collection_run(db, trigger="manual", pages_total=7)
    for actor_run, (page_ids, attempt) in batches.items():
        persist.record_actor_run(db, run_id, {"id": actor_run, "defaultDatasetId": actor_run}, "BR",
                                 [pages[p]["id"] for p in page_ids], attempt)
    for item in stored:
        pg = pages[item["page_id"]]
        persist.upsert_ad(db, pg["company_id"], pg["id"], run_id, from_apify_item(item),
                          datetime.now(timezone.utc))
    db.execute("""UPDATE collection_runs SET total_ads_collected = %s, new_ads_discovered = %s,
                  current_step = 'Reading ads on Apify (9)' WHERE id = %s""",
               (len(stored), len(stored), run_id))
    db.commit()
    return run_id


def test_a_resumed_run_follows_its_actor_runs_instead_of_starting_them_again(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=7)             # a Batch of 5 Pages and one of 2
    items = {f"70{i}": _apify_items(f"70{i}", 2) for i in range(7)}
    fake = FakeApify(items)
    first, second = [f"70{i}" for i in range(5)], ["705", "706"]
    fake.adopt("a1", first, running=False)               # it had finished...
    fake.adopt("a2", second)                             # ...and this one was still going
    run_id = _left_open(db, {"a1": (first, 1), "a2": (second, 1)},
                        stored=[items["700"][0]])        # one ad of a1 was saved already

    resumed, progress = collector.run_collection(make_apify=lambda token: fake, resume=run_id)
    assert resumed == run_id and fake.inputs == []       # no Actor run started again
    assert (progress.total, progress.new, progress.errors, progress.pages_done) == (14, 14, 0, 7)
    assert progress.cost_usd == pytest.approx(0.02)      # what both Actor runs cost
    seen = db.execute("SELECT count(*) AS n FROM ad_appearances WHERE collection_run_id = %s",
                      (run_id,)).fetchone()
    assert seen["n"] == 14                               # each ad once, the one saved before too
    outcomes = db.execute("SELECT outcome FROM apify_actor_runs WHERE collection_run_id = %s",
                          (run_id,)).fetchall()
    assert [r["outcome"] for r in outcomes] == ["stored", "stored"]
    row = db.execute("SELECT finished_at, trigger FROM collection_runs WHERE id = %s", (run_id,)).fetchone()
    assert row["finished_at"] is None and row["trigger"] == "manual"   # the scheduler finishes it


def test_a_resumed_run_retries_a_failed_batch_and_starts_the_pages_never_sent(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=7)
    items = {f"70{i}": _apify_items(f"70{i}", 1) for i in range(7)}
    fake = FakeApify(items)
    first = [f"70{i}" for i in range(5)]
    fake.adopt("a1", first, final="FAILED")              # the stopped worker never saw it fail
    run_id = _left_open(db, {"a1": (first, 1)})          # and never started the second Batch

    _, progress = collector.run_collection(make_apify=lambda token: fake, resume=run_id)
    started = sorted(len(i["urls"]) for i in fake.inputs)
    assert started == [2, 5]                             # the retry, and the Batch never sent
    assert (progress.total, progress.errors, progress.pages_done) == (7, 0, 7)
    attempts = db.execute("""SELECT attempt, outcome FROM apify_actor_runs
                             WHERE collection_run_id = %s ORDER BY attempt, id""", (run_id,)).fetchall()
    assert [(r["attempt"], r["outcome"]) for r in attempts] == [(1, None), (1, "stored"), (2, "stored")]


def test_a_stopped_worker_leaves_its_apify_run_for_the_next_one(seeded, db, monkeypatch):
    import signal

    from tracker import db as dbmod
    from tracker.worker import collector, persist, scheduler

    _apify_setup(db, monkeypatch, n_pages=2)
    items = {"700": _apify_items("700", 2), "701": _apify_items("701", 1)}
    fake = FakeApify(items)
    fake.adopt("a1", ["700", "701"])
    run_id = _left_open(db, {"a1": (["700", "701"], 1)})
    self_hosted = persist.start_collection_run(db, trigger="schedule", pages_total=1)

    monkeypatch.setattr(scheduler.os, "_exit", lambda code: None)
    lock = scheduler.WorkerLock()
    try:
        assert lock.try_take()
        scheduler._stop_handler(lock)(signal.SIGTERM, None)
    finally:
        lock.release()
    runs = {r["id"]: r for r in db.execute(
        "SELECT id, finished_at, current_step, last_error FROM collection_runs").fetchall()}
    assert runs[self_hosted]["last_error"] == persist.RUN_STOPPED          # closed
    assert runs[run_id]["finished_at"] is None                             # left open...
    assert runs[run_id]["current_step"] == persist.STEP_PAUSED             # ...saying why

    # the next worker takes it on, as its startup run, and finishes it
    lock = scheduler.WorkerLock()
    try:
        resume = scheduler._take_the_lock(lock)
    finally:
        lock.release()
    assert resume == run_id
    monkeypatch.setattr(scheduler, "run_collection", lambda trigger, resume=None: collector.run_collection(
        make_apify=lambda token: fake, trigger=trigger, resume=resume))
    monkeypatch.setattr(scheduler, "_post_collection_steps", lambda step, fail: None)
    scheduler._run_once("startup", resume=resume)
    run = next(r for r in dbmod.get_run_status()["runs"] if r["id"] == run_id)
    assert (run["trigger"], run["total_ads_collected"], run["errors"]) == ("manual", 3, 0)
    assert run["finished_at"] is not None and fake.inputs == []


@pytest.mark.parametrize("why", ["source switched", "too old"])
def test_a_run_through_apify_that_cant_be_resumed_is_closed(seeded, db, monkeypatch, why):
    from tracker.worker import persist, scheduler

    _apify_setup(db, monkeypatch, n_pages=1)
    fake = FakeApify({})
    fake.adopt("a1", ["700"])
    run_id = _left_open(db, {"a1": (["700"], 1)})
    if why == "source switched":
        db.execute("UPDATE app_settings SET collect_source = 'self_hosted'")
    else:
        db.execute("UPDATE collection_runs SET started_at = now() - interval '2 days'")
    db.commit()
    lock = scheduler.WorkerLock()
    try:
        assert scheduler._take_the_lock(lock) is None
    finally:
        lock.release()
    row = db.execute("SELECT finished_at, last_error FROM collection_runs WHERE id = %s", (run_id,)).fetchone()
    assert row["finished_at"] is not None and row["last_error"] == persist.RUN_INTERRUPTED


# ── Showing an ad ───────────────────────────────────────────────────────────

def _store_fixture_ad(db, fmt):
    from datetime import datetime, timezone

    from tests.test_unit import _meta_records
    from tracker.worker import persist
    from tracker.worker.normalize import from_apify_item

    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == fmt)
    page = db.execute("SELECT id, company_id FROM pages WHERE page_id = '111'").fetchone()
    run_id = persist.start_collection_run(db, trigger="manual", pages_total=1)
    ad = from_apify_item(record)
    persist.upsert_ad(db, page["company_id"], page["id"], run_id, ad, datetime.now(timezone.utc))
    persist.update_page_profile(db, page["id"], ad.page)
    return record


def test_catalog_ad_shows_every_card_and_no_templates(seeded, db):
    from tracker import db as dbmod

    record = _store_fixture_ad(db, "DPA")
    ad = dbmod.get_ad_detail(record["ad_archive_id"])
    assert ad["is_carousel"] and ad["kind"] == "catalog" and len(ad["creatives"]) == 6
    texts = [c[k] for c in ad["creatives"] for k in ("title", "body", "description")] + [ad["body"]]
    assert not any("{{" in t for t in texts)
    assert all(c["title"] for c in ad["creatives"])
    assert ad["page"]["like_count"] == record["snapshot"]["page_like_count"]
    assert ad["page"]["avatar_url"] and ad["page"]["categories"]


def test_carousel_keeps_the_ads_primary_text(seeded, db):
    from tracker import db as dbmod

    record = _store_fixture_ad(db, "CAROUSEL")
    ad = dbmod.get_ad_detail(record["ad_archive_id"])
    assert ad["kind"] == "carousel" and len(ad["creatives"]) == len(record["snapshot"]["cards"])
    assert ad["body"] == record["snapshot"]["body"]["text"]


def test_image_ad_in_several_sizes_still_shows_formats(seeded, db):
    from tracker import db as dbmod

    db.execute("UPDATE ads SET display_format = 'IMAGE' WHERE ad_archive_id = 'AD_VARIANTS'")
    db.commit()
    ad = dbmod.get_ad_detail("AD_VARIANTS")
    assert not ad["is_carousel"] and len(ad["formats"]) == 3


def test_library_cards_carry_kind_cards_and_page_picture(seeded, db):
    from tests.test_db import _page as library_page

    record = _store_fixture_ad(db, "DPA")
    cards = {c["id"]: c for c in library_page(status="all")["cards"]}
    card = cards[record["ad_archive_id"]]
    assert (card["kind"], card["media"], card["cards"]) == ("catalog", "carousel", 6)
    assert card["avatar"] and "{{" not in card["title"] + card["body"]
    assert cards["AD_VARIANTS"]["cards"] == 1 and cards["AD_VARIANTS"]["formats"] == 3


def test_apify_progress_shows_ads_read_and_cost_before_saving(seeded, db, monkeypatch):
    from tracker.worker import collector, persist

    _apify_setup(db, monkeypatch, n_pages=1)
    beats = []
    real = persist.update_run_progress
    monkeypatch.setattr(persist, "update_run_progress", lambda conn, run_id, p: (
        beats.append((p.current_step, p.cost_usd, p.total)), real(conn, run_id, p))[1])
    fake = FakeApify({"700": _apify_items("700", 3)})
    collector.run_collection(make_apify=lambda token: fake)
    steps = [b[0] for b in beats]
    assert "Reading ads on Apify (3)" in steps
    first_save = steps.index("Saving ads (0/3)")
    assert beats[first_save][1] == pytest.approx(0.01) and beats[first_save][2] == 0   # cost known, nothing saved yet
    assert "Saving ads (3/3)" in steps


# ── Media, mirrored after the ads are stored ────────────────────────────────

def test_media_is_mirrored_in_parallel_after_the_ads_are_stored(seeded, db, monkeypatch):
    import threading
    import time

    from tracker.worker import persist

    record = _store_fixture_ad(db, "DCO")           # 3 video cards: video + preview each
    running, peak, lock, calls = [0], [0], threading.Lock(), []

    def slow_mirror(session, url, key, **kw):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            calls.append(key)
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return None if key.endswith("_thumb") else key + ".bin"   # previews fail

    monkeypatch.setattr(persist, "mirror_media", slow_mirror)
    progress = []
    result = persist.mirror_pending_media(
        db, make_session=object, workers=3, on_progress=lambda d, t: progress.append((d, t)))
    assert peak[0] > 1                              # several at once
    assert result.total == len(calls) and progress[-1] == (result.total, result.total)
    assert result.not_stored == 0                   # a failed download isn't the storage's doing
    rows = db.execute(
        """SELECT media_video_key, media_thumb_key FROM creatives
           WHERE ad_id = (SELECT id FROM ads WHERE ad_archive_id = %s)""",
        (record["ad_archive_id"],)).fetchall()
    assert rows and all(r["media_video_key"] and r["media_thumb_key"] is None for r in rows)
    # what failed is tried again next time; what's done isn't
    calls.clear()
    persist.mirror_pending_media(db, make_session=object, workers=3)
    assert calls and all(k.endswith("_thumb") for k in calls)


def test_media_the_storage_refuses_is_counted_apart(seeded, db, monkeypatch):
    from tracker.worker import persist

    _store_fixture_ad(db, "DCO")

    def refuse_videos(session, url, key, **kw):
        if key.endswith("_video"):
            raise persist.StoreFailed("AccessDenied")
        return key + ".bin"

    monkeypatch.setattr(persist, "mirror_media", refuse_videos)
    result = persist.mirror_pending_media(db, make_session=object)
    assert result.not_stored > 0 and result.store_error == "AccessDenied"
    assert result.saved == result.total - result.not_stored


def test_videos_are_stored_from_metas_sd_file_without_reencoding(seeded, db, monkeypatch):
    from tracker.worker import persist

    record = _store_fixture_ad(db, "DCO")
    calls = []
    monkeypatch.setattr(persist, "mirror_media",
                        lambda session, url, key, compress_video=True, image_quality="standard":
                        calls.append((url, key, compress_video)))
    persist.mirror_pending_media(db, make_session=object)            # standard quality
    videos = [c for c in calls if c[1].endswith("_video")]
    sd_urls = {card["video_sd_url"] for card in record["snapshot"]["cards"]}
    assert videos and all(url in sd_urls and not compress for url, _, compress in videos)
    # high quality: the HD file, re-encoded
    calls.clear()
    persist.mirror_pending_media(db, make_session=object, video_quality="high")
    assert all(compress for url, key, compress in calls if key.endswith("_video"))


def test_apify_refusal_is_reported_and_not_retried(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=2)
    fake = FakeApify({"700": _apify_items("700", 2)}, refuse=True)
    _, progress = collector.run_collection(make_apify=lambda token: fake)
    assert len(fake.inputs) == 1                                  # the same input would be refused again
    assert progress.total == 0 and progress.errors == 2           # one per Page of the Batch
    assert "Run refused" in progress.last_error


def test_a_partial_run_leaves_the_ads_it_did_not_see(seeded, db, monkeypatch):
    from tracker.worker import collector

    _apify_setup(db, monkeypatch, n_pages=1)
    full = FakeApify({"700": _apify_items("700", 3)})
    collector.run_collection(make_apify=lambda token: full)
    active = "SELECT count(*) AS n FROM ads WHERE page_id = '700' AND is_active"
    assert db.execute(active).fetchone()["n"] == 3

    # the Actor stopped early: the two ads it didn't reach are not "gone"
    partial = FakeApify({"700": _apify_items("700", 1)}, stopped_early="time limit")
    _, progress = collector.run_collection(make_apify=lambda token: partial)
    assert progress.total == 1 and progress.errors == 0
    assert db.execute(active).fetchone()["n"] == 3
