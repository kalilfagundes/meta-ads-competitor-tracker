"""Bootstrap for the `migrate` service (docker-compose): applies the migrations and
ensures the storage bucket. web/worker only start after this finishes successfully.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time

from . import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("bootstrap")


def main() -> None:
    log.info("applying migrations (alembic upgrade head)...")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)

    log.info("ensuring the storage bucket...")
    for i in range(30):
        try:
            storage.ensure_bucket()
            log.info("bucket ok")
            break
        except Exception as exc:  # storage may still be starting up
            log.info("storage unavailable (%s); attempt %d/30...", exc, i + 1)
            time.sleep(2)
    else:
        log.warning("bucket not ensured after retries; it will be created on first use")

    log.info("bootstrap complete")


if __name__ == "__main__":
    main()
