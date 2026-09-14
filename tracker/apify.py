"""The Apify side of the Apify Collection source: which Actor, and the calls that
don't start a run (checking a token, checking the Actor is reachable). Used by
Settings, the setup wizard, the canary and the collector.

The token is a secret: it's never logged, never put in an error message (see
`redact`) and never sent back to the browser (see `mask_token`).
"""

from __future__ import annotations

import httpx

API_BASE = "https://api.apify.com/v2"
ACTOR_ID = "SykzLQys0gpl4ZJv4"
ACTOR_NAME = "kalilfagundes.w/facebook-instagram-meta-ads-library-scraper"
# Apify's free plan runs at most this many Actor runs at once.
MAX_PARALLEL_RUNS = 5
DEFAULT_PARALLEL_RUNS = 2

# What the help page (/help/apify) quotes. Prices are the Actor's own, on Apify's
# free plan (paid plans pay a little less), as of 2026-09; the Actor's page has the
# current ones, and every collection run shows what was actually charged.
ACTOR_URL = f"https://apify.com/{ACTOR_NAME}"
SIGNUP_URL = "https://console.apify.com/sign-up"
TOKEN_URL = "https://console.apify.com/settings/integrations"
PRICE_PER_1000_ADS_USD = 0.25
RUN_START_USD = 0.005            # per Actor run, which reads up to 5 Pages
FREE_PLAN_CREDIT_USD = 5         # a month, no credit card, doesn't roll over
_TIMEOUT = 20


class ApifyError(Exception):
    """A call to Apify failed. `reason`: 'unauthorized' (bad token), 'not_found'
    (no such Actor, or not visible to this token) or 'unreachable'."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


def redact(text: str, token: str | None) -> str:
    """`text` with the token removed, for anything that gets stored or logged."""
    return text.replace(token, "***") if token else text


def mask_token(token: str | None) -> str:
    """What the UI shows of a stored token: its last 4 characters."""
    return f"••••{token[-4:]}" if token else ""


def _get(path: str, token: str, *, client: httpx.Client | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if client is not None:
            resp = client.get(f"{API_BASE}{path}", headers=headers, timeout=_TIMEOUT)
        else:
            resp = httpx.get(f"{API_BASE}{path}", headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ApifyError("unreachable", redact(str(exc), token)) from None
    if resp.status_code in (401, 403):
        raise ApifyError("unauthorized")
    if resp.status_code == 404:
        raise ApifyError("not_found")
    if resp.status_code >= 400:
        raise ApifyError("unreachable", f"HTTP {resp.status_code}")
    return resp.json().get("data") or {}


def account_username(token: str, *, client: httpx.Client | None = None) -> str:
    """The Apify account the token belongs to (free: no run is started)."""
    return _get("/users/me", token, client=client).get("username") or ""


def check_actor(token: str, *, client: httpx.Client | None = None) -> str:
    """The Actor's name, if this token can see it (free: no run is started)."""
    data = _get(f"/acts/{ACTOR_ID}", token, client=client)
    return f"{data.get('username')}/{data.get('name')}"


# ── Actor runs (the collector) ──────────────────────────────────────────────

RUN_DONE = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")
_ITEMS_PAGE = 1000


def run_cost_usd(run: dict) -> float:
    """What an Actor run cost the account that started it.

    A pay-per-event Actor bills whoever runs it per event (the events Apify
    accounted to that account, times each event's price) and bills its platform
    usage to its developer. Any other Actor, or your own pay-per-event Actor (no
    events accounted to you), bills the platform usage (`usageTotalUsd`), which
    Apify keeps updating for a few seconds after the run ends."""
    pricing = run.get("pricingInfo") or {}
    prices = (pricing.get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
    events = sum(
        count * float((prices.get(name) or {}).get("eventPriceUsd") or 0)
        for name, count in (run.get("accountedChargedEventCounts") or {}).items()
    )
    if pricing.get("pricingModel") == "PAY_PER_EVENT" and events > 0:
        return events
    return float(run.get("usageTotalUsd") or 0) + events


class ApifyRuns:
    """Starts runs of the Actor, follows them and reads their datasets (async).
    The collector's seam to Apify: tests hand the collector a fake with the same
    methods."""

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._token = token
        self._client = client or httpx.AsyncClient(
            base_url=API_BASE, timeout=60, headers={"Authorization": f"Bearer {token}"})

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            resp = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ApifyError("unreachable", redact(str(exc), self._token)) from None
        if resp.status_code in (401, 403):
            raise ApifyError("unauthorized")
        if resp.status_code == 404:
            raise ApifyError("not_found")
        if resp.status_code >= 400:
            raise ApifyError("unreachable", redact(f"HTTP {resp.status_code}: {resp.text[:200]}",
                                                   self._token))
        return resp

    async def start_run(self, run_input: dict) -> dict:
        resp = await self._call("POST", f"/acts/{ACTOR_ID}/runs", json=run_input)
        return resp.json()["data"]

    async def get_run(self, run_id: str) -> dict:
        return (await self._call("GET", f"/actor-runs/{run_id}")).json()["data"]

    async def dataset_item_count(self, dataset_id: str) -> int:
        """How many items a (possibly still running) Actor run has saved so far.
        Apify updates this figure with a small delay, so it is only for display."""
        data = (await self._call("GET", f"/datasets/{dataset_id}")).json()["data"]
        return int(data.get("itemCount") or 0)

    async def run_summary(self, key_value_store_id: str) -> dict | None:
        """The Actor's own RUN_SUMMARY record: how many ads it scraped and whether
        it stopped early (`stoppedEarlyBecause`). None when the Actor didn't write
        one."""
        try:
            return (await self._call("GET", f"/key-value-stores/{key_value_store_id}"
                                            "/records/RUN_SUMMARY")).json()
        except ApifyError:
            return None

    async def abort_run(self, run_id: str) -> None:
        await self._call("POST", f"/actor-runs/{run_id}/abort")

    async def iter_items(self, dataset_id: str):
        """Every item of a dataset, a page of items at a time."""
        offset = 0
        while True:
            resp = await self._call(
                "GET", f"/datasets/{dataset_id}/items",
                params={"format": "json", "clean": "true", "offset": offset, "limit": _ITEMS_PAGE})
            items = resp.json()
            for item in items:
                yield item
            if len(items) < _ITEMS_PAGE:
                return
            offset += len(items)

    async def aclose(self) -> None:
        await self._client.aclose()
