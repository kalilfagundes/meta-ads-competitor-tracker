"""Connection + shared helpers for the data layer.

Every db submodule builds on this: `connect()` for a dict-row connection, plus the
small value helpers used to shape query results.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from ..config import get_settings
from ..media_urls import is_meta_media_url


def _dsn() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("No database configured: set DATABASE_URL (see docs/configuration.md).")
    # psycopg.connect does not accept SQLAlchemy's driver suffix.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def connect(**kwargs) -> psycopg.Connection:
    """New psycopg connection; rows come back as dicts. `kwargs` go to
    `psycopg.connect` (autocommit, connect_timeout, application_name…)."""
    return psycopg.connect(_dsn(), row_factory=dict_row, **kwargs)


def _date_only(v: Any) -> str | None:
    if not v:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    return str(v)[:10]


def _f(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v) if isinstance(v, (int, float)) else None


def resolve_media_url(raw_url: str | None, media_key: str | None) -> str | None:
    """Prefer the durable /media/<key> path (storage) over Meta's CDN URL (which
    expires). The raw URL goes as ?fallback= so the /media route can redirect on a miss.

    The raw URL comes from collected data, so it is used only when it is on Meta's
    CDN over https: a page must not send the viewer's browser to any other host."""
    raw = raw_url if is_meta_media_url(raw_url) else None
    if media_key:
        path = f"/media/{media_key}"
        return f"{path}?fallback={quote(raw, safe='')}" if raw else path
    return raw


def _now_utc_label() -> str:
    return datetime.utcnow().isoformat(timespec="minutes").replace("T", " ") + " UTC"
