"""Ad -> landing page linking (no network).

Canonicalizes each creative's `link_url` (strips utm_*/fbclid/gclid/fragment, lowercases the host, no
trailing slash) and maps it to a `landing_pages` row (deduped by canonical URL). 50
ads pointing at the same page become ONE landing page — the basis of the
"by landing page" axis.

Runs after every collection (see `scheduler`), is idempotent and cheap. Fetching
each page's title lives in `resolver.py`.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .. import db

logger = logging.getLogger(__name__)

# Tracking/attribution noise — dropped during canonicalization (utm_* matches by prefix).
# Does NOT include params that might carry offer/discount identity: a false split
# (two landing pages for the same offer) is recoverable; a false merge is not.
_DROP_PARAMS = {
    "fbclid", "gclid", "gbraid", "wbraid", "mc_eid", "mc_cid", "_ga",
    "src", "seller", "source", "ad_id", "adset_id", "campaign_id",
}


def canonicalize_url(raw: str) -> str | None:
    """Canonical form used as the landing-page dedup key (http/https only)."""
    if not raw or not raw.strip():
        return None
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.hostname.lower() if parts.hostname else ""
    if not host:
        return None
    if parts.port and not (
        (parts.scheme == "http" and parts.port == 80)
        or (parts.scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in _DROP_PARAMS
    ]
    query = urlencode(sorted(kept))
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, host, path, query, ""))


def link_creatives(conn) -> int:
    """Assign landing_page_id to every creative that has a destination but no link yet.
    Returns how many were linked."""
    pending = conn.execute(
        """SELECT cr.id AS creative_id, cr.link_url, a.company_id
           FROM creatives cr JOIN ads a ON cr.ad_id = a.id
           WHERE cr.landing_page_id IS NULL
             AND cr.link_url IS NOT NULL AND cr.link_url <> ''""",
    ).fetchall()

    linked = 0
    for row in pending:
        canonical = canonicalize_url(row["link_url"])
        if not canonical:
            continue  # non-http(s) destination; leave it unlinked, costs nothing
        lp = conn.execute(
            """INSERT INTO landing_pages (company_id, url_canonical, url_raw_example)
               VALUES (%s, %s, %s)
               ON CONFLICT (url_canonical) DO UPDATE SET last_seen = now()
               RETURNING id""",
            (row["company_id"], canonical, row["link_url"]),
        ).fetchone()
        conn.execute(
            "UPDATE creatives SET landing_page_id = %s WHERE id = %s",
            (lp["id"], row["creative_id"]),
        )
        linked += 1

    conn.commit()
    return linked


def run_linking() -> int:
    """Open its own connection and link pending creatives. Callable by the scheduler."""
    conn = db.connect()
    try:
        return link_creatives(conn)
    finally:
        conn.close()
