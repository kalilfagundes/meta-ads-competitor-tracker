"""Ad-level collections API (favorites)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ... import db
from ..deps import _authed

router = APIRouter(prefix="/api/collections")


class CollectionCreate(BaseModel):
    name: str = ""


class ToggleBody(BaseModel):
    ad_archive_id: str = ""


@router.get("")
def collections_list(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return {"collections": db.list_collections()}


@router.post("")
def collections_create(request: Request, body: CollectionCreate):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    name = (body.name or "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    return JSONResponse(db.create_collection(name), status_code=201)


@router.delete("/{collection_id}")
def collections_delete(request: Request, collection_id: int):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.delete_collection(collection_id)
    return {"ok": True}


@router.post("/{collection_id}/toggle")
def collections_toggle(request: Request, collection_id: int, body: ToggleBody):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    ad = (body.ad_archive_id or "").strip()
    if not ad:
        return JSONResponse({"error": "ad_archive_id required"}, status_code=400)
    return {"action": db.toggle_collection_item(collection_id, ad)}
