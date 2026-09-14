"""Route tests via FastAPI TestClient (need a Postgres — see conftest).

These assert DURABLE behaviour — status codes, auth gating, security/robustness,
and that the data layer's output reaches the response. They deliberately do NOT
assert UI copy or markup (nav labels, class names, wording): that churns, and the
meaningful behaviour behind it is covered by the data-layer tests instead.
"""

from __future__ import annotations

from dataclasses import replace


def test_anonymous_is_redirected_to_login(anon_client, seeded):
    r = anon_client.get("/ads/AD_EVERGREEN", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_ads_page_authed_ok_and_ships_its_first_page(client, seeded):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"
    # the page carries its first page of cards to the client (data contract, not copy)
    assert "AD_EVERGREEN" in r.text and "AD_CAROUSEL" in r.text
    assert "AD_CEMETERY" not in r.text          # inactive: not on the default (active) page


def test_login_is_throttled_after_repeated_failures(anon_client, db, monkeypatch):
    from tracker import db as dbmod
    from tracker.auth import hash_password
    from tracker.web import throttle

    monkeypatch.setattr(throttle, "login_throttle", throttle.LoginThrottle(max_failures=3))
    monkeypatch.setattr(throttle, "account_throttle", throttle.LoginThrottle(max_failures=100))
    dbmod.create_user("admin", hash_password("right-password"))
    good = {"username": "admin", "password": "right-password"}
    bad = {"username": "admin", "password": "wrong"}

    # a success clears earlier failures
    assert anon_client.post("/login", data=bad).status_code == 401
    assert anon_client.post("/login", data=good, follow_redirects=False).status_code == 302
    assert anon_client.post("/login", data=bad).status_code == 401
    assert anon_client.post("/login", data=bad).status_code == 401
    r = anon_client.post("/login", data=bad)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # blocked even with the right password: no cookie handed out
    r = anon_client.post("/login", data=good, follow_redirects=False)
    assert r.status_code == 429 and "set-cookie" not in r.headers


def test_login_is_throttled_per_account_whatever_the_address(anon_client, db, monkeypatch):
    from tracker import db as dbmod
    from tracker.auth import hash_password
    from tracker.web import throttle

    # the per-address limit never trips here: as if every guess came from a new IP
    monkeypatch.setattr(throttle, "login_throttle", throttle.LoginThrottle(max_failures=100))
    monkeypatch.setattr(throttle, "account_throttle", throttle.LoginThrottle(max_failures=3))
    dbmod.create_user("admin", hash_password("right-password"))
    bad = {"username": "admin", "password": "wrong"}

    assert anon_client.post("/login", data=bad).status_code == 401
    assert anon_client.post("/login", data=bad).status_code == 401
    assert anon_client.post("/login", data=bad).status_code == 429
    r = anon_client.post("/login", data={"username": "admin", "password": "right-password"},
                         follow_redirects=False)
    assert r.status_code == 429 and "set-cookie" not in r.headers
    # unknown usernames aren't counted per account (nothing to protect, nothing to store)
    assert anon_client.post("/login", data={"username": "nobody", "password": "x"}).status_code == 401
    assert throttle.account_throttle._failures.keys() == {"1"}


def test_ad_detail_known_and_unknown(client, seeded):
    assert client.get("/ads/AD_EVERGREEN").status_code == 200
    assert client.get("/ads/DOES_NOT_EXIST").status_code == 404


def test_ad_detail_links_to_its_landing_page(client, seeded):
    r = client.get("/ads/AD_EVERGREEN")
    assert f'href="/landing-pages/{seeded["lp"]}"' in r.text
    assert 'href="https://northwindprep.example/exam-prep"' in r.text


def test_landing_detail_known_and_unknown(client, seeded):
    assert client.get(f"/landing-pages/{seeded['lp']}").status_code == 200
    assert client.get("/landing-pages/999999").status_code == 404


def test_landing_catalog_accepts_the_all_companies_filter(client, seeded):
    # the filter form submits company="" for "All"; that must not be a 422
    for query in ("company=&sort=duration", "company=&sort=ads", "company=abc", ""):
        assert client.get(f"/landing-pages?{query}").status_code == 200


def test_first_run_setup_gate(client, db):
    # no users -> the setup wizard is served; once a user exists it redirects away.
    assert client.get("/setup", follow_redirects=False).status_code == 200

    from tracker import db as dbmod
    from tracker.auth import hash_password

    dbmod.create_user("admin", hash_password("password"))
    assert client.get("/setup", follow_redirects=False).status_code == 302


def test_collect_now_is_single_flight(client, seeded):
    headers = {"Accept": "application/json"}
    first = client.post("/admin/collect-now", headers=headers).json()
    assert first["accepted"] is True and first["state"] == "queued"
    # a second click while queued must not queue another run
    second = client.post("/admin/collect-now", headers=headers).json()
    assert second["accepted"] is False and second["state"] == "queued"
    # the plain form post (no JS) still redirects back to Settings
    r = client.post("/admin/collect-now", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/admin")


def test_run_status_endpoint(client, anon_client, seeded):
    assert anon_client.get("/admin/run-status").status_code == 401
    r = client.get("/admin/run-status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "idle" and len(body["runs"]) == 1


def test_admin_edit_routes(client, seeded, db):
    co = seeded["companies"]["contoso"]
    r = client.post(f"/admin/companies/{co}/edit", data={"name": "Contoso", "domain": ""},
                    follow_redirects=False)
    assert r.status_code == 302 and "err=" not in r.headers["location"]
    assert db.execute("SELECT name FROM companies WHERE id = %s", (co,)).fetchone()["name"] == "Contoso"
    # empty name is rejected with a message, not saved
    r = client.post(f"/admin/companies/{co}/edit", data={"name": " "}, follow_redirects=False)
    assert "err=" in r.headers["location"]

    pk = db.execute("SELECT id FROM pages WHERE page_id = '222'").fetchone()["id"]
    r = client.post(f"/admin/pages/{pk}/edit", data={"page_id": "111", "page_name": "x"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]            # duplicate page ID -> message
    assert client.get("/admin").status_code == 200


def test_static_assets_served(client):
    js = client.get("/static/js/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    css = client.get("/static/css/app.css")
    assert css.status_code == 200 and "css" in css.headers["content-type"]


def test_not_indexable_by_crawlers(client, anon_client):
    robots = anon_client.get("/robots.txt")
    assert robots.status_code == 200
    assert "User-agent: *" in robots.text and "Disallow: /" in robots.text
    # every kind of response carries the header, authed or not
    for c, path in ((anon_client, "/login"), (anon_client, "/robots.txt"),
                    (client, "/static/css/app.css")):
        assert "noindex" in c.get(path, follow_redirects=False).headers["x-robots-tag"]
    assert anon_client.get("/ads/x", follow_redirects=False).headers["x-robots-tag"]
    # the auto-generated, unauthenticated API docs are off
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon_client.get(path).status_code == 404


class _Body:
    def __init__(self, data):
        self.data, self.closed = data, False

    def iter_chunks(self, size):
        for i in range(0, len(self.data), size):
            yield self.data[i:i + size]

    def close(self):
        self.closed = True


def _fake_store(monkeypatch, data: bytes, opened: list):
    """Serve `data` as the object behind every key, honouring a byte range."""
    from tracker import storage

    def open_object(key, byte_range=None):
        if byte_range is None:
            obj = storage.StoredObject(_Body(data), "video/mp4", len(data), None)
        else:
            start, _, end = byte_range.removeprefix("bytes=").partition("-")
            start, end = int(start), min(int(end or len(data) - 1), len(data) - 1)
            if start >= len(data):
                raise storage.RangeNotSatisfiable(key)
            part = data[start:end + 1]
            obj = storage.StoredObject(_Body(part), "video/mp4", len(part),
                                       f"bytes {start}-{end}/{len(data)}")
        opened.append((byte_range, obj))
        return obj

    monkeypatch.setattr(storage, "open_object", open_object)


def test_media_proxy_streams_whole_object(client, monkeypatch):
    opened = []
    _fake_store(monkeypatch, b"x" * 300_000, opened)
    r = client.get("/media/ads/v.mp4")
    assert r.status_code == 200 and r.content == b"x" * 300_000
    assert r.headers["accept-ranges"] == "bytes" and r.headers["content-length"] == "300000"
    assert opened[0][1].body.closed                       # the storage body is released


def test_media_proxy_serves_byte_ranges(client, monkeypatch):
    data = bytes(range(256)) * 1000
    _fake_store(monkeypatch, data, [])
    r = client.get("/media/ads/v.mp4", headers={"Range": "bytes=1000-1999"})
    assert r.status_code == 206
    assert r.content == data[1000:2000]
    assert r.headers["content-range"] == f"bytes 1000-1999/{len(data)}"
    assert client.get("/media/ads/v.mp4", headers={"Range": "bytes=999999999-"}).status_code == 416
    # a malformed range is ignored: the whole object is served
    assert client.get("/media/ads/v.mp4", headers={"Range": "lines=1-2"}).status_code == 200


def test_media_proxy_no_500_when_store_unreachable(client, monkeypatch):
    from tracker import storage

    def boom(key, byte_range=None):
        raise RuntimeError("object store unreachable")

    monkeypatch.setattr(storage, "open_object", boom)
    # no fallback -> a clean 404, never a 500
    assert client.get("/media/ads/x.jpg").status_code == 404


def test_media_proxy_rejects_ssrf_fallback(client, monkeypatch):
    import httpx

    from tracker import storage

    monkeypatch.setattr(storage, "open_object", lambda key, byte_range=None: None)
    calls = []

    class NoNetwork:  # the proxy's own HTTP client; the test client is left alone
        def __init__(self, *a, **kw):
            calls.append("client created")
            raise AssertionError("a disallowed fallback must never be fetched")

    monkeypatch.setattr(httpx, "Client", NoNetwork)
    r = client.get("/media/ads/x.jpg", params={"fallback": "http://169.254.169.254/latest"})
    assert r.status_code == 404
    assert calls == []           # the non-Meta/http host was never fetched


def test_library_api(client, anon_client, seeded):
    assert anon_client.get("/api/ads").status_code == 401
    body = client.get("/api/ads", params={"status": "all", "media": "carousel"}).json()
    assert [c["id"] for c in body["cards"]] == ["AD_CAROUSEL"]
    assert body["total"] == 1 and body["champions"]
    assert client.get("/api/ads", params={"page": "abc", "min_days": "x"}).status_code == 200


# ── first-run wizard ────────────────────────────────────────────────────────

def _wizard_state(db):
    return db.execute(
        "SELECT setup_completed, collect_country, collect_interval_hours, collect_now FROM app_settings"
    ).fetchone()


def test_wizard_language_admin_competitor_collection(anon_client, db):
    db.execute("UPDATE app_settings SET setup_completed = FALSE")
    db.commit()
    c = anon_client
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"

    # step 1: detected from the browser, previewed, then saved for the instance
    assert '<html lang="es">' in c.get("/setup", headers={"accept-language": "es-MX"}).text
    assert '<html lang="pt">' in c.get("/setup", params={"lang": "pt"}).text
    r = c.post("/setup/language", data={"language": "pt"}, follow_redirects=False)
    assert r.headers["location"] == "/setup/admin" and "set-cookie" not in r.headers
    assert db.execute("SELECT language FROM app_settings").fetchone()["language"] == "pt"
    assert '<html lang="pt">' in c.get("/setup/admin", headers={"accept-language": "en"}).text

    # step 2: a wrong setup code or a short password is refused, a good one signs the admin in
    assert c.get("/setup/admin").status_code == 200
    admin = {"username": "admin", "password": "long-enough", "confirm": "long-enough"}
    assert c.post("/setup/admin", data=admin).status_code == 400                        # no setup code
    r = c.post("/setup/admin", data={**admin, "setup_code": "guess"}, follow_redirects=False)
    assert r.status_code == 400 and "set-cookie" not in r.headers
    assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    code = "test-setup-code"
    assert c.post("/setup/admin", data={**admin, "password": "short", "confirm": "short",
                                        "setup_code": code}).status_code == 400
    r = c.post("/setup/admin", data={**admin, "setup_code": code}, follow_redirects=False)
    assert r.headers["location"] == "/setup/competitor"
    # until the wizard is done, the Ads screen sends the admin back to it
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup/competitor"
    assert c.get("/setup/admin", follow_redirects=False).headers["location"] == "/setup/competitor"

    # step 3: a page ID must be digits
    assert c.get("/setup/competitor").status_code == 200
    assert c.post("/setup/competitor", data={"company": "Acme", "page_id": "acme"}).status_code == 400
    r = c.post("/setup/competitor", data={"company": "Acme", "page_id": "12345"}, follow_redirects=False)
    assert r.headers["location"] == "/setup/collection"

    # step 4: country + interval; a tracked page starts the first run
    assert c.get("/setup/collection").status_code == 200
    r = c.post("/setup/collection", data={"country": "MX", "interval": "12"}, follow_redirects=False)
    assert r.headers["location"] == "/admin"
    state = _wizard_state(db)
    assert (state["setup_completed"], state["collect_country"], state["collect_interval_hours"],
            state["collect_now"]) == (True, "MX", 12, True)
    assert db.execute("SELECT page_id FROM pages").fetchone()["page_id"] == "12345"
    assert c.get("/setup/collection", follow_redirects=False).headers["location"] == "/"


def test_wizard_without_a_competitor(client, db):
    from tracker import db as dbmod
    from tracker.auth import hash_password

    dbmod.create_user("admin", hash_password("long-enough"))
    db.execute("UPDATE app_settings SET setup_completed = FALSE")
    db.commit()
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/setup/competitor"
    # skipping the competitor: no run is queued, and an odd interval falls back to 6h
    r = client.post("/setup/collection", data={"country": "ZZ", "interval": "7"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    state = _wizard_state(db)
    assert (state["setup_completed"], state["collect_country"], state["collect_interval_hours"],
            state["collect_now"]) == (True, "US", 6, False)
    assert client.get("/", follow_redirects=False).status_code == 200


def test_wizard_steps_need_the_admin_session(anon_client, db):
    from tracker import db as dbmod
    from tracker.auth import hash_password

    dbmod.create_user("admin", hash_password("long-enough"))
    db.execute("UPDATE app_settings SET setup_completed = FALSE")
    db.commit()
    for path in ("/setup/competitor", "/setup/collection"):
        assert anon_client.get(path, follow_redirects=False).headers["location"] == "/login"
    # the language and admin steps are gone once an admin exists
    assert anon_client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    r = anon_client.post("/setup/admin", data={"username": "x", "password": "long-enough", "confirm": "long-enough"},
                         follow_redirects=False)
    assert r.headers["location"] == "/setup/competitor"
    assert db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1


def test_first_user_is_created_once(db):
    from tracker import db as dbmod

    assert dbmod.create_first_user("admin", "hash")["username"] == "admin"
    assert dbmod.create_first_user("intruder", "hash") is None   # e.g. a racing wizard submission
    assert [r["username"] for r in db.execute("SELECT username FROM users").fetchall()] == ["admin"]


def test_setup_code_is_generated_and_logged_when_unset(monkeypatch, caplog):
    from tracker.config import get_settings
    from tracker.web.routes import setup

    monkeypatch.setattr(setup, "get_settings", lambda: replace(get_settings(), setup_token=""))
    monkeypatch.setattr(setup, "_generated_token", None)
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        code = setup.setup_token()
    assert len(code) >= 20 and code in caplog.text
    assert setup.setup_token() == code                     # stable for the process
    assert setup._setup_token_matches(f" {code} ") and not setup._setup_token_matches("")


def test_settings_rejects_an_unknown_country(client, db):
    client.post("/admin/settings", data={"collect_interval_hours": "4", "collect_country": "ZZ"})
    row = db.execute("SELECT collect_interval_hours, collect_country FROM app_settings").fetchone()
    assert row["collect_interval_hours"] == 4 and row["collect_country"] == "BR"
    client.post("/admin/settings", data={"collect_interval_hours": "4", "collect_country": "us"})
    assert db.execute("SELECT collect_country FROM app_settings").fetchone()["collect_country"] == "US"


# ── CSV export ──────────────────────────────────────────────────────────────

def test_ads_csv_export(client, anon_client, seeded):
    import csv
    import io

    assert anon_client.get("/ads.csv", follow_redirects=False).status_code == 302
    r = client.get("/ads.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    by_id = {row["ad_archive_id"]: row for row in rows}
    assert set(by_id) == {"AD_EVERGREEN", "AD_CAROUSEL", "AD_CEMETERY", "AD_VARIANTS"}
    assert by_id["AD_CAROUSEL"]["media"] == "carousel"
    assert by_id["AD_VARIANTS"]["media"] == "image" and by_id["AD_VARIANTS"]["creatives"] == "3"
    assert by_id["AD_EVERGREEN"]["impression_rank"] == "1"
    assert by_id["AD_CEMETERY"]["impression_rank"] == ""
    assert by_id["AD_EVERGREEN"]["landing_page_url"] == "https://northwindprep.example/exam-prep"

    active = client.get("/ads.csv", params={"status": "active", "company": "Northwind Prep"})
    ids = [row["ad_archive_id"] for row in csv.DictReader(io.StringIO(active.content.decode("utf-8-sig")))]
    assert ids == ["AD_EVERGREEN", "AD_VARIANTS"]                  # longest-running first


def test_landing_pages_csv_export(client, seeded):
    r = client.get("/landing-pages.csv", params={"company": "", "sort": "duration"})
    assert r.status_code == 200
    assert "northwindprep.example/exam-prep" in r.content.decode("utf-8-sig")


# ── translations ────────────────────────────────────────────────────────────

def test_settings_save_language_and_default_country(client, db):
    r = client.post("/admin/settings", data={"collect_interval_hours": "3", "collect_country": "br",
                                             "language": "es"}, follow_redirects=False)
    assert r.status_code == 302
    row = db.execute("SELECT collect_interval_hours, collect_country, language FROM app_settings").fetchone()
    assert (row["collect_interval_hours"], row["collect_country"], row["language"]) == (3, "BR", "es")
    assert '<html lang="es">' in client.get("/admin", headers={"accept-language": "pt-BR"}).text
    client.post("/admin/settings", data={"collect_interval_hours": "3", "collect_country": "BR", "language": "xx"})
    assert db.execute("SELECT language FROM app_settings").fetchone()["language"] == "es"


def test_page_country_in_settings(client, seeded, db):
    co = seeded["companies"]["contoso"]
    client.post(f"/admin/companies/{co}/pages", data={"page_id": "333", "collect_country": "us"})
    client.post(f"/admin/companies/{co}/pages", data={"page_id": "444", "collect_country": ""})
    client.post(f"/admin/companies/{co}/pages", data={"page_id": "555", "collect_country": "ZZ"})
    rows = db.execute("SELECT page_id, collect_country FROM pages WHERE page_id IN ('333', '444', '555')").fetchall()
    countries = {r["page_id"]: r["collect_country"] for r in rows}
    assert countries == {"333": "US", "444": None, "555": None}      # empty or unknown = the default

    pk = db.execute("SELECT id FROM pages WHERE page_id = '444'").fetchone()["id"]
    client.post(f"/admin/pages/{pk}/edit", data={"page_id": "444", "page_name": "", "collect_country": "MX"})
    assert db.execute("SELECT collect_country FROM pages WHERE id = %s", (pk,)).fetchone()["collect_country"] == "MX"
    client.post(f"/admin/pages/{pk}/edit", data={"page_id": "444", "page_name": "", "collect_country": ""})
    assert db.execute("SELECT collect_country FROM pages WHERE id = %s", (pk,)).fetchone()["collect_country"] is None
    assert client.get("/admin").status_code == 200


def test_pages_render_in_every_language(client, seeded, db):
    from tracker import i18n

    for lang in i18n.LANGUAGES:
        db.execute("UPDATE app_settings SET language = %s", (lang,))
        db.commit()
        for path in ("/", "/ads/AD_CAROUSEL", "/landing-pages", f"/landing-pages/{seeded['lp']}", "/admin"):
            r = client.get(path)
            assert r.status_code == 200, (lang, path)
            assert f'<html lang="{lang}">' in r.text
        js = client.get(f"/i18n/{lang}.js", params={"v": i18n.catalog_version(lang)})
        assert js.status_code == 200 and "immutable" in js.headers["cache-control"]
    assert client.get("/i18n/xx.js").status_code == 404


# ── Collection source ───────────────────────────────────────────────────────

def _source(db):
    return db.execute(
        "SELECT collect_source, apify_token, apify_max_parallel_runs FROM app_settings").fetchone()


def test_settings_switch_the_collection_source(client, db):
    r = client.post("/admin/source", data={"collect_source": "apify", "apify_token": "",
                                           "apify_max_parallel_runs": "3"}, follow_redirects=False)
    assert "err=apify_token_missing" in r.headers["location"]           # no token yet
    assert _source(db)["collect_source"] == "self_hosted"

    client.post("/admin/source", data={"collect_source": "apify", "apify_token": " apify_api_secret1234 ",
                                       "apify_max_parallel_runs": "3"})
    assert tuple(_source(db).values()) == ("apify", "apify_api_secret1234", 3)
    # an empty token field keeps the saved token
    client.post("/admin/source", data={"collect_source": "self_hosted", "apify_token": "",
                                       "apify_max_parallel_runs": "5"})
    assert tuple(_source(db).values()) == ("self_hosted", "apify_api_secret1234", 5)
    for bad in ("0", "6", "two"):
        r = client.post("/admin/source", data={"collect_source": "apify",
                                               "apify_max_parallel_runs": bad}, follow_redirects=False)
        assert "err=apify_parallel_invalid" in r.headers["location"], bad
    assert _source(db)["apify_max_parallel_runs"] == 5


def test_settings_never_show_the_apify_token(client, db):
    db.execute("UPDATE app_settings SET apify_token = 'apify_api_topsecret9876'")
    db.commit()
    html = client.get("/admin").text
    assert "topsecret" not in html and "••••9876" in html


def test_apify_token_check(client, anon_client, db, monkeypatch):
    from tracker import apify

    checked = []

    def account_username(token):
        checked.append(token)
        if token == "bad":
            raise apify.ApifyError("unauthorized")
        return "someone"

    monkeypatch.setattr(apify, "account_username", account_username)
    assert anon_client.post("/admin/apify/test", data={"apify_token": "x"}).status_code == 401
    assert client.post("/admin/apify/test", data={"apify_token": "bad"}).json() == {
        "ok": False, "error": "unauthorized"}
    assert client.post("/admin/apify/test", data={"apify_token": ""}).json() == {
        "ok": False, "error": "missing"}
    db.execute("UPDATE app_settings SET apify_token = 'stored'")
    db.commit()
    # an empty field checks the saved token
    assert client.post("/admin/apify/test", data={"apify_token": ""}).json() == {
        "ok": True, "username": "someone"}
    assert checked == ["bad", "stored"]


def test_apify_errors_never_carry_the_token(monkeypatch):
    import httpx

    from tracker import apify

    def boom(url, headers, timeout):
        raise httpx.ConnectError(f"failed {headers['Authorization']}")

    monkeypatch.setattr(httpx, "get", boom)
    try:
        apify.account_username("apify_api_leaky")
    except apify.ApifyError as exc:
        assert exc.reason == "unreachable" and "leaky" not in str(exc)


def test_wizard_collects_through_apify(client, db, monkeypatch):
    from tracker import apify
    from tracker import db as dbmod
    from tracker.auth import hash_password

    dbmod.create_user("admin", hash_password("long-enough"))
    db.execute("UPDATE app_settings SET setup_completed = FALSE")
    db.commit()
    monkeypatch.setattr(apify, "account_username",
                        lambda token: (_ for _ in ()).throw(apify.ApifyError("unauthorized"))
                        if token == "bad" else "someone")
    r = client.post("/setup/collection", data={"country": "BR", "interval": "6",
                                               "collect_source": "apify", "apify_token": ""})
    assert r.status_code == 400                                          # no token
    r = client.post("/setup/collection", data={"country": "BR", "interval": "6",
                                               "collect_source": "apify", "apify_token": "bad"})
    assert r.status_code == 400 and not db.execute(
        "SELECT setup_completed FROM app_settings").fetchone()["setup_completed"]
    r = client.post("/setup/collection", data={"country": "BR", "interval": "6",
                                               "collect_source": "apify", "apify_token": "good"},
                    follow_redirects=False)
    assert r.status_code == 302
    assert tuple(_source(db).values()) == ("apify", "good", 2)


def test_catalog_ad_page_renders_its_cards(client, seeded, db):
    from tests.test_db import _store_fixture_ad

    record = _store_fixture_ad(db, "DPA")
    html = client.get(f"/ads/{record['ad_archive_id']}").text
    assert "{{product" not in html
    assert html.count('class="ci-n"') == 6 and "Card 1 of 6" in html
    assert 'class="pagehead"' in html and "likes" in html


def test_ad_page_never_links_to_script_urls(client, seeded, db):
    from tests.test_db import _store_fixture_ad

    record = _store_fixture_ad(db, "CAROUSEL")
    db.execute("""UPDATE creatives SET link_url = 'javascript:alert(document.domain)'
                  WHERE ad_id = (SELECT id FROM ads WHERE ad_archive_id = %s)""",
               (record["ad_archive_id"],))
    db.execute("UPDATE pages SET profile_uri = 'javascript:alert(1)' WHERE page_id = '111'")
    db.commit()
    html = client.get(f"/ads/{record['ad_archive_id']}").text.lower()
    # shown as text at most, never as a link
    assert 'href="javascript:' not in html and "href='javascript:" not in html
    assert html.count("javascript:") == html.count("<span class=\"dest-lp\">javascript:")


def test_apify_help_page_is_public_and_quotes_the_prices(anon_client):
    from tracker import apify

    r = anon_client.get("/help/apify")
    assert r.status_code == 200
    assert apify.SIGNUP_URL in r.text and apify.TOKEN_URL in r.text
    assert "US$ 0.25" in r.text and "US$ 5.00" in r.text and "20,000" in r.text


def test_help_page_formats_prices_for_the_language(anon_client, db):
    db.execute("UPDATE app_settings SET language = 'pt'")
    db.commit()
    html = anon_client.get("/help/apify").text
    assert "US$ 0,25" in html and "20.000" in html


def test_wizard_recommends_apify_and_links_the_help(client, db):
    from tracker import db as dbmod
    from tracker.auth import hash_password

    dbmod.create_user("admin", hash_password("long-enough"))
    db.execute("UPDATE app_settings SET setup_completed = FALSE")
    db.commit()
    html = client.get("/setup/collection").text
    assert 'class="rec"' in html and 'href="/help/apify"' in html
    assert 'value="apify" checked' in html                       # the recommended one is picked


def test_settings_save_media_quality(client, db):
    def quality():
        return tuple(db.execute("SELECT video_quality, image_quality FROM app_settings").fetchone().values())

    assert quality() == ("standard", "standard")                     # fast by default
    html = client.get("/admin").text
    assert 'name="video_quality"' in html and 'name="image_quality"' in html
    client.post("/admin/settings", data={"collect_interval_hours": "6", "collect_country": "BR",
                                         "video_quality": "high", "image_quality": "high"})
    assert quality() == ("high", "high")
    client.post("/admin/settings", data={"collect_interval_hours": "6", "collect_country": "BR",
                                         "video_quality": "ultra", "image_quality": "high"})
    assert quality() == ("high", "high")                             # unknown value: unchanged
