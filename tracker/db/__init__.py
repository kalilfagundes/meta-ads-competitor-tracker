"""Data layer (psycopg, native Postgres over TCP), split by domain.

This package re-exports the flat API so callers use ``db.get_library_page()``,
``db.connect()``, etc. unchanged. Submodules:
  - ``base``        — connection + shared value helpers
  - ``ads``         — ad detail
  - ``library``     — the ads library: filtered, paginated cards; champions
  - ``landing``     — landing pages (catalog / product detail)
  - ``export``      — flat rows for the landing-pages CSV
  - ``collections`` — ad-level favorites
  - ``users``       — login + first-run wizard
  - ``settings``    — operational config read by the worker
  - ``admin``       — companies/pages/proxies/config-write CRUD
"""

from __future__ import annotations

from .base import connect, resolve_media_url
from .ads import _is_real_carousel, get_ad_detail
from .landing import get_catalog_data, get_product_detail
from .export import list_landing_pages_for_export
from .library import (
    LibraryFilters,
    get_champions,
    get_library_companies,
    get_library_page,
    iter_ads_for_export,
)
from .collections import (
    create_collection,
    delete_collection,
    list_collections,
    toggle_collection_item,
)
from .users import count_users, create_first_user, create_user, get_user_by_username
from .settings import (
    STALE_MINUTES,
    consume_collect_now,
    get_app_settings,
    list_enabled_proxies,
    list_tracked_pages,
    request_collect_now,
    update_page_name,
)
from .admin import (
    COLLECT_SOURCES,
    MEDIA_QUALITIES,
    PageEditError,
    complete_setup,
    create_company,
    create_page,
    create_proxy,
    delete_company,
    delete_page,
    delete_proxy,
    get_run_status,
    list_companies_with_pages,
    list_pages,
    list_proxies,
    set_interface_language,
    toggle_page_tracked,
    toggle_proxy_enabled,
    update_app_settings,
    update_collection_source,
    update_company,
    update_media_quality,
    update_page,
)

__all__ = [
    "connect", "resolve_media_url",
    "get_ad_detail", "_is_real_carousel",
    "LibraryFilters", "get_library_page", "get_champions", "get_library_companies",
    "iter_ads_for_export",
    "get_catalog_data", "get_product_detail",
    "list_landing_pages_for_export",
    "list_collections", "create_collection", "delete_collection", "toggle_collection_item",
    "count_users", "get_user_by_username", "create_user", "create_first_user",
    "get_app_settings", "list_enabled_proxies", "list_tracked_pages", "update_page_name",
    "request_collect_now", "consume_collect_now", "STALE_MINUTES",
    "list_pages", "list_companies_with_pages", "create_company", "delete_company",
    "create_page", "toggle_page_tracked", "delete_page", "list_proxies", "create_proxy",
    "toggle_proxy_enabled", "delete_proxy", "update_app_settings",
    "COLLECT_SOURCES", "update_collection_source", "MEDIA_QUALITIES", "update_media_quality",
    "update_company", "update_page", "PageEditError", "get_run_status",
    "complete_setup", "set_interface_language",
]
