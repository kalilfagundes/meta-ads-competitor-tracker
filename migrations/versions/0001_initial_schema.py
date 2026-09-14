"""initial schema

Companies and their Facebook Pages, the ads collected from them (creatives,
appearances per run), landing pages with their title snapshots, saved
collections, and the instance tables (admin user, settings with the Collection
source, proxies).

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-14
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = r"""
-- Company (a competitor brand). The name is set by the admin.
CREATE TABLE companies (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    domain      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Facebook Page tracked for a company (N per company). is_tracked: does the admin
-- collect this Page? page_name_from_admin: the admin named it, so runs keep that
-- name instead of Facebook's. collect_country: whose Ad Library to read for this
-- Page; NULL uses the default country (app_settings).
CREATE TABLE pages (
    id                   SERIAL PRIMARY KEY,
    company_id           INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    page_id              TEXT UNIQUE NOT NULL,
    page_name            TEXT,
    page_name_from_admin BOOLEAN NOT NULL DEFAULT FALSE,
    page_url             TEXT,
    collect_country      TEXT,
    is_tracked           BOOLEAN NOT NULL DEFAULT TRUE,
    -- the Page as Meta shows it; profile_picture_key: the picture mirrored in storage
    profile_picture_url  TEXT,
    profile_picture_key  TEXT,
    profile_uri          TEXT,
    like_count           BIGINT,
    categories           TEXT[],
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pages_company ON pages(company_id);

-- One collection run, with the live progress the Settings screen polls.
CREATE TABLE collection_runs (
    id                      SERIAL PRIMARY KEY,
    trigger                 TEXT,                  -- manual | schedule | startup
    started_at              TIMESTAMPTZ NOT NULL,
    finished_at             TIMESTAMPTZ,
    heartbeat_at            TIMESTAMPTZ,
    pages_total             INTEGER NOT NULL DEFAULT 0,
    pages_done              INTEGER NOT NULL DEFAULT 0,
    current_step            TEXT,                  -- Page being collected, or the post-collection step
    current_page_ads        INTEGER NOT NULL DEFAULT 0,
    total_ads_collected     INTEGER DEFAULT 0,
    new_ads_discovered      INTEGER DEFAULT 0,
    companies_with_results  INTEGER DEFAULT 0,
    errors                  INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    cost_usd                NUMERIC(12, 6)         -- what Apify charged; NULL when Self-hosted
);

-- Ads, deduplicated by Meta's ad_archive_id. duration_days is the core signal.
-- collation_id groups the ads Meta shows as versions of one another.
CREATE TABLE ads (
    id                      SERIAL PRIMARY KEY,
    ad_archive_id           TEXT UNIQUE NOT NULL,
    company_id              INTEGER REFERENCES companies(id),
    page_ref_id             INTEGER REFERENCES pages(id),
    page_id                 TEXT,
    page_name               TEXT,
    is_active               BOOLEAN,
    ad_status               TEXT,
    delivery_start_time     TIMESTAMPTZ,
    delivery_stop_time      TIMESTAMPTZ,
    duration_days           INTEGER,
    snapshot_url            TEXT,
    ad_snapshot_url         TEXT,
    publisher_platforms     TEXT[],
    languages               TEXT[],
    ad_type                 TEXT,
    categories              JSONB,
    collation_id            TEXT,
    collation_count         INTEGER,
    first_seen_at           TIMESTAMPTZ,
    last_seen_at            TIMESTAMPTZ,
    last_collection_run_id  INTEGER REFERENCES collection_runs(id),
    -- how Meta renders the ad: IMAGE, VIDEO, CAROUSEL, DCO, DPA...
    display_format          TEXT,
    -- the ad's primary text (a carousel's cards often have none; for catalog ads
    -- it's a {{template}})
    body                    TEXT
);
CREATE INDEX idx_ads_company   ON ads(company_id);
CREATE INDEX idx_ads_page      ON ads(page_ref_id);
CREATE INDEX idx_ads_active    ON ads(is_active);
CREATE INDEX idx_ads_last_seen ON ads(last_seen_at);
CREATE INDEX idx_ads_collation ON ads(collation_id);

-- Landing page: one row per canonical URL (UTM, fbclid and fragment removed).
CREATE TABLE landing_pages (
    id                SERIAL PRIMARY KEY,
    company_id        INTEGER REFERENCES companies(id),
    url_canonical     TEXT UNIQUE NOT NULL,
    url_raw_example   TEXT,
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_resolved_at  TIMESTAMPTZ,                 -- last title fetch; NULL = never
    resolution_status TEXT,                        -- ok | fetch_failed | no_content
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_lp_company  ON landing_pages(company_id);
CREATE INDEX idx_lp_resolved ON landing_pages(last_resolved_at NULLS FIRST);

-- Creatives (N per ad: carousel cards or formats). media_*_key is the object key
-- in storage; NULL = not mirrored yet, so Meta's original *_url is used.
CREATE TABLE creatives (
    id              SERIAL PRIMARY KEY,
    ad_id           INTEGER REFERENCES ads(id) ON DELETE CASCADE,
    creative_index  INTEGER DEFAULT 0,
    body            TEXT,
    title           TEXT,
    description     TEXT,
    link_url        TEXT,
    landing_page_id INTEGER REFERENCES landing_pages(id),
    image_url       TEXT,
    video_url       TEXT,
    video_hd_url    TEXT,
    video_sd_url    TEXT,
    thumbnail_url   TEXT,
    cta_text        TEXT,
    cta_type        TEXT,
    media_image_key TEXT,
    media_video_key TEXT,
    media_thumb_key TEXT,
    caption         TEXT,          -- the display domain under a card
    card_key        TEXT,          -- the card's identity across reads (media file + destination)
    UNIQUE(ad_id, creative_index),
    UNIQUE(ad_id, card_key)
);
CREATE INDEX idx_creatives_ad ON creatives(ad_id);
CREATE INDEX idx_creatives_lp ON creatives(landing_page_id);

-- An ad seen in a run. sort_index: its position in Meta's impression-sorted
-- results for its Page (0 = top).
CREATE TABLE ad_appearances (
    id                  SERIAL PRIMARY KEY,
    ad_id               INTEGER REFERENCES ads(id) ON DELETE CASCADE,
    collection_run_id   INTEGER REFERENCES collection_runs(id),
    is_active           BOOLEAN,
    sort_index          INTEGER,
    seen_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_appearances_ad   ON ad_appearances(ad_id);
CREATE INDEX idx_appearances_run  ON ad_appearances(collection_run_id);
CREATE INDEX idx_appearances_sort ON ad_appearances(ad_id, seen_at DESC);

-- Collections of ads saved by the user.
CREATE TABLE collections (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE collection_items (
    collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
    ad_archive_id TEXT NOT NULL,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, ad_archive_id)
);

-- A landing page's title and description when fetched. A new content_hash means
-- the page changed. raw: {final_url, http_status, title, description, text_len}.
CREATE TABLE page_snapshots (
    id              SERIAL PRIMARY KEY,
    landing_page_id INTEGER REFERENCES landing_pages(id) ON DELETE CASCADE,
    content_hash    TEXT NOT NULL,
    headline        TEXT,
    description     TEXT,
    raw             JSONB,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_snapshots_lp   ON page_snapshots(landing_page_id, captured_at DESC);
CREATE INDEX idx_snapshots_hash ON page_snapshots(content_hash);

-- Each landing page's activity across the ads that point at it.
CREATE VIEW lp_activity AS
SELECT
    lp.id                                                AS landing_page_id,
    lp.company_id,
    c.name                                               AS company_name,
    lp.url_canonical,
    COUNT(DISTINCT a.id)                                 AS ad_count,
    MIN(a.delivery_start_time)                           AS window_start,
    MAX(COALESCE(a.delivery_stop_time, a.last_seen_at))  AS window_end,
    BOOL_OR(a.is_active)                                 AS currently_active,
    MAX(a.duration_days)                                 AS max_duration_days,
    (SELECT COUNT(*) FROM page_snapshots ps
       WHERE ps.landing_page_id = lp.id)                 AS snapshot_count
FROM landing_pages lp
JOIN companies c          ON c.id = lp.company_id
LEFT JOIN creatives cr    ON cr.landing_page_id = lp.id
LEFT JOIN ads a           ON a.id = cr.ad_id
GROUP BY lp.id, lp.company_id, c.name, lp.url_canonical;

-- The admin account, created by the first-run wizard.
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Instance settings: a single row (id = 1), edited in the setup wizard and Settings.
-- collect_country is the default country; language is the interface language
-- (NULL until chosen: the browser's language is used). collect_source: where ads
-- are read from (Self-hosted or the Apify Actor, with its token and how many Actor
-- runs go at once). video/image_quality: how media is stored (standard = fast).
CREATE TABLE app_settings (
    id                       INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    setup_completed          BOOLEAN NOT NULL DEFAULT FALSE,
    language                 TEXT CHECK (language IN ('en', 'es', 'pt')),
    collect_interval_hours   INTEGER NOT NULL DEFAULT 6,
    collect_country          TEXT NOT NULL DEFAULT 'US',
    collect_now              BOOLEAN NOT NULL DEFAULT FALSE,
    collect_now_requested_at TIMESTAMPTZ,
    collect_source           TEXT NOT NULL DEFAULT 'self_hosted'
                             CHECK (collect_source IN ('self_hosted', 'apify')),
    apify_token              TEXT,
    apify_max_parallel_runs  INTEGER NOT NULL DEFAULT 2 CHECK (apify_max_parallel_runs BETWEEN 1 AND 5),
    video_quality            TEXT NOT NULL DEFAULT 'standard' CHECK (video_quality IN ('standard', 'high')),
    image_quality            TEXT NOT NULL DEFAULT 'standard' CHECK (image_quality IN ('standard', 'high')),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO app_settings (id) VALUES (1);

-- Proxy pool the collector rotates through.
CREATE TABLE proxies (
    id       SERIAL PRIMARY KEY,
    url      TEXT NOT NULL,
    enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


DOWNGRADE_SQL = r"""
DROP VIEW IF EXISTS lp_activity;
DROP TABLE IF EXISTS proxies;
DROP TABLE IF EXISTS app_settings;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS page_snapshots;
DROP TABLE IF EXISTS collection_items;
DROP TABLE IF EXISTS collections;
DROP TABLE IF EXISTS ad_appearances;
DROP TABLE IF EXISTS creatives;
DROP TABLE IF EXISTS landing_pages;
DROP TABLE IF EXISTS ads;
DROP TABLE IF EXISTS collection_runs;
DROP TABLE IF EXISTS pages;
DROP TABLE IF EXISTS companies;
"""


def _run(sql: str) -> None:
    """Execute each ';'-separated statement. Line comments are stripped first so a
    ';' inside a comment can't split a statement (this DDL has no string literal
    containing '--' or ';')."""
    no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    for chunk in no_comments.split(";"):
        if chunk.strip():
            op.execute(chunk)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
