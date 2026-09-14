"""Migration tests (need a Postgres — see conftest). They move the test database's
schema down and back up, and always leave it at head.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.conftest import REPO_ROOT


def _alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT), env=os.environ.copy(), check=True, capture_output=True,
    )


@pytest.fixture
def at_head(_migrated_db):
    """Put the schema back at head whatever the test did."""
    yield _migrated_db
    _alembic("upgrade", "head")


def test_downgrade_to_base_and_back(at_head):
    import psycopg

    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    with psycopg.connect(at_head) as conn:
        row = conn.execute(
            "SELECT setup_completed, collect_country, collect_interval_hours FROM app_settings"
        ).fetchone()
    # a fresh instance starts with the wizard pending, US, every 6 hours
    assert row == (False, "US", 6)
    with psycopg.connect(at_head) as conn:
        row = conn.execute(
            "SELECT collect_source, apify_token, apify_max_parallel_runs FROM app_settings"
        ).fetchone()
    # ... collecting Self-hosted, with no Apify token
    assert row == ("self_hosted", None, 2)


def test_password_with_special_characters(at_head):
    """The setup script accepts any password; URL-encoded it contains %, which Alembic's
    config parser would otherwise read as interpolation."""
    import psycopg
    from psycopg import sql
    from urllib.parse import quote, urlsplit

    role, password = "tracker_special_pw", "p@ss%w:rd/#$"
    with psycopg.connect(at_head, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT SELECT ON alembic_version TO {}").format(sql.Identifier(role)))
    try:
        parts = urlsplit(at_head)
        netloc = f"{role}:{quote(password, safe='')}@{parts.hostname}:{parts.port or 5432}"
        subprocess.run(
            [sys.executable, "-m", "alembic", "current"],
            cwd=str(REPO_ROOT), env={**os.environ, "DATABASE_URL": parts._replace(netloc=netloc).geturl()},
            check=True, capture_output=True,
        )
    finally:
        with psycopg.connect(at_head, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
