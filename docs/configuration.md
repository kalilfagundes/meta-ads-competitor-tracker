# Configuration & deployment

The setup script and `docker compose up -d` cover the usual install. This guide covers the rest.

- [The .env file](#the-env-file)
- [How secrets reach the containers](#how-secrets-reach-the-containers)
- [The bundled Garage](#the-bundled-garage)
- [Using AWS S3, Cloudflare R2 or another S3](#using-aws-s3-cloudflare-r2-or-another-s3)
- [Using your own Postgres](#using-your-own-postgres)
- [Collecting through Apify](#collecting-through-apify)
- [Exposing it on the internet (HTTPS)](#exposing-it-on-the-internet-https)
- [Running without Docker](#running-without-docker)
- [Backups](#backups)
- [Upgrading](#upgrading)

## The .env file

`./setup.sh` (or `.\setup.ps1` on Windows) writes it; [`.env.example`](../.env.example) describes
every variable. Run the script with `--defaults` (`-Defaults`) to accept the recommended answers
without questions, e.g. in automation. It never replaces an existing `.env` unless you confirm
(or pass `--force` / `-Force`): new passwords wouldn't match a database that already exists.

`docker compose` refuses to start without `.env` and says so.

## How secrets reach the containers

`POSTGRES_PASSWORD`, `S3_SECRET_ACCESS_KEY`, `SESSION_SECRET`, `GARAGE_RPC_SECRET` and
`GARAGE_ADMIN_TOKEN` are declared as
[Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/) sourced from `.env`. Each
one is mounted as a read-only file under `/run/secrets` in the services that need it, and never in
their environment: Postgres gets only its password, Garage only its own secrets, and the worker has
no session secret. The app reads them through `*_FILE` variables
(`POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`, …), the same convention as the official
Postgres image.

## The bundled Garage

[Garage](https://garagehq.deuxfleurs.fr) runs as a single node configured by
[`garage.toml`](../garage.toml). It has no web console and isn't published outside Docker. On
every start the one-shot `garage-init` service makes sure the node has its storage role, the
access key from `.env` exists and the bucket is readable and writable with it.

The access key lives in Garage's own data. If you recreate `.env` with a different
`S3_SECRET_ACCESS_KEY` while keeping the volumes, `garage-init` stops with an error that says so:
restore the old value, or delete the stored media with `docker compose down -v`.

To inspect Garage, use its CLI inside the container:

```bash
docker compose exec garage /garage status
docker compose exec garage /garage bucket info ads-media
```

## Using AWS S3, Cloudflare R2 or another S3

Run the setup script and choose **External S3-compatible bucket**, or edit `.env`:

```bash
COMPOSE_PROFILES=postgres          # without "garage"
S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com   # empty for AWS S3
S3_BUCKET=my-ads-media
S3_REGION=auto                     # e.g. us-east-1 on AWS
S3_ACCESS_KEY_ID=<access key>
S3_SECRET_ACCESS_KEY=<secret key>
S3_FORCE_PATH_STYLE=false
```

Remove the `GARAGE_*` lines too. Then `docker compose up -d`. The `migrate` service creates the
bucket if it's missing. If Garage was running before, stop it with `docker compose rm -sf garage`;
its data stays in the `garage-data` volume. Media already mirrored there isn't moved: those
creatives fall back to Meta's original links until they expire, and new runs mirror into the new
bucket.

Media is always served to the browser through the app's `/media/…` route, so the bucket **does
not need to be public**.

## Using your own Postgres

Run the setup script and choose **Your own Postgres**, or edit `.env`:

```bash
COMPOSE_PROFILES=garage            # without "postgres"
POSTGRES_HOST=db.example.com
POSTGRES_PORT=5432
POSTGRES_DB=tracker
POSTGRES_USER=tracker
POSTGRES_PASSWORD=<password>
POSTGRES_SSLMODE=require
```

The `migrate` service creates the schema on it.

## Collecting through Apify

By default the worker reads the Ad Library itself (**Self-hosted**), from the server's IP or
through the proxies in Settings. Meta blocks many datacenter IPs; if collection fails from your
server, switch the source to **Apify** in Settings → *Where ads are read from* (or in the setup
wizard). The worker then runs the
[`kalilfagundes.w/facebook-instagram-meta-ads-library-scraper`](https://apify.com/kalilfagundes.w/facebook-instagram-meta-ads-library-scraper)
Actor on your Apify account, which reads through residential proxies. Settings and the help page
name the Actor too, so you always know what runs on your account.

- **Token.** Paste an API token from Apify Console → Settings → API & Integrations and press
  *Test token* (it checks the token without starting a run). The token is stored in the database,
  is never shown back in full and is never written to the logs.
- **Batches.** Pages are sent to the Actor 5 at a time, as Ad Library URLs (one country per run).
  *Simultaneous Actor runs* (1–5, default 2) sets how many of those runs go at once. Apify's free plan allows at most 5
  simultaneous runs, and while a collection runs, the ones it uses aren't available to your other
  Actors.
- **Cost.** Apify charges your account per ad collected. Each collection run shows what Apify
  charged in Settings (the *Cost* column). A failed Actor run is retried once; if it fails again,
  its Pages count as errors and none of their ads is marked as stopped.
- **Checking it.** `docker compose exec worker python -m tracker.worker.canary` checks the saved
  token and that the Actor is reachable, without starting a run (in Self-hosted mode it reads one
  ad from Meta instead).

Both sources store exactly the same data, so you can switch at any time.

## Exposing it on the internet (HTTPS)

The web container serves plain HTTP on port `8000`. Before exposing it beyond your machine:

1. Put a reverse proxy that terminates TLS in front of it, such as
   [Caddy](https://caddyserver.com/), Traefik, nginx, or a PaaS like Coolify. A minimal
   `Caddyfile`:

   ```
   ads.example.com {
       reverse_proxy localhost:8000
   }
   ```

2. Don't publish Postgres or Garage's ports (by default neither is).
3. Finish the setup wizard before the address is public. Creating the admin asks for the setup
   code (`SETUP_TOKEN`, printed by the setup script), so a stranger who finds a fresh instance
   can't claim it; if `SETUP_TOKEN` is empty, the web app generates a code and writes it to its
   log (`docker compose logs web`).

**Forwarding headers.** Behind a proxy, the app learns the visitor's real IP and that the
connection is HTTPS from the proxy's `X-Forwarded-For` / `X-Forwarded-Proto` headers: the IP for
the login limit, HTTPS to mark the session cookie `Secure`. It believes those headers only from
the addresses in `FORWARDED_ALLOW_IPS`, by default loopback and the private networks
(`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`). That covers a proxy on the same
machine or in Docker, while a visitor reaching port 8000 straight from the internet can't fake
either. Set it in `.env` only if your proxy connects from a public IP. Don't set it to `*`: anyone
could then fake their IP and dodge the login limit.

**Login limit.** After 5 failed sign-ins from one IP within 15 minutes, that IP is refused for the
rest of the window, even with the right password. After 10 failed sign-ins to one account within
15 minutes, from any IPs, that account is refused the same way, so guessing stays slow even for
someone who can change IP. The counters live in the web process, so
restarting it clears them.

The app asks search engines and AI crawlers to stay out: `/robots.txt` disallows everything,
and every response carries `X-Robots-Tag: noindex, nofollow, noarchive, …` (repeated as a
`<meta name="robots">` tag). Well-behaved bots honour this; it isn't access control, so rely on
the login (or restrict the proxy to your network or VPN) to keep others out.

## Running without Docker

You need Python 3.11+, a Postgres (14+) and any S3-compatible storage. ffmpeg is optional: without
it, videos are mirrored uncompressed.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL=postgresql://tracker:secret@localhost:5432/tracker
export SESSION_SECRET=$(openssl rand -hex 32)   # required: the web app won't start without it
export S3_ENDPOINT=<endpoint URL> S3_REGION=<region> S3_BUCKET=ads-media \
       S3_ACCESS_KEY_ID=<access key> S3_SECRET_ACCESS_KEY=<secret key>

python -m tracker.bootstrap                               # migrations + bucket
uvicorn tracker.web.app:app --host 0.0.0.0 --port 8000    # web
python -m tracker.worker.scheduler                        # worker (second terminal)
```

Every secret can also be given as a file: `SESSION_SECRET_FILE=/path/to/file`, and likewise for
`POSTGRES_PASSWORD` and `S3_SECRET_ACCESS_KEY`. Variables can also go in a `.env` file in the repo
root, which is loaded automatically; variables already set in the environment take precedence.

## Backups

All state lives in the Postgres database and the media bucket; keep a copy of `.env` too, or the
passwords won't match a restored database.

```bash
# Database dump
docker compose exec postgres pg_dump -U tracker tracker > backup.sql

# Restore into a fresh stack
docker compose exec -T postgres psql -U tracker tracker < backup.sql
```

For media, back up the `garage-meta` and `garage-data` Docker volumes (with the stack stopped), or
sync the bucket with any S3 tool, such as [rclone](https://rclone.org/) or `aws s3 sync`.
If media is lost, the ads still load: creatives fall back to Meta's original URLs until those expire.

## Upgrading

```bash
git pull
docker compose up -d --build
```

The `migrate` service applies any new database migrations before `web` and `worker` start.
