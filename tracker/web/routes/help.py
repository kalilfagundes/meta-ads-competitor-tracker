"""Help pages. Open without signing in: the setup wizard links here before
collection is configured, and nothing on them is instance data."""

from __future__ import annotations

from babel.numbers import format_decimal
from fastapi import APIRouter, Request

from ... import apify
from ..deps import request_lang, templates

router = APIRouter(prefix="/help")

# The worked example on the page: 5 competitors with about 100 active ads each.
EXAMPLE_PAGES = 5
EXAMPLE_ADS = 500


def collection_cost_usd(ads: int, pages: int) -> float:
    """What one collection of `ads` ads from `pages` Pages costs on Apify's free
    plan: the ads, plus one Actor start per Batch of up to 5 Pages."""
    batches = -(-pages // 5)
    return ads * apify.PRICE_PER_1000_ADS_USD / 1000 + batches * apify.RUN_START_USD


def pricing(lang: str) -> dict:
    """The Apify prices and the worked example, formatted for `lang`. Also shown
    next to the Apify option in the setup wizard and Settings."""

    def num(amount: float, pattern: str = "#,##0.00") -> str:
        return format_decimal(amount, format=pattern, locale=lang)

    per_run = collection_cost_usd(EXAMPLE_ADS, EXAMPLE_PAGES)
    return {
        "price_1000": num(apify.PRICE_PER_1000_ADS_USD),
        "run_start": num(apify.RUN_START_USD, "0.000"),
        "free_credit": num(apify.FREE_PLAN_CREDIT_USD),
        "free_ads": num(apify.FREE_PLAN_CREDIT_USD / apify.PRICE_PER_1000_ADS_USD * 1000, "#,##0"),
        "per_run": num(per_run),
        "daily_month": num(per_run * 30),
        "six_hours_month": num(per_run * 30 * 4),
    }


def source_pricing(lang: str) -> dict:
    """What the Collection source choice (_source_fields.html) quotes."""
    p = pricing(lang)
    return {"apify_price_1000": p["price_1000"], "apify_free_credit": p["free_credit"]}


@router.get("/apify")
def help_apify(request: Request):
    return templates.TemplateResponse(request, "help_apify.html", {
        "actor_url": apify.ACTOR_URL,
        "signup_url": apify.SIGNUP_URL,
        "token_url": apify.TOKEN_URL,
        "example_pages": EXAMPLE_PAGES,
        "example_ads": EXAMPLE_ADS,
        **pricing(request_lang(request)),
    })
