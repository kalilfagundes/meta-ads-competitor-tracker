"""Ad-level collections (favorites saved by the user in the dashboard)."""

from __future__ import annotations

from .base import connect


def list_collections() -> list[dict]:
    with connect() as conn:
        return conn.execute(
            """SELECT c.id, c.name,
                      COALESCE(array_agg(ci.ad_archive_id)
                               FILTER (WHERE ci.ad_archive_id IS NOT NULL), '{}') AS ad_archive_ids
               FROM collections c
               LEFT JOIN collection_items ci ON ci.collection_id = c.id
               GROUP BY c.id, c.name ORDER BY c.name"""
        ).fetchall()


def create_collection(name: str) -> dict:
    with connect() as conn:
        return conn.execute(
            """INSERT INTO collections (name) VALUES (%s)
               ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
               RETURNING id, name""",
            (name,),
        ).fetchone()


def delete_collection(collection_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM collections WHERE id = %s", (collection_id,))


def toggle_collection_item(collection_id: int, ad_archive_id: str) -> str:
    """Add/remove an ad from a collection. Returns 'added' | 'removed'."""
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM collection_items WHERE collection_id = %s AND ad_archive_id = %s",
            (collection_id, ad_archive_id),
        ).fetchone()
        if exists:
            conn.execute(
                "DELETE FROM collection_items WHERE collection_id = %s AND ad_archive_id = %s",
                (collection_id, ad_archive_id),
            )
            return "removed"
        conn.execute(
            "INSERT INTO collection_items (collection_id, ad_archive_id) VALUES (%s, %s)",
            (collection_id, ad_archive_id),
        )
        return "added"
