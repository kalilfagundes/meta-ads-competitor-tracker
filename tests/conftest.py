"""Shared pytest fixtures.

Unit tests (tests/test_unit.py) need nothing external and always run. The DB and
web tests need a reachable Postgres: set TEST_DATABASE_URL (or DATABASE_URL). If
none is set or it can't be reached, those tests are skipped, not failed — so a
bare `pytest` still runs the unit suite anywhere.

Spin a throwaway Postgres for the full suite:

    docker run -d --name tracker-test-pg -e POSTGRES_USER=tracker \
        -e POSTGRES_PASSWORD=tracker -e POSTGRES_DB=tracker -p 55432:5432 postgres:16
    TEST_DATABASE_URL=postgresql://tracker:tracker@localhost:55432/tracker pytest
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Environment must be set before any `tracker.*` import (config/storage read it).
os.environ.setdefault("SESSION_SECRET", "test-secret-please-keep-32-chars-000")
os.environ.setdefault("SETUP_TOKEN", "test-setup-code")
os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET", "ads-test")
os.environ.setdefault("S3_ACCESS_KEY_ID", "test")
os.environ.setdefault("S3_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("S3_REGION", "auto")
os.environ.setdefault("S3_FORCE_PATH_STYLE", "true")

REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if _TEST_DB_URL:
    os.environ["DATABASE_URL"] = _TEST_DB_URL

# Tables truncated between tests (app_settings is reset separately to keep its row).
_DATA_TABLES = (
    "companies", "pages", "collection_runs", "ads", "landing_pages", "creatives",
    "ad_appearances", "collections", "collection_items",
    "page_snapshots", "users", "proxies",
)


def _db_reachable(url: str) -> bool:
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def _migrated_db():
    """Session-scoped: ensure a reachable, migrated test database (or skip)."""
    if not _TEST_DB_URL:
        pytest.skip("no TEST_DATABASE_URL / DATABASE_URL set — skipping DB/web tests")
    if not _db_reachable(_TEST_DB_URL):
        pytest.skip(f"database not reachable at {_TEST_DB_URL} — skipping DB/web tests")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT), env={**os.environ, "DATABASE_URL": _TEST_DB_URL},
        check=True, capture_output=True,
    )
    return _TEST_DB_URL


@pytest.fixture
def db(_migrated_db):
    """A clean psycopg connection: every test starts with empty tables."""
    from tracker import db as dbmod

    conn = dbmod.connect()
    conn.execute(f"TRUNCATE {', '.join(_DATA_TABLES)} RESTART IDENTITY CASCADE")
    conn.execute(
        """UPDATE app_settings SET collect_interval_hours = 6, collect_country = 'BR', setup_completed = TRUE, language = NULL,
               collect_now = FALSE, collect_now_requested_at = NULL,
               collect_source = 'self_hosted', apify_token = NULL, apify_max_parallel_runs = 2,
               video_quality = 'standard', image_quality = 'standard'
           WHERE id = 1"""
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def seeded(db):
    """The standard dataset (companies, ads incl. carousel/variants/inactive, a
    landing page, admin data). Returns a dict of handy ids/archive-ids."""
    from tests.seed import seed_basic

    ids = seed_basic(db)
    return ids


@pytest.fixture
def client(_migrated_db):
    """FastAPI TestClient with a valid session cookie (authenticated)."""
    from fastapi.testclient import TestClient

    from tracker.auth import SESSION_COOKIE_NAME, create_session_value
    from tracker.config import get_settings
    from tracker.web.app import app

    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE_NAME, create_session_value(get_settings().session_secret))
    return c


@pytest.fixture
def anon_client(_migrated_db):
    """FastAPI TestClient with no session (unauthenticated)."""
    from fastapi.testclient import TestClient

    from tracker.web.app import app

    return TestClient(app)
