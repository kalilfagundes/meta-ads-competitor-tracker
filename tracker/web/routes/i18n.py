"""The translation catalog served to the browser scripts."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ... import i18n

router = APIRouter()


@router.get("/i18n/{code}.js")
def js_catalog(code: str, v: str = ""):
    """`window.I18N_CATALOG` for static/js/i18n.js. Public: it's only UI strings."""
    if code not in i18n.LANGUAGES:
        return Response("unknown language", status_code=404)
    # A versioned URL never changes content; an unversioned one may.
    cache = ("public, max-age=31536000, immutable" if v == i18n.catalog_version(code)
             else "public, max-age=300")
    return Response(
        f"window.I18N_CATALOG={i18n.js_catalog(code)};",
        media_type="application/javascript; charset=utf-8",
        headers={"cache-control": cache},
    )
