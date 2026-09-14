# Single image shared by web + worker + migrate.
FROM python:3.12-slim

WORKDIR /app

# ffmpeg: video compression in the worker (the mirror rescales/re-encodes videos).
# Without it the worker still works, but mirrors videos uncompressed (fallback).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY alembic.ini .
COPY migrations ./migrations
COPY tracker ./tracker

# Believe X-Forwarded-For/-Proto only from proxies on private networks (a Caddy/nginx on
# the host, Traefik/Coolify on the Docker network). A visitor hitting the port directly
# from the internet can't fake their IP or https; uvicorn reads this variable.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"

# Default command (the web); worker and migrate override it in docker-compose.
CMD ["uvicorn", "tracker.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
