"""Users (admin) — login and the first-run wizard."""

from __future__ import annotations

from .base import connect


def count_users() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def get_user_by_username(username: str) -> dict | None:
    with connect() as conn:
        return conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = %s",
            (username,),
        ).fetchone()


def create_first_user(username: str, password_hash: str) -> dict | None:
    """Create the first-run administrator, or return None if a user already exists.
    The table lock makes check-and-insert atomic: two wizard submissions racing each
    other can't both create an admin."""
    with connect() as conn:
        conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")
        return conn.execute(
            """INSERT INTO users (username, password_hash)
               SELECT %s, %s WHERE NOT EXISTS (SELECT 1 FROM users)
               RETURNING id, username""",
            (username, password_hash),
        ).fetchone()


def create_user(username: str, password_hash: str) -> dict:
    with connect() as conn:
        return conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id, username",
            (username, password_hash),
        ).fetchone()
