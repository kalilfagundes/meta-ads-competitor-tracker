"""Apify Actor runs of each collection run

What a collection run through Apify started on Apify, Batch by Batch, so that a
worker that stops mid-run (a restart, a deploy, a crash) can be followed by one
that picks the same Actor runs up: it waits for those still going and stores the
ads of those that finished, instead of losing them and paying for them again.

Revision ID: 0002_apify_actor_runs
Revises: 0001_initial
Create Date: 2026-09-24
"""
from alembic import op

revision = "0002_apify_actor_runs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE apify_actor_runs (
            id                  TEXT PRIMARY KEY,      -- Apify's run id
            collection_run_id   INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
            country             TEXT NOT NULL,         -- the Batch: its country...
            page_ids            INTEGER[] NOT NULL,    -- ...and its Pages (pages.id)
            attempt             INTEGER NOT NULL,      -- a failed Actor run is tried once more
            dataset_id          TEXT NOT NULL,
            key_value_store_id  TEXT,
            outcome             TEXT,                  -- NULL until dealt with: stored | failed
            started_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_actor_runs_run ON apify_actor_runs(collection_run_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS apify_actor_runs")
