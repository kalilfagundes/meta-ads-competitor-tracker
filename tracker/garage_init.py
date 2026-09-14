"""Setup of the bundled Garage storage (the `garage-init` service in docker-compose).

A new Garage node has no storage role, no access keys and no buckets. Through
Garage's admin API this gives the node its role, imports the access key from
`.env` and creates the bucket, readable and writable with that key. Each step
checks first, so it runs safely on every start; `migrate` waits for it.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from .config import get_settings, read_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("garage_init")

logging.getLogger("httpx").setLevel(logging.WARNING)

# A single node stores everything: its capacity only weighs data between nodes, it
# isn't a quota. The disk size is used when Garage reports it, so `garage status`
# reads sensibly.
FALLBACK_CAPACITY = 1_000_000_000
NODE_ZONE = "local"


class GarageError(RuntimeError):
    pass


def _call(client: httpx.Client, endpoint: str, *, params: dict | None = None,
          body: dict | None = None, missing_ok: bool = False) -> dict | None:
    """One admin API v2 call: GET without a body, POST with one. None for a 404 when
    `missing_ok`."""
    if body is None:
        response = client.get(f"/v2/{endpoint}", params=params)
    else:
        response = client.post(f"/v2/{endpoint}", params=params, json=body)
    if missing_ok and response.status_code == 404:
        return None
    if response.is_error:
        raise GarageError(f"{endpoint} failed ({response.status_code}): {response.text}")
    return response.json()


def _wait_until_ready(client: httpx.Client, attempts: int = 30, delay: float = 2) -> None:
    """Garage answers /health with 503 until the layout gives it a role."""
    for _ in range(attempts):
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(delay)
    raise GarageError("Garage didn't become ready")


def configure(client: httpx.Client, access_key_id: str, secret_access_key: str,
              bucket: str, *, delay: float = 2) -> None:
    for i in range(30):  # Garage may still be starting up
        try:
            status = _call(client, "GetClusterStatus")
            break
        except httpx.TransportError as exc:
            log.info("Garage unavailable (%s); attempt %d/30...", exc, i + 1)
            time.sleep(delay)
    else:
        raise GarageError("Garage unreachable")

    if status["layoutVersion"] == 0:
        node = status["nodes"][0]
        node_id = node["id"]
        capacity = (node.get("dataPartition") or {}).get("total") or FALLBACK_CAPACITY
        _call(client, "UpdateClusterLayout", body={"roles": [
            {"id": node_id, "zone": NODE_ZONE, "capacity": capacity, "tags": []},
        ]})
        _call(client, "ApplyClusterLayout", body={"version": 1})
        log.info("storage role assigned to node %s", node_id[:16])
    _wait_until_ready(client, delay=delay)

    key = _call(client, "GetKeyInfo", params={"id": access_key_id, "showSecretKey": "true"},
                missing_ok=True)
    if key is None:
        _call(client, "ImportKey", body={
            "accessKeyId": access_key_id, "secretAccessKey": secret_access_key, "name": "tracker",
        })
        log.info("access key %s imported", access_key_id)
    elif key.get("secretAccessKey") != secret_access_key:
        raise GarageError(
            f"Garage already has the access key {access_key_id} with a different secret. "
            ".env was probably recreated while Garage kept its data: restore the old "
            "S3_SECRET_ACCESS_KEY, or delete the media with `docker compose down -v`."
        )

    info = _call(client, "GetBucketInfo", params={"globalAlias": bucket}, missing_ok=True)
    if info is None:
        info = _call(client, "CreateBucket", body={"globalAlias": bucket})
        log.info("bucket %s created", bucket)
    _call(client, "AllowBucketKey", body={
        "bucketId": info["id"],
        "accessKeyId": access_key_id,
        "permissions": {"read": True, "write": True, "owner": True},
    })


def main() -> None:
    settings = get_settings()
    token = read_secret("GARAGE_ADMIN_TOKEN")
    with httpx.Client(base_url=os.environ.get("GARAGE_ADMIN_URL", "http://garage:3903"),
                      headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        configure(client, settings.s3_access_key_id, settings.s3_secret_access_key,
                  settings.s3_bucket)
    log.info("Garage ready")


if __name__ == "__main__":
    main()
