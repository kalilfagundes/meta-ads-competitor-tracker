"""S3 media proxy: streams mirrored objects, with a guarded CDN fallback.

Bodies are streamed in chunks, never held in memory whole, and HTTP Range
requests are honoured (browsers fetch video in parts), so a video costs one
chunk of memory per request, whatever its size.
"""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from ... import storage
from ..deps import _allowed_media_fallback, _authed

router = APIRouter()

CHUNK_SIZE = 64 * 1024
MAX_REDIRECTS = 5
_RANGE_RE = re.compile(r"^bytes=(\d+-\d*|-\d+)$")


def _byte_range(request: Request) -> str | None:
    """The request's single byte range, if it has a well-formed one."""
    value = request.headers.get("range", "").strip()
    return value if _RANGE_RE.match(value) else None


def _open_fallback(client: httpx.Client, url: str, byte_range: str | None) -> httpx.Response | None:
    """Follow redirects by hand, so every hop is checked BEFORE it is requested:
    a redirect must not lead the proxy away from Meta's CDN."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _allowed_media_fallback(url):
            return None
        request = client.build_request("GET", url, headers={"range": byte_range} if byte_range else None)
        resp = client.send(request, stream=True)
        if not resp.is_redirect:
            return resp
        resp.close()
        url = str(resp.next_request.url) if resp.next_request else ""
    return None


def _stream_fallback(url: str, byte_range: str | None) -> Response | None:
    """Stream Meta's original media (the mirrored copy is missing), or None."""
    client = httpx.Client(timeout=30, follow_redirects=False)
    try:
        resp = _open_fallback(client, url, byte_range)
    except Exception:
        client.close()
        return None
    if resp is None or resp.status_code not in (200, 206):
        if resp is not None:
            resp.close()
        client.close()
        return None

    def body():
        try:
            yield from resp.iter_raw(CHUNK_SIZE)
        finally:
            resp.close()
            client.close()

    headers = {"cache-control": "private, max-age=86400"}
    for name in ("content-length", "content-range", "content-encoding", "accept-ranges"):
        if name in resp.headers:
            headers[name] = resp.headers[name]
    return StreamingResponse(
        body(), status_code=resp.status_code, headers=headers,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
    )


@router.get("/media/{key:path}")
def media(key: str, request: Request, fallback: str | None = None):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    byte_range = _byte_range(request)
    try:
        obj = storage.open_object(key, byte_range)
    except storage.RangeNotSatisfiable:
        return Response(status_code=416, headers={"content-range": "bytes */*"})
    except Exception:
        # A storage outage must not 500 the page: fall through to the CDN fallback.
        obj = None

    if obj is None:
        if fallback and _allowed_media_fallback(fallback):
            if resp := _stream_fallback(fallback, byte_range):
                return resp
        return Response("Not found", status_code=404)

    headers = {"cache-control": "private, max-age=31536000, immutable", "accept-ranges": "bytes"}
    if obj.content_length is not None:
        headers["content-length"] = str(obj.content_length)
    if obj.content_range:
        headers["content-range"] = obj.content_range
    return StreamingResponse(
        obj.chunks(CHUNK_SIZE), status_code=206 if obj.content_range else 200, headers=headers,
        media_type=obj.content_type or "application/octet-stream",
    )
