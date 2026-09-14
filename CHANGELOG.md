# Changelog

Notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor version (0.x.0) may include
breaking changes; they are always listed here.

## [0.1.0]

The first public release.

### Added

- **Collection** from the public Meta Ad Library (no Meta API key), every *N* hours and on
  demand with **Collect now**, reading every active ad of each tracked Page. Two sources, chosen
  in the setup wizard or Settings:
  - **Apify** (recommended): an Apify Actor reads the Ad Library through residential proxies, so
    it works from servers Meta blocks. Pages go in batches of 5, with a configurable number of
    simultaneous Actor runs; a failed run is retried once, and what Apify charged is shown on
    every collection. A help page (`/help/apify`) explains getting a token and the costs.
  - **Self-hosted**: the server reads the Ad Library itself through
    [`meta-ads-collector`](https://github.com/promisingcoder/MetaAdsCollector), from its own IP
    or an optional rotating proxy pool.
- **Live collection status** in Settings: ads read by Apify so far, ads and media being saved,
  errors and cost.
- **Permanent copies of creatives** in any S3-compatible storage (bundled
  [Garage](https://garagehq.deuxfleurs.fr), AWS S3, Cloudflare R2…), saved several at a time
  after each collection. Video and image quality are set in Settings: *standard* (fast) keeps
  Meta's SD video and WebP images up to 1280px; *high* re-encodes HD video with ffmpeg and keeps
  images up to 2048px.
- **Ad library** filtered by company, status, platform and media type, with duration tiers
  (New → Testing → Winner → Gold → Evergreen), Meta's relative impression rank, each Page's
  picture and the kind of ad (carousel, catalog, dynamic creative, video, image).
- **Ad page** that shows carousels, catalog and dynamic-creative ads card by card (image, title,
  text, caption, call to action and link), the ad in each of its formats, its versions, where it
  leads, and a header with the Page's picture, likes and categories.
- **Landing pages view**: ads grouped by canonical destination, with each page's title and
  description.
- **Collections** of saved ads, and **CSV export** of ads and landing pages.
- **Companies with several Facebook Pages**, each Page with its own Ad Library country if needed.
- **Settings UI** for companies, Pages, the collection source, proxies, collection interval,
  default country, media quality and language.
- **Interface in English, Spanish and Portuguese.**
- **Setup scripts** (`setup.sh`, `setup.ps1`) that write `.env`: bundled or external Postgres
  and storage, with chosen or generated users and passwords.
- **First-run wizard** in the browser: language, administrator (protected by a setup code),
  first competitor, collection.
- **Docker Compose stack** (web, worker, migrate, optional Postgres and Garage), with passwords
  passed as Compose secrets.
- **Kept out of search engines and AI crawlers**: `robots.txt` disallows everything and every
  response is sent with `X-Robots-Tag: noindex`.
- **Hardened by default**: the app refuses to start without a strong `SESSION_SECRET`; sign-ins
  are limited per IP and per account; forwarding headers are trusted only from private networks
  (`FORWARDED_ALLOW_IPS`), so the session cookie is `Secure` behind HTTPS; the worker and the
  media proxy only fetch media from Meta's CDN and landing pages from public addresses, checking
  every redirect; links built from collected data are only rendered for http(s) URLs.
- **Collection check** (`python -m tracker.worker.canary`): reads one ad (Self-hosted) or checks
  the Apify token and Actor (Apify) to tell whether collection still works.

[0.1.0]: https://github.com/kalilfagundes/meta-ads-competitor-tracker/releases/tag/v0.1.0
