"""Infrastructure/secret configuration, read from the environment.

Only deploy-time settings live here: database, session and S3 storage, written to
`.env` by the setup script (see `.env.example`). OPERATIONAL config (companies/
pages, interval, country, proxies) does NOT belong here — it lives in the database
(`app_settings`/`proxies`), managed through the web UI. See CONTEXT.md.

Each secret is read from the variable itself (`SESSION_SECRET=...`) or, when
`<VAR>_FILE` is set, from that file: docker compose mounts its secrets that way,
under /run/secrets, so they never sit in a container's environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

try:
    # Load .env for local runs (gitignored). Does not override already-set env.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env(name: str, default: str = "") -> str:
    """An env var, treating empty as unset (compose passes `${X:-}` through as "")."""
    value = os.environ.get(name, "")
    return value if value.strip() else default


def read_secret(name: str) -> str:
    """`NAME`, else the contents of the file at `NAME_FILE`; "" if neither is set."""
    if value := _env(name):
        return value
    if path := _env(f"{name}_FILE"):
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def database_url() -> str:
    """`DATABASE_URL`, or one assembled from the `POSTGRES_*` parts (docker compose),
    with the password read as a secret."""
    if url := _env("DATABASE_URL"):
        return url
    host = _env("POSTGRES_HOST")
    if not host:
        return ""
    user = quote(_env("POSTGRES_USER", "tracker"), safe="")
    password = quote(read_secret("POSTGRES_PASSWORD"), safe="")
    auth = f"{user}:{password}" if password else user
    url = f"postgresql://{auth}@{host}:{_env('POSTGRES_PORT', '5432')}/{_env('POSTGRES_DB', 'tracker')}"
    if sslmode := _env("POSTGRES_SSLMODE"):
        url += f"?sslmode={quote(sslmode, safe='')}"
    return url


@dataclass(frozen=True)
class Settings:
    # Database
    database_url: str
    # Session (login cookie signature)
    session_secret: str
    # First-run setup code, asked when creating the admin ("" = generated and logged)
    setup_token: str
    # Object storage (S3-compatible: Garage / AWS S3 / Cloudflare R2 / …)
    s3_endpoint: str          # e.g. http://garage:3900 ; empty = default AWS S3
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str
    s3_force_path_style: bool  # Garage needs path-style; AWS S3 can be False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=database_url(),
            session_secret=read_secret("SESSION_SECRET"),
            setup_token=read_secret("SETUP_TOKEN"),
            s3_endpoint=_env("S3_ENDPOINT"),
            s3_bucket=_env("S3_BUCKET", "ads-media"),
            s3_access_key_id=_env("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=read_secret("S3_SECRET_ACCESS_KEY"),
            s3_region=_env("S3_REGION", "auto"),
            s3_force_path_style=_bool(os.environ.get("S3_FORCE_PATH_STYLE"), True),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
