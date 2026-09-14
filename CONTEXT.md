# CONTEXT — Glossary

Canonical vocabulary for meta-ads-competitor-tracker. Use these terms (not
synonyms) in code, issues, UI and docs. This is a glossary — no implementation
decisions.

## Entities

- **Company**: the tracked brand/competitor. The name is set by the admin
  (editable), independent of any name coming from Facebook. A company has **N Pages**.

- **Page**: an advertiser's Facebook Page, identified by Meta's `page_id`. It is
  the unit the Meta Ad Library exposes and where collection happens. A Page belongs
  to **one** Company; a Company can have several. **Do not confuse it with an "ad
  account"** (the `act_…` from Ads Manager): that one is private to the owner and is
  **not trackable** through the public Ad Library.

- **Country**: whose Ad Library a Page is read from (the ads shown to people in that
  country). There is a **default country** (`app_settings.collect_country`); a Page
  can set its own (`pages.collect_country`), otherwise it uses the default.

- **Ad**: a single Meta ad, deduplicated by `ad_archive_id`. It belongs to a Page
  (and, through it, to a Company).

- **Creative**: one piece of an Ad. A carousel Ad has several Creatives (one per
  card), not several Ads. A card is identified by its **card key** (the media file
  id in Meta's URL plus its destination), not by its position: Meta reorders cards
  between reads.

- **Display format**: how Meta renders an Ad (`IMAGE`, `VIDEO`, `CAROUSEL`, `DCO`,
  `DPA`…), taken from the Ad Library. `CAROUSEL`, `DCO` and `DPA` (catalog) Ads show
  their cards; an `IMAGE`/`VIDEO` Ad with several Creatives is showing Formats.

- **Format**: one size/aspect ratio of an Ad (e.g. 9:16, 1:1, 1.91:1). Meta returns
  the same Ad in several Formats as several Creatives with the same text and
  destination: they are one Ad, not a carousel.

- **Version**: Ads that Meta groups as versions of one ad share a `collation_id`
  ("this ad has multiple versions" in the Ad Library). The library shows them as one
  card. Each Version is a whole Ad with its own `ad_archive_id`, not a Format.

- **Ad appearance**: an immutable record that an Ad was seen in a specific
  collection run. The basis of the time series.

- **Collection run** (a "run"): one execution of the collector over the tracked Pages,
  followed by linking and fetching landing-page titles. Its live progress is the
  **run status** (idle / queued / running / stalled). A run through Apify whose worker
  stops is **paused**, and the next worker **resumes** it: its Actor runs go on on Apify.

- **Landing page**: the canonical destination of a Creative (URL without
  utm/fbclid/fragment). Several Ads pointing at the same destination collapse into a
  single Landing page. It belongs to the Company.

- **Page snapshot**: a captured version of a Landing page. A new version is born
  when the content changes (a new hash). It carries the page's title and description,
  read from the page itself.

- **Collection**: a named folder of Ads the user saves in the dashboard (favorites).
  Unrelated to a "collection run".

## Attributes and concepts

- **`duration_days`**: how many days an Ad has been on air. **The core metric.**
  Without the competitor's performance data, time on air is the proxy for "it works"
  (nobody pays for 60 days by accident).

- **Active vs. Tracked** — the word "active" is overloaded; keep them apart:
  - **Active ad** (`is_active`): the Ad is running on Meta right now.
  - **Tracked page** (`is_tracked`): the admin wants collection to include this
    Page. Pausing tracking ≠ the ad stopped.

- **Media key**: a Creative's object key in storage (Garage/S3/R2) —
  `media_image_key` / `media_video_key` / `media_thumb_key`. Empty = not mirrored
  yet; falls back to Meta's original URL (which expires).

- **Inactive ads** (informally, the "cemetery"): ads that are no longer running.
  The Status filter surfaces them — useful for spotting angles that were tested and
  dropped, so you don't repeat what didn't work.

- **Aggregation axis**: the same dataset seen **by Ad** or **by Landing page** —
  the only difference is how the data is grouped/ordered.

- **Collection source**: where a Collection run reads the Ad Library from, one per
  instance (`app_settings.collect_source`):
  - **Self-hosted**: the worker reads it directly, from the server's own IP or
    through the Proxy pool.
  - **Apify**: the worker starts runs of the Apify Actor and reads their datasets.
  Both yield the same Ad data; switching source must not change what is collected.

- **Batch**: in the Apify source, the up-to-5 Pages (all of one Country) read by a
  single Actor run. Several Batches run in parallel, up to the configured number
  of simultaneous Actor runs. Don't confuse an **Actor run** (on Apify) with a
  **Collection run** (ours), which spans many Actor runs.

- **Proxy pool**: a rotating set of proxies used by the Self-hosted source to scrape
  the Ad Library.

## The instance

- **Setup script**: `setup.sh` / `setup.ps1`, run once before the first start. It
  writes `.env`: the database and media storage (bundled or your own) and their
  users and passwords, generated for the bundled services unless typed in.

- **Setup wizard**: the first-run flow in the browser: language → administrator →
  first competitor → collection (country, interval). `app_settings.setup_completed`
  records that it is done; everything it asks is also in Settings.

- **Interface language**: English, Spanish or Portuguese, for the whole instance
  (`app_settings.language`): chosen in the setup wizard, changed in Settings. Until
  it's chosen, the browser's language is used. It changes the interface only, never
  the collected data or the CSV column names.

- **Collection canary**: the command-line check (`python -m tracker.worker.canary`) that
  one ad can still be read from the Ad Library, run from the collecting server.
