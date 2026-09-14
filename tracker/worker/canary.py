"""Collection canary: can we still read the Meta Ad Library?

Run by hand only; nothing schedules it.

    python -m tracker.worker.canary [PAGE_ID ...]

It checks the instance's Collection source (read from the database; Self-hosted
when the database can't be reached):

  - Self-hosted: collection depends on Meta's public web endpoints, which change
    without notice. One ad proves they still work, so this asks the collector the
    worker uses for a single ad and stops there. The fallback Pages are only tried
    if the first one returns nothing. No storage, no proxies. Run it from the
    server that collects (`docker compose exec worker python -m tracker.worker.canary`):
    Meta blocks many datacenter IPs, so a failure from elsewhere doesn't say much.
  - Apify: checks the saved token and that the Actor is reachable with it. It
    starts no Actor run, so it costs nothing.

Exits 0 when the check passed, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

# Facebook Page IDs of large US advertisers with ads running year-round, tried
# in order. Override with CANARY_PAGE_IDS.
DEFAULT_PAGE_IDS = ["15087023444", "159616034235", "20446254070"]


async def first_ad(page_ids: list[str], country: str) -> tuple[str, str, list[str]]:
    """(page_id, page name) of the first Page that returned an ad ("" if none),
    plus one line per Page that failed."""
    from meta_ads_collector.async_collector import AsyncMetaAdsCollector

    failures = []
    async with AsyncMetaAdsCollector(rate_limit_delay=1.0, jitter=0.5, timeout=30) as collector:
        for page_id in page_ids:
            try:
                async for ad in collector.search(
                    country=country, ad_type="ALL", status="ACTIVE", search_type="PAGE",
                    page_ids=[page_id], max_results=1, page_size=1,
                ):
                    name = getattr(getattr(ad, "page", None), "name", "") or ""
                    return page_id, name, failures
                failures.append(f"page {page_id}: no ads returned")
            except Exception as exc:  # the point is to report, not to crash
                failures.append(f"page {page_id}: {type(exc).__name__}: {exc}")
    return "", "", failures


def instance_source() -> tuple[str, str | None]:
    """The Collection source and Apify token saved in the instance; Self-hosted
    (no token) if the database can't be read."""
    try:
        from .. import db

        settings = db.get_app_settings() or {}
    except Exception:
        return "self_hosted", None
    return settings.get("collect_source") or "self_hosted", settings.get("apify_token")


def check_apify(token: str | None) -> tuple[bool, str]:
    """(ok, the line to print) for the Apify source. Starts no Actor run."""
    from .. import apify

    if not token:
        return False, "BROKEN: collection source is Apify, but no Apify token is saved (Settings)"
    try:
        account = apify.account_username(token)
        actor = apify.check_actor(token)
    except apify.ApifyError as exc:
        why = {"unauthorized": "Apify didn't accept the saved token",
               "not_found": f"Actor {apify.ACTOR_ID} not found for this token",
               }.get(exc.reason, f"couldn't reach Apify ({exc})")
        return False, f"BROKEN: {why}"
    return True, f"healthy: Apify account {account} can run Actor {actor} ({apify.ACTOR_ID})"


def main() -> int:
    source, token = instance_source()
    if source == "apify":
        ok, line = check_apify(token)
        print(line)
        return 0 if ok else 1
    page_ids = sys.argv[1:] or os.environ.get("CANARY_PAGE_IDS", "").split() or DEFAULT_PAGE_IDS
    country = os.environ.get("CANARY_COUNTRY", "US")
    started = time.monotonic()
    page_id, name, failures = asyncio.run(first_ad(page_ids, country))
    for line in failures:
        print(f"FAIL {line}")
    elapsed = f"{time.monotonic() - started:.0f}s"
    if page_id:
        print(f"healthy: got an ad from page {page_id}{f' ({name})' if name else ''}, "
              f"country={country}, {elapsed}")
        return 0
    print(f"BROKEN: no ad from {len(page_ids)} page(s), country={country}, {elapsed}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
