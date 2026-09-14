"""Pure-function unit tests — no database, no network. These always run.

They lock down the logic most likely to break silently on a refactor: auth,
the carousel heuristic, URL canonicalization, duration math, the SSRF allowlist,
the duration tiers, the open-redirect guard, and image compression.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


# ── auth ────────────────────────────────────────────────────────────────────

def test_password_hash_roundtrip():
    from tracker.auth import hash_password, verify_password

    h = hash_password("s3cret-pw")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-pw", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("s3cret-pw", "not-a-valid-hash") is False


def test_session_cookie_valid_expired_tampered():
    from tracker.auth import create_session_value, verify_session_value

    secret = "a-secret-that-is-at-least-32-chars"
    val = create_session_value(secret)
    assert verify_session_value(val, secret) is True
    assert verify_session_value(val, "other-secret-also-at-least-32-chars") is False  # wrong secret
    assert verify_session_value(val[:-1] + ("0" if val[-1] != "0" else "1"), secret) is False  # tampered sig
    assert verify_session_value("garbage", secret) is False
    expired = create_session_value(secret, now=0)                       # expiry in 1970
    assert verify_session_value(expired, secret) is False


def test_session_cookie_refuses_a_missing_or_short_secret():
    import hashlib
    import hmac

    import pytest

    from tracker.auth import create_session_value, verify_session_value

    for weak in ("", "short-secret"):
        with pytest.raises(RuntimeError):
            create_session_value(weak)
        # a cookie anyone can compute with the weak key is still refused
        expiry = "4102444800"
        forged = f"{expiry}.{hmac.new(weak.encode(), expiry.encode(), hashlib.sha256).hexdigest()}"
        assert verify_session_value(forged, weak) is False


def test_login_throttle_blocks_then_releases():
    from tracker.web.throttle import LoginThrottle

    now = [1000.0]
    t = LoginThrottle(max_failures=3, window=60, clock=lambda: now[0])
    for _ in range(2):
        t.record_failure("1.2.3.4")
    assert t.retry_after("1.2.3.4") == 0            # under the limit
    t.record_failure("1.2.3.4")
    assert t.retry_after("1.2.3.4") == 60           # blocked for the window
    assert t.retry_after("5.6.7.8") == 0            # per address
    now[0] += 59.5
    assert t.retry_after("1.2.3.4") == 1
    now[0] += 1
    assert t.retry_after("1.2.3.4") == 0            # the failures aged out
    t.record_failure("5.6.7.8")                     # ...and are swept from memory
    assert "1.2.3.4" not in t._failures

    for _ in range(3):
        t.record_failure("9.9.9.9")
    t.reset("9.9.9.9")                              # a successful login clears it
    assert t.retry_after("9.9.9.9") == 0


# ── carousel heuristic ──────────────────────────────────────────────────────

def _cr(title="", body="", link="", cta=""):
    return {"title": title, "body": body, "link_url": link, "cta_text": cta}


def test_is_real_carousel():
    from tracker.db import _is_real_carousel

    assert _is_real_carousel([]) is False
    assert _is_real_carousel([_cr(title="only")]) is False
    # distinct cards -> real carousel
    assert _is_real_carousel([_cr(title="a"), _cr(title="b")]) is True
    assert _is_real_carousel([_cr(link="/a"), _cr(link="/b")]) is True
    # placement variants: identical text/link/cta, only image differs -> NOT a carousel
    same = [_cr(title="t", body="b", link="/x", cta="Go"),
            _cr(title="t", body="b", link="/x", cta="Go"),
            _cr(title="t", body="b", link="/x", cta="Go")]
    assert _is_real_carousel(same) is False
    # formats whose links differ only by per-placement UTM tags are still one ad's formats
    utm = [_cr(title="t", link="https://a.com/x?utm_content=story"),
           _cr(title="t", link="https://a.com/x?utm_content=feed")]
    assert _is_real_carousel(utm) is False


# ── URL canonicalization (linking) ──────────────────────────────────────────

def test_canonicalize_url():
    from tracker.worker.linking import canonicalize_url

    assert canonicalize_url(
        "https://EXAMPLE.com/Path/?utm_source=x&fbclid=y&q=1#frag"
    ) == "https://example.com/Path?q=1"
    assert canonicalize_url("https://a.com/x/") == "https://a.com/x"
    assert canonicalize_url("https://a.com") == "https://a.com"
    assert canonicalize_url("https://a.com/?ad_id=5&keep=1") == "https://a.com?keep=1"
    assert canonicalize_url("mailto:x@y.com") is None
    assert canonicalize_url("") is None
    assert canonicalize_url("   ") is None


# ── landing-page title extraction ───────────────────────────────────────────

def test_extract_title_and_description():
    from tracker.worker.resolver import extract_title_and_description as extract

    # og:title wins over <title>; attribute order doesn't matter; entities decoded
    html = ('<head><title>Guia | Brand</title>'
            '<meta content="Guia &amp; Método" property="og:title">'
            "<meta name='description' content='  Baixe   grátis  '></head>")
    assert extract(html) == ("Guia & Método", "Baixe grátis")
    # falls back to <title> (inner tags/whitespace cleaned) and og:description
    html = ('<title>\n  Pós-Graduação\n EAD </title>'
            '<meta property="og:description" content="Online">')
    assert extract(html) == ("Pós-Graduação EAD", "Online")
    # a template comment documenting "<title> e <meta description>" is not the title
    html = ("<!-- 1. EDITAR-TITULO · <title> e <meta description>\n 2. EDITAR-HERO -->"
            "<html><head><title>Inscrição confirmada</title></head></html>")
    assert extract(html) == ("Inscrição confirmada", None)
    assert extract("<p>no head at all</p>") == (None, None)
    assert extract('<meta property="og:title" content="   ">') == (None, None)


# ── landing-page crawl SSRF guard ───────────────────────────────────────────

def _fake_dns(monkeypatch, table):
    from tracker.worker import resolver

    def resolve(host, port):
        if host not in table:
            raise OSError("no such host")
        return table[host]

    monkeypatch.setattr(resolver, "_resolve", resolve)


def test_is_public_url(monkeypatch):
    from tracker.worker.resolver import is_public_url

    _fake_dns(monkeypatch, {
        "shop.example": ["93.184.215.14"], "garage": ["172.18.0.3"],
        "127.0.0.1": ["127.0.0.1"], "169.254.169.254": ["169.254.169.254"],
        "mixed.example": ["93.184.215.14", "10.0.0.5"], "v6.example": ["::ffff:10.0.0.1"],
    })
    assert is_public_url("https://shop.example/offer") is True
    assert is_public_url("http://garage:3903/health") is False                 # Compose service
    assert is_public_url("http://127.0.0.1:8000/") is False
    assert is_public_url("http://169.254.169.254/latest/meta-data/") is False  # cloud metadata
    assert is_public_url("https://mixed.example/") is False                   # any private address
    assert is_public_url("https://v6.example/") is False                      # IPv4-mapped private
    assert is_public_url("https://unknown.example/") is False
    assert is_public_url("ftp://shop.example/") is False
    assert is_public_url("not a url") is False


def test_fetch_checks_every_redirect_hop(monkeypatch):
    from tracker.worker import resolver

    _fake_dns(monkeypatch, {"shop.example": ["93.184.215.14"], "internal": ["10.0.0.5"]})

    class Resp:
        def __init__(self, status, location=None):
            self.status_code, self.headers = status, ({"location": location} if location else {})

    requested = []
    routes = {
        "https://shop.example/lp": Resp(302, "/final"),
        "https://shop.example/final": Resp(200),
        "https://shop.example/evil": Resp(302, "http://internal/admin"),
        "https://shop.example/loop": Resp(302, "/loop"),
    }
    monkeypatch.setattr(resolver, "_get", lambda url: requested.append(url) or routes[url])

    assert resolver._fetch("https://shop.example/lp").status_code == 200
    assert requested == ["https://shop.example/lp", "https://shop.example/final"]

    requested.clear()
    with pytest.raises(resolver.BlockedDestination):
        resolver._fetch("https://shop.example/evil")
    assert requested == ["https://shop.example/evil"]              # the internal hop is never requested

    with pytest.raises(resolver.BlockedDestination):
        resolver._fetch("http://internal/")
    with pytest.raises(resolver.BlockedDestination):
        resolver._fetch("https://shop.example/loop")


# ── run state ───────────────────────────────────────────────────────────────

def test_run_state():
    from tracker.db.admin import _run_state

    open_run = {"finished_at": None, "crashed": False}
    stalled = {"finished_at": None, "crashed": True}
    done = {"finished_at": "2026-09-14", "crashed": False}
    assert _run_state(False, False, None) == "idle"
    assert _run_state(False, False, done) == "idle"
    assert _run_state(True, False, done) == "queued"
    assert _run_state(False, True, done) == "queued"       # worker took the flag, run not open yet
    assert _run_state(True, True, open_run) == "running"   # an open run wins
    assert _run_state(False, False, stalled) == "stalled"
    assert _run_state(True, False, stalled) == "queued"     # a request can replace a stalled run


# ── duration math ───────────────────────────────────────────────────────────

def test_calc_duration_days():
    from tracker.worker.persist import calc_duration_days

    now = datetime.now(timezone.utc)
    assert calc_duration_days(now - timedelta(days=10), now - timedelta(days=3), False) == 7
    assert calc_duration_days(None, None, True) is None
    active = calc_duration_days(now - timedelta(days=5), None, True)  # active -> up to now
    assert active is not None and active >= 4
    assert calc_duration_days("2020-01-01T00:00:00Z", "2020-01-11T00:00:00Z", False) == 10


# ── media-fallback SSRF allowlist ───────────────────────────────────────────

def test_allowed_media_fallback():
    from tracker.web.deps import _allowed_media_fallback

    assert _allowed_media_fallback("https://scontent.xx.fbcdn.net/v/a.jpg") is True
    assert _allowed_media_fallback("https://video.fbcdn.net/x.mp4") is True
    assert _allowed_media_fallback("https://z.cdninstagram.com/x.jpg") is True
    assert _allowed_media_fallback("http://scontent.fbcdn.net/x.jpg") is False   # not https
    assert _allowed_media_fallback("https://evil.com/x") is False
    assert _allowed_media_fallback("https://localhost/x") is False
    assert _allowed_media_fallback("https://fbcdn.net.evil.com/x") is False       # suffix spoof
    assert _allowed_media_fallback("not-a-url") is False
    assert _allowed_media_fallback("") is False


def test_media_fallback_checks_redirects_before_following_them():
    import httpx

    from tracker.web.routes.media import _open_fallback

    requested = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "https://video.fbcdn.net/ok.mp4"})
        if request.url.path == "/escape":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})
        return httpx.Response(200, content=b"media")

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        resp = _open_fallback(client, "https://www.facebook.com/hop", None)
        assert resp.status_code == 200
        assert requested == ["https://www.facebook.com/hop", "https://video.fbcdn.net/ok.mp4"]

        requested.clear()
        assert _open_fallback(client, "https://www.facebook.com/escape", None) is None
        assert requested == ["https://www.facebook.com/escape"]   # the internal hop is never requested


# ── duration tiers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("days,tier_class", [
    (0, "new"), (7, "new"), (8, "test"), (29, "test"),
    (30, "win"), (59, "win"), (60, "gold"), (89, "gold"),
    (90, "ever"), (365, "ever"),
])
def test_tier_ramp_boundaries(days, tier_class):
    from tracker.web.deps import _tier

    # assert the stable class token (the CSS/data contract), not the display label
    assert _tier(days)[0] == tier_class


# ── open-redirect guard ─────────────────────────────────────────────────────

def test_sanitize_next():
    from tracker.web.deps import _sanitize_next

    assert _sanitize_next("/dashboard") == "/dashboard"
    assert _sanitize_next("//evil.com") == ""
    assert _sanitize_next("http://evil.com") == ""
    assert _sanitize_next("") == ""
    assert _sanitize_next(None) == ""


# ── image compression ───────────────────────────────────────────────────────

def test_compress_image_shrinks_and_resizes():
    pytest.importorskip("PIL")
    import io
    import random

    from PIL import Image

    from tracker.worker.persist import _compress_image

    im = Image.new("RGB", (2000, 1500))
    im.putdata([(random.randint(0, 255),) * 3 for _ in range(2000 * 1500)])
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    raw = buf.getvalue()

    result = _compress_image(raw, "test")
    assert result is not None
    comp, content_type = result
    assert content_type == "image/webp"
    assert len(comp) < len(raw)
    assert Image.open(io.BytesIO(comp)).width == 1280  # capped


# ── config (secrets from env or compose secret files) ───────────────────────

def test_read_secret_from_env_or_file(tmp_path, monkeypatch):
    from tracker.config import read_secret

    monkeypatch.delenv("DEMO_SECRET", raising=False)
    monkeypatch.delenv("DEMO_SECRET_FILE", raising=False)
    assert read_secret("DEMO_SECRET") == ""
    secret_file = tmp_path / "demo_secret"
    secret_file.write_text("from-file\n")
    monkeypatch.setenv("DEMO_SECRET_FILE", str(secret_file))
    assert read_secret("DEMO_SECRET") == "from-file"                  # compose secret, trimmed
    monkeypatch.setenv("DEMO_SECRET", "from-env")
    assert read_secret("DEMO_SECRET") == "from-env"                   # the variable wins
    monkeypatch.delenv("DEMO_SECRET")
    monkeypatch.setenv("DEMO_SECRET_FILE", str(tmp_path / "missing"))
    assert read_secret("DEMO_SECRET") == ""


def test_database_url_from_compose_parts(tmp_path, monkeypatch):
    from tracker.config import database_url

    for name in ("DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_SSLMODE"):
        monkeypatch.delenv(name, raising=False)
    assert database_url() == ""
    password = tmp_path / "postgres_password"
    password.write_text("p@ss/word")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(password))
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_USER", "tracker")
    monkeypatch.setenv("POSTGRES_DB", "tracker")
    assert database_url() == "postgresql://tracker:p%40ss%2Fword@postgres:5432/tracker"
    monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")
    assert database_url() == "postgresql://tracker:p%40ss%2Fword@db.example.com:6543/tracker?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@other:5432/x")
    assert database_url() == "postgresql://u:p@other:5432/x"          # an explicit URL wins


# ── bundled Garage setup ────────────────────────────────────────────────────

class _FakeGarage:
    """In-memory stand-in for Garage's admin API v2, as far as garage_init uses it."""

    def __init__(self):
        self.layout_version = 0
        self.keys: dict[str, str] = {}
        self.buckets: dict[str, str] = {}   # alias -> id
        self.grants: set[tuple[str, str]] = set()
        self.calls: list[str] = []

    def handler(self, request):
        import httpx

        path = request.url.path
        self.calls.append(path)
        params = request.url.params
        body = json.loads(request.content) if request.content else {}
        if path == "/health":
            return httpx.Response(200 if self.layout_version else 503)
        if path == "/v2/GetClusterStatus":
            return httpx.Response(200, json={"layoutVersion": self.layout_version,
                                             "nodes": [{"id": "node1" * 8}]})
        if path == "/v2/UpdateClusterLayout":
            return httpx.Response(200, json={})
        if path == "/v2/ApplyClusterLayout":
            self.layout_version = body["version"]
            return httpx.Response(200, json={})
        if not self.layout_version:
            return httpx.Response(500, json={"message": "Layout not ready"})
        if path == "/v2/GetKeyInfo":
            if params["id"] not in self.keys:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"accessKeyId": params["id"],
                                             "secretAccessKey": self.keys[params["id"]]})
        if path == "/v2/ImportKey":
            self.keys[body["accessKeyId"]] = body["secretAccessKey"]
            return httpx.Response(200, json={})
        if path == "/v2/GetBucketInfo":
            if params["globalAlias"] not in self.buckets:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"id": self.buckets[params["globalAlias"]]})
        if path == "/v2/CreateBucket":
            self.buckets[body["globalAlias"]] = "bucket-id"
            return httpx.Response(200, json={"id": "bucket-id"})
        if path == "/v2/AllowBucketKey":
            self.grants.add((body["bucketId"], body["accessKeyId"]))
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    def client(self):
        import httpx

        return httpx.Client(base_url="http://garage:3903", transport=httpx.MockTransport(self.handler))


def test_garage_init_sets_up_a_new_node_and_is_idempotent():
    from tracker.garage_init import configure

    garage = _FakeGarage()
    configure(garage.client(), "tracker_media", "s" * 40, "ads-media", delay=0)
    assert garage.layout_version == 1
    assert garage.keys == {"tracker_media": "s" * 40}
    assert garage.grants == {("bucket-id", "tracker_media")}

    garage.calls.clear()
    configure(garage.client(), "tracker_media", "s" * 40, "ads-media", delay=0)
    assert "/v2/ApplyClusterLayout" not in garage.calls
    assert "/v2/ImportKey" not in garage.calls
    assert "/v2/CreateBucket" not in garage.calls


def test_garage_init_refuses_a_key_with_a_different_secret():
    from tracker.garage_init import GarageError, configure

    garage = _FakeGarage()
    configure(garage.client(), "tracker_media", "s" * 40, "ads-media", delay=0)
    with pytest.raises(GarageError, match="different secret"):
        configure(garage.client(), "tracker_media", "t" * 40, "ads-media", delay=0)


# ── CSV export ──────────────────────────────────────────────────────────────

def test_csv_stream_neutralises_formulas_and_opens_in_excel():
    import csv
    import io

    from tracker.web.routes.export import csv_stream

    rows = iter([
        {"a": "=HYPERLINK(\"http://x\")", "b": "-10% off, today", "c": True},
        {"a": "plain, with comma", "b": "Promoção", "c": 3},
    ])
    data = b"".join(csv_stream(["a", "b", "c"], rows))
    assert data.startswith("\ufeff".encode())                 # BOM: Excel reads UTF-8
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert parsed[0] == ["a", "b", "c"]
    assert parsed[1] == ["'=HYPERLINK(\"http://x\")", "'-10% off, today", "true"]
    assert parsed[2] == ["plain, with comma", "Promoção", "3"]


# ── media mirroring (streamed to disk, capped) ──────────────────────────────

class _FakeResponse:
    def __init__(self, body: bytes, content_type: str, declared_length: bool = True):
        self._body = body
        self.headers = {"content-type": content_type}
        if declared_length:
            self.headers["content-length"] = str(len(body))
        self.closed = False
        self.read_whole_body = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=None):
        for i in range(0, len(self._body), chunk_size or 1024):
            yield self._body[i:i + (chunk_size or 1024)]

    @property
    def content(self):  # the mirror must never read the whole body at once
        self.read_whole_body = True
        return self._body

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, **kw):
        self.calls.append(kw)
        return self.resp


def test_mirror_streams_video_to_disk_and_uploads_the_file(monkeypatch):
    from tracker import storage
    from tracker.worker import persist

    uploads = []
    monkeypatch.setattr(persist, "_compress_video", lambda src, label: None)
    monkeypatch.setattr(storage, "upload_file",
                        lambda key, path, ct: uploads.append((key, path.read_bytes(), ct)))
    monkeypatch.setattr(storage, "upload_bytes", lambda *a: (_ for _ in ()).throw(AssertionError("in memory")))
    resp = _FakeResponse(b"v" * 700_000, "video/mp4")
    session = _FakeSession(resp)

    key = persist.mirror_media(session, "https://video.fbcdn.net/x.mp4", "ads/1/0_video")
    assert key == "ads/1/0_video.mp4"
    assert uploads == [("ads/1/0_video.mp4", b"v" * 700_000, "video/mp4")]
    assert session.calls[0]["stream"] is True
    assert resp.closed and not resp.read_whole_body


def test_mirror_skips_media_over_the_cap(monkeypatch):
    from tracker import storage
    from tracker.worker import persist

    monkeypatch.setattr(persist, "MAX_VIDEO_BYTES", 1000)
    monkeypatch.setattr(storage, "upload_file", lambda *a: (_ for _ in ()).throw(AssertionError("uploaded")))
    # refused from the declared length, before reading
    big = _FakeResponse(b"v" * 5000, "video/mp4")
    assert persist.mirror_media(_FakeSession(big), "https://video.fbcdn.net/x.mp4", "k") is None
    assert big.closed
    # a missing or lying length is caught while streaming
    unknown = _FakeResponse(b"v" * 5000, "video/mp4", declared_length=False)
    assert persist.mirror_media(_FakeSession(unknown), "https://video.fbcdn.net/x.mp4", "k") is None
    assert unknown.closed


# ── Normalized ad: both Collection sources give the same Ad ─────────────────
# tests/fixtures/meta_records.json: one Ad Library record per display format
# (DPA, CAROUSEL, DCO, VIDEO), as the Apify Actor returns them (anonymized).

def _meta_records():
    from pathlib import Path

    return json.loads((Path(__file__).parent / "fixtures" / "meta_records.json")
                      .read_text(encoding="utf-8"))


def _as_library_ad(record):
    """What the Self-hosted source yields for the same record: meta-ads-collector
    copies the snapshot keys to the top level (keeping `snapshot`) and parses it."""
    from meta_ads_collector.models import Ad

    flat = dict(record)
    for key, value in (record.get("snapshot") or {}).items():
        flat.setdefault(key, value)
    return Ad.from_graphql_response(flat)


@pytest.mark.parametrize("fmt", ["DPA", "CAROUSEL", "DCO", "VIDEO"])
def test_both_sources_normalize_to_the_same_ad(fmt):
    from tracker.worker.normalize import from_apify_item, from_library_ad

    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == fmt)
    assert from_library_ad(_as_library_ad(record)) == from_apify_item(record)


def test_normalized_ad_keeps_what_the_library_drops():
    from tracker.worker.normalize import from_apify_item

    records = {r["snapshot"]["display_format"]: r for r in _meta_records()}
    dpa = from_apify_item(records["DPA"])
    assert dpa.display_format == "DPA" and len(dpa.creatives) == 6
    assert dpa.page.profile_picture_url and dpa.page.profile_uri
    assert dpa.page.like_count and dpa.page.categories
    assert dpa.categories == ["UNKNOWN"]              # top-level stays on the Ad
    assert all(c.caption for c in dpa.creatives)
    assert dpa.delivery_start_time.tzinfo is not None  # epoch read as UTC
    # a card-less ad: one creative from the ad-level fields
    video = from_apify_item(records["VIDEO"])
    assert video.display_format == "VIDEO" and len(video.creatives) == 1
    assert video.creatives[0].video_url and video.creatives[0].body


def test_library_ad_without_raw_data_falls_back_to_its_model():
    from tracker.worker.normalize import from_library_ad

    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "DPA")
    ad = _as_library_ad(record)
    ad.raw_data = None
    norm = from_library_ad(ad)
    assert norm.id == record["ad_archive_id"] and len(norm.creatives) == 6
    assert norm.display_format is None                # only the raw record has it


def test_card_key_survives_cdn_host_and_expiring_query():
    from tracker.worker.normalize import NormalizedCreative, card_key

    a = NormalizedCreative(
        image_url="https://scontent.fcmn1-1.fna.fbcdn.net/v/t39.35426-6/111_222_333_n.jpg?oe=6AB1B1C8&oh=x",
        link_url="https://shop.example/p?utm_source=fb#top")
    b = NormalizedCreative(
        image_url="https://scontent.faju7-1.fna.fbcdn.net/v/t39.35426-6/111_222_333_n.jpg?oe=6AB2FFFF&oh=y",
        link_url="https://shop.example/p?utm_source=ig")
    assert card_key(a) == card_key(b) == "111_222_333_n|https://shop.example/p"
    # a video card is keyed by its preview image
    v = NormalizedCreative(video_url="https://video.xx.fbcdn.net/o1/v/AQN1.mp4?x=1",
                           thumbnail_url="https://scontent.xx.fbcdn.net/v/t15/999_n.jpg?oe=1")
    assert card_key(v).startswith("999_n|")


def test_every_card_of_an_ad_gets_its_own_key():
    from tracker.worker.normalize import from_apify_item

    for record in _meta_records():
        keys = [c.card_key for c in from_apify_item(record).creatives]
        assert all(keys) and len(set(keys)) == len(keys), record["snapshot"]["display_format"]
    # the same media and destination twice: numbered, not merged
    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "DPA")
    record["snapshot"]["cards"].append(dict(record["snapshot"]["cards"][0]))
    keys = [c.card_key for c in from_apify_item(record).creatives]
    assert keys[-1] == keys[0] + "#1"


def test_card_images_are_the_full_size_upload():
    from tracker.worker.normalize import from_apify_item

    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "CAROUSEL")
    for card, creative in zip(record["snapshot"]["cards"], from_apify_item(record).creatives):
        assert creative.image_url == card["original_image_url"] != card["resized_image_url"]


def test_an_active_ad_has_no_end_date():
    import copy

    from tracker.worker.normalize import from_apify_item

    record = next(r for r in _meta_records() if r["snapshot"]["display_format"] == "DPA")
    assert record["is_active"] and record["end_date"]     # Meta dates it the day it was read
    assert from_apify_item(record).delivery_stop_time is None
    ended = copy.deepcopy(record)
    ended["is_active"] = False
    assert from_apify_item(ended).delivery_stop_time is not None


# ── Apify source: Batches ───────────────────────────────────────────────────

def test_pages_go_in_batches_of_one_country_and_at_most_five():
    from tracker.worker.collector import actor_input, make_batches

    pages = [{"page_id": str(i), "collect_country": "US" if i in (3, 7) else None} for i in range(12)]
    batches = make_batches(pages, "br")
    assert [(c, [p["page_id"] for p in g]) for c, g in batches] == [
        ("BR", ["0", "1", "2", "4", "5"]), ("BR", ["6", "8", "9", "10", "11"]), ("US", ["3", "7"])]
    # a Batch is one Ad Library search per Page, which is what the Actor takes
    run_input = actor_input("US", batches[2][1])
    assert [u["url"] for u in run_input["urls"]] == [
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
        f"&country=US&view_all_page_id={page}" for page in ("3", "7")]
    assert (run_input["maxItems"], run_input["activeStatus"], run_input["sortBy"],
            run_input["fetchDetails"], run_input["maxConcurrency"]) == (0, "active", "impressions", False, 2)
    assert actor_input("BR", batches[0][1])["maxConcurrency"] == 5   # never above the Actor's cap


def test_a_refused_run_is_not_a_successful_one():
    from tracker.worker.collector import refusal

    refused = {"status": "SUCCEEDED", "statusMessage":
               "Run refused. You entered 5 page IDs; this Actor takes 1 per run."}
    assert refusal(refused).startswith("Run refused")
    assert refusal({"status": "SUCCEEDED", "statusMessage": "Finished: 173 ads scraped"}) == ""
    assert refusal({"status": "SUCCEEDED"}) == ""


# ── Canary, Apify source ────────────────────────────────────────────────────

def test_canary_checks_the_apify_token_and_actor(monkeypatch):
    from tracker import apify
    from tracker.worker import canary

    assert canary.check_apify(None)[0] is False

    calls = []
    monkeypatch.setattr(apify, "account_username", lambda t: calls.append("me") or "someone")
    monkeypatch.setattr(apify, "check_actor", lambda t: calls.append("actor") or "owner/actor")
    ok, line = canary.check_apify("tok")
    assert ok and "someone" in line and calls == ["me", "actor"]

    for reason in ("unauthorized", "not_found", "unreachable"):
        def fail(t, reason=reason):
            raise apify.ApifyError(reason)
        monkeypatch.setattr(apify, "check_actor", fail)
        ok, line = canary.check_apify("tok")
        assert not ok and line.startswith("BROKEN") and "tok" not in line.replace("token", "")


def test_canary_in_apify_mode_starts_no_collection(monkeypatch, capsys):
    from tracker.worker import canary

    monkeypatch.setattr(canary, "instance_source", lambda: ("apify", "tok"))
    monkeypatch.setattr(canary, "check_apify", lambda token: (True, "healthy: ok"))
    monkeypatch.setattr(canary, "first_ad", lambda *a: (_ for _ in ()).throw(AssertionError("scraped")))
    assert canary.main() == 0 and "healthy" in capsys.readouterr().out


# ── Showing an ad: cards vs Formats, catalog templates ──────────────────────

def test_clean_text_drops_templates_and_blanks():
    from tracker.db.ads import clean_text

    assert clean_text("{{product.name}}") == ""
    assert clean_text("  ") == "" and clean_text(None) == ""
    assert clean_text(" Real copy ") == "Real copy"
    assert clean_text("Use {braces} freely") == "Use {braces} freely"


def test_display_format_decides_cards_or_formats():
    from tracker.db.ads import ad_kind, shows_cards

    same = [{"title": "A", "body": "x", "link_url": "u", "cta_text": ""}] * 3
    different = [{"title": "A"}, {"title": "B"}]
    assert shows_cards("CAROUSEL", same) and shows_cards("DPA", same) and shows_cards("DCO", same)
    assert not shows_cards("IMAGE", different)       # an image ad in several sizes: Formats
    assert not shows_cards("DPA", same[:1])          # a single card is just the ad
    assert shows_cards(None, different) and not shows_cards(None, same)   # no display format
    assert (ad_kind("DPA", "carousel"), ad_kind("DCO", "carousel"),
            ad_kind("CAROUSEL", "carousel"), ad_kind(None, "video")) == (
        "catalog", "dynamic", "carousel", "video")


def test_compact_like_counts():
    from tracker.web.routes.ads import compact_number

    assert compact_number(39_538_110, "en") == "39.5M"
    assert compact_number(39_538_110, "pt") == "39,5M"
    assert compact_number(16_153, "en") == "16.2K" and compact_number(284, "en") == "284"
    assert compact_number(2_000_000, "es") == "2M" and compact_number(None, "en") == ""


def test_apify_run_cost_follows_the_pricing_model():
    from tracker.apify import run_cost_usd

    prices = {"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {"actorChargeEvents": {
        "apify-default-dataset-item": {"eventPriceUsd": 0.00025},
        "apify-actor-start": {"eventPriceUsd": 0.005}}}}
    # someone else's pay-per-event Actor: the events, not the platform usage
    run = {"pricingInfo": prices, "usageTotalUsd": 0.0035,
           "accountedChargedEventCounts": {"apify-default-dataset-item": 173, "apify-actor-start": 1}}
    assert run_cost_usd(run) == pytest.approx(173 * 0.00025 + 0.005)
    # your own pay-per-event Actor: nothing accounted, you pay the usage
    run["accountedChargedEventCounts"] = {"apify-default-dataset-item": 0, "apify-actor-start": 0}
    assert run_cost_usd(run) == pytest.approx(0.0035)
    # a usage-billed Actor
    assert run_cost_usd({"usageTotalUsd": 0.02}) == pytest.approx(0.02)
    assert run_cost_usd({}) == 0


# ── Media URLs from collected data stay on Meta's CDN ───────────────────────

class _RedirectResponse(_FakeResponse):
    def __init__(self, location):
        super().__init__(b"", "text/html")
        self.status_code, self.headers["location"] = 302, location


class _ScriptedSession:
    """Answers each GET with the next scripted response and records the URLs."""
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []

    def get(self, url, **kw):
        assert kw.get("allow_redirects") is False       # every hop is checked by us
        self.urls.append(url)
        return self.responses.pop(0)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/x.jpg",   # cloud metadata
    "http://garage:3903/v1/status.jpg",                # a service next to the worker
    "https://127.0.0.1/x.jpg",
    "http://scontent.xx.fbcdn.net/x.jpg",              # Meta, but not https
    "https://fbcdn.net.evil.example/x.jpg",
])
def test_mirror_never_fetches_outside_metas_cdn(url, monkeypatch):
    from tracker import storage
    from tracker.worker import persist

    monkeypatch.setattr(storage, "upload_bytes", lambda *a: (_ for _ in ()).throw(AssertionError("uploaded")))
    session = _ScriptedSession()
    assert persist.mirror_media(session, url, "ads/1/x_image") is None
    assert session.urls == []                          # nothing requested


def test_mirror_checks_every_redirect_before_following_it(monkeypatch):
    from tracker import storage
    from tracker.worker import persist

    uploads = []
    monkeypatch.setattr(storage, "upload_bytes", lambda key, data, ct: uploads.append(key))
    monkeypatch.setattr(persist, "_compress_image", lambda data, label, quality="standard": None)
    # a redirect out of the CDN is refused before it is requested
    session = _ScriptedSession(_RedirectResponse("http://10.0.0.5/admin.jpg"))
    assert persist.mirror_media(session, "https://scontent.xx.fbcdn.net/a.jpg", "k") is None
    assert session.urls == ["https://scontent.xx.fbcdn.net/a.jpg"] and uploads == []
    # a redirect within the CDN is followed
    session = _ScriptedSession(_RedirectResponse("https://scontent.yy.fbcdn.net/b.jpg"),
                               _FakeResponse(b"img", "image/jpeg"))
    assert persist.mirror_media(session, "https://scontent.xx.fbcdn.net/a.jpg", "k") == "k.jpg"
    assert session.urls[-1] == "https://scontent.yy.fbcdn.net/b.jpg"


def test_mirror_reports_a_file_the_storage_refused(monkeypatch):
    from tracker import storage
    from tracker.worker import persist

    def refuse(*a):
        raise RuntimeError("AccessDenied")

    monkeypatch.setattr(storage, "upload_bytes", refuse)
    monkeypatch.setattr(persist, "_compress_image", lambda data, label, quality="standard": None)
    session = _ScriptedSession(_FakeResponse(b"img", "image/jpeg"))
    with pytest.raises(persist.StoreFailed, match="AccessDenied"):
        persist.mirror_media(session, "https://scontent.xx.fbcdn.net/a.jpg", "k")


def test_links_from_collected_data_are_only_http():
    from tracker.web.deps import safe_href

    assert safe_href("https://shop.example/p?x=1") == "https://shop.example/p?x=1"
    assert safe_href(" http://shop.example ") == "http://shop.example"
    for bad in ("javascript:alert(document.domain)", " JavaScript:alert(1)", "data:text/html,<b>x</b>",
                "vbscript:x", "//evil.example", "/admin", "", None):
        assert safe_href(bad) == "", bad


def test_media_urls_from_other_hosts_never_reach_the_page():
    from tracker.db.base import resolve_media_url

    meta = "https://scontent.xx.fbcdn.net/a.jpg"
    assert resolve_media_url(meta, None) == meta
    assert resolve_media_url("https://tracker.evil.example/pixel.gif", None) is None
    assert resolve_media_url("https://tracker.evil.example/p.gif", "ads/1/k.webp") == "/media/ads/1/k.webp"
    assert resolve_media_url(meta, "ads/1/k.webp").startswith("/media/ads/1/k.webp?fallback=https%3A")


def test_example_collection_cost():
    from tracker.web.routes.help import collection_cost_usd

    # 500 ads at US$0.25/1,000 plus one Actor start for up to 5 Pages
    assert collection_cost_usd(500, 5) == pytest.approx(0.13)
    assert collection_cost_usd(500, 6) == pytest.approx(0.135)      # two Batches


def test_static_urls_change_with_the_file():
    from tracker.web.deps import static_url

    url = static_url("css/app.css")
    assert url.startswith("/static/css/app.css?v=") and len(url.split("v=")[1]) == 10
    assert static_url("css/missing.css") == "/static/css/missing.css"


def test_image_quality_presets():
    import io

    from PIL import Image

    from tracker.worker.persist import _compress_image

    buf = io.BytesIO()
    Image.effect_noise((3000, 1500), 60).convert("RGB").save(buf, format="PNG")
    widths = {}
    for quality in ("standard", "high"):
        data, content_type = _compress_image(buf.getvalue(), "t", quality)
        widths[quality] = Image.open(io.BytesIO(data)).width
        assert content_type == "image/webp"
    assert widths == {"standard": 1280, "high": 2048}
