"""Which media URLs the app may fetch: Meta's CDN, over https.

Media URLs come from collected ad data. With the Apify Collection source that data
is produced by an Actor running outside this instance, so a URL in it can point
anywhere, including this server's own network. Both places that fetch media check
it here: the worker when it mirrors media to storage, and the web app's /media
route when it falls back to the original URL.
"""

from __future__ import annotations

from urllib.parse import urlparse

MEDIA_HOSTS = ("fbcdn.net", "cdninstagram.com", "fbsbx.com", "facebook.com")


def is_meta_media_url(url: str | None) -> bool:
    """An https URL on one of Meta's media hosts (or a subdomain of one)."""
    try:
        p = urlparse(url or "")
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS)
