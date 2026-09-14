<div align="center">

# Meta Ads Competitor Tracker

**A self-hosted tracker for your competitors' ads in the [Meta Ad Library](https://www.facebook.com/ads/library/).**
Collects on a schedule, mirrors the creatives, keeps the full history, and shows which ads have lasted.

[![CI](https://github.com/kalilfagundes/meta-ads-competitor-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/kalilfagundes/meta-ads-competitor-tracker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)

<img src="docs/images/meta-ads-competitor-tracking.png" alt="The Ads screen: each competitor's longest-running ads above the filterable library" width="900">

</div>

---

## Why

You can't see a competitor's ad spend, clicks or conversions, but you can see how long each ad
has been running. Nobody pays to keep an ad running for 60 days by accident, so **time on air
(`duration_days`) is the best public signal that an ad works**.

But the Ad Library only shows what is **live today**. As soon as a competitor switches an ad off,
it disappears from the library, except for categories Meta keeps archived (such as political and
social-issue ads). The creative's media links also expire, so that ad, and everything you
could have learned from it, is **gone for good**.

This tool collects the ads on a schedule, **stores a permanent copy of every ad and creative**
(images and videos included, in your own storage), and keeps the history after the ad has been
switched off. It then builds a searchable library around the time-on-air signal. Creative teams (copywriters, video
editors, strategists) use it to answer questions like:

- Which competitor ads have been running the longest (the "evergreens")?
- What are they testing this week?
- Which angles did they try and drop?
- Which landing pages get the most ads pointed at them?

## Features

- **No Meta API key.** Collection uses the public Ad Library via
  [`meta-ads-collector`](https://github.com/promisingcoder/MetaAdsCollector) (MIT).
- **Quick setup.** Run the setup script to create `.env`, then follow the wizard in the browser:
  confirm the language, create the admin, add your first competitor, and pick the country and how
  often to collect. Collection starts right away. Storage and Postgres are bundled, or use your own
  S3-compatible bucket and database.
- **Scheduled and on-demand collection.** Runs every *N* hours, plus a **Collect now** button.
- **Compressed media.** Images are converted to WebP and videos compressed with ffmpeg to save
  storage.
- **Two ways to read the Ad Library.** Self-hosted, from your server's own IP or through an
  optional proxy pool with rotation (paste proxies into the UI), or through the
  [`kalilfagundes.w/facebook-instagram-meta-ads-library-scraper`](https://apify.com/kalilfagundes.w/facebook-instagram-meta-ads-library-scraper)
  Actor on [Apify](https://apify.com), which uses residential proxies, for servers Meta blocks.
  Apify charges your Apify account per ad; the cost of each run shows in Settings.
- **Carousels, catalog and dynamic ads card by card**, with each card's image, text and link, and
  the advertiser's Page picture, likes and categories.
- **CSV export.** Download the ads you're looking at (with the current filters) or the landing
  pages, ready for a spreadsheet.
- **Two ways to browse the same data:**
  - **By ad:** filter by one or more companies, status, platform and media type (image, video,
    carousel).
    Duration tiers (New → Testing → Winner → Gold → Evergreen) and Meta's relative impression rank
    are shown on every ad.
  - **By landing page:** ads are grouped by their canonical destination (UTM and `fbclid` removed),
    so you can see which offers competitors push hardest.
- **Settings UI for non-technical admins.** Add companies, attach one or more Facebook Pages to
  each, pause or resume tracking, set a Page's country, and change the collection interval,
  default country and interface language.
- **English, Español, Português.** Picked from the browser in the wizard and changeable in
  Settings.

## Quick start

**Requirements:** [Docker](https://docs.docker.com/get-docker/) with Compose v2 (`docker compose`).

```bash
git clone https://github.com/kalilfagundes/meta-ads-competitor-tracker.git
cd meta-ads-competitor-tracker
```

**1. Create `.env`.** The setup script asks a few questions (press Enter for the recommended
answers: the bundled Postgres and Garage, with generated passwords) and writes `.env`:

```bash
./setup.sh
```

On Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Prefer to write it yourself? Copy [`.env.example`](.env.example) to `.env` and fill it in.

**2. Start it.**

```bash
docker compose up -d --build
```

**3. Open <http://localhost:8765>** and follow the setup wizard:

1. **Language:** confirm the one detected from your browser, or pick another.
2. **Administrator:** enter the setup code the setup script printed (`SETUP_TOKEN` in `.env`),
   then choose a username and password.
3. **First competitor:** name the company and paste its **Facebook Page ID** (see
   [Finding a Page ID](#finding-a-page-id)).
4. **Collection:** the default country whose Ad Library to read (each Page can have its own,
   in Settings) and how often to collect. The first collection starts right away, and Settings
   shows its progress.

Add more companies and Pages in **Settings** at any time. Ads show up on the home screen as they
are collected.

To stop the stack, run `docker compose down`. Your data is kept in Docker volumes; add `-v` to
delete it as well.

### Finding a Page ID

The tracker follows **Facebook Pages**, not ad accounts. To get a Page ID:

1. Open the [Meta Ad Library](https://www.facebook.com/ads/library/) and search for the advertiser.
2. Click the advertiser's name. The URL now contains `view_all_page_id=<digits>`. Those digits are
   the Page ID.

The Ad Library also shows a *Library ID* on each ad. That is the ID of one ad, not of the Page, so
don't paste it into Settings. Ad-account IDs (`act_…` in Ads Manager) are private and can't be
tracked.

A company can have several Pages, for example regional or product-specific ones. Adding a Page ID
that is already registered moves it under the company you're editing.

## How it works

| Service    | What it does                                                                                             |
| ---------- | -------------------------------------------------------------------------------------------------------- |
| `migrate`  | Runs once at startup: applies Alembic migrations and creates the storage bucket, then exits.            |
| `web`      | FastAPI app with server-rendered Jinja templates and a bespoke CSS design system (no Node build step).  |
| `worker`   | Runs collection on the configured interval or on **Collect now**, then links ads to landing pages and fetches each page's title. One worker collects at a time: a second one waits. A collection through Apify interrupted by a restart is resumed where it stopped. |
| `postgres` | Postgres 16. Holds ads, creatives, collection runs, landing pages, users and settings. Optional: the `postgres` profile. |
| `garage`   | [Garage](https://garagehq.deuxfleurs.fr), S3-compatible object storage for the mirrored media. Optional: the `garage` profile. |
| `garage-init` | Runs once at startup, before `migrate`: sets up Garage with the access key and bucket from `.env`, then exits. Part of the `garage` profile. |

## Configuration

Everything is in `.env`, written by the setup script and documented variable by variable in
[`.env.example`](.env.example):

| Setting                | Variables                                                          |
| ---------------------- | ------------------------------------------------------------------ |
| Bundled services       | `COMPOSE_PROFILES` (`postgres`, `garage`, both or neither)         |
| Web port               | `WEB_PORT`                                                         |
| Trusted proxies        | `FORWARDED_ALLOW_IPS` (optional)                                   |
| Database               | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE` |
| Media storage          | `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_FORCE_PATH_STYLE` |
| Bundled Garage         | `GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN`                          |
| Login cookie signature | `SESSION_SECRET` (required, 32+ characters)                        |
| First-run setup code   | `SETUP_TOKEN`                                                      |

To switch to an external bucket or database later, run the setup script again or edit `.env`, then
`docker compose up -d`. To run the app **without Docker** or put it **behind HTTPS**, see
[`docs/configuration.md`](docs/configuration.md).

## Development

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

**Tests.** The unit tests run anywhere. The DB and web tests need a disposable Postgres and are
*skipped* (not failed) without one:

```bash
pytest tests/test_unit.py tests/test_i18n.py                 # fast, no database
TEST_DATABASE_URL=postgresql://tracker:tracker@localhost:55432/tracker pytest   # full suite
```

Setup details are in [`tests/README.md`](tests/README.md). To run the web app and worker against
local services outside Docker, follow [`docs/configuration.md`](docs/configuration.md#running-without-docker).

### Project layout

```
tracker/
  config.py        env-driven infra settings
  storage.py       S3 client (Garage / S3 / R2)
  auth.py          password hashing + signed session cookie
  bootstrap.py     migrations + bucket (the `migrate` service)
  i18n.py          interface translations (EN/ES/PT) and language negotiation
  locale/          gettext catalogs (.po)
  db/              data layer, split by domain (ads, landing, admin, collections, …)
  web/             FastAPI app: routes/, templates/, static/
  worker/          collector, persistence + media mirroring, linking, page titles, scheduler
migrations/        Alembic schema
setup.sh, setup.ps1  interactive .env creation (Linux/macOS, Windows)
tests/             pytest suite (unit + DB/web integration)
docs/              configuration & deployment guide
CONTEXT.md         domain glossary: Company, Page, Ad, Creative, Landing page, …
```

[`CONTEXT.md`](CONTEXT.md) defines the vocabulary used in the code, issues and UI. Read it before
contributing.

## Known limitations

- **Scraping depends on Meta.** Collection uses the public Ad Library web endpoints through
  `meta-ads-collector`. When Meta changes them, collection can break until the collector is
  updated. Heavy use without proxies may be rate-limited, and Meta blocks many datacenter IPs: the
  Apify source is the way around both, at a cost.

## Responsible use

This project is **not affiliated with, endorsed by, or sponsored by Meta Platforms, Inc.**
"Meta", "Facebook" and "Instagram" are trademarks of their respective owners.

It is also **not affiliated with Nike, Adidas or any other brand shown in the screenshots**. Those
images are only illustrative examples of ads the companies ran publicly in the Meta Ad Library;
the brands, logos and creatives belong to their owners.

The tool only reads data that Meta publishes in its public Ad Library. You are responsible for
using it in line with Meta's Terms of Service and the laws that apply to you, including
copyright on the creatives you mirror and privacy rules. Use it for competitive research,
not to republish other people's ads.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow
and [SECURITY.md](SECURITY.md) to report a vulnerability privately. Everyone taking part is
expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE). Collection is powered by
[meta-ads-collector](https://github.com/promisingcoder/MetaAdsCollector) (MIT).
