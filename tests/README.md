# Tests

Regression tests so you can change the code and know quickly whether something
broke.

- **`test_unit.py`** — pure functions (auth, carousel heuristic, URL
  canonicalization, landing-page title extraction, run state, duration math, the
  SSRF allowlist, duration tiers, the open-redirect guard, image compression, and
  both Collection sources normalizing the ads in `fixtures/meta_records.json` to
  the same Ad). No
  database, no network — these run anywhere.
- **`test_db.py`** — the data layer (the ads library: filters, pages, Versions, champions; ad detail with formats and
  versions, landing catalog/detail, admin reads and edits, run status and
  single-flight "collect now", collections) plus the landing-page title crawl
  against the database (network stubbed). Needs a Postgres.
- **`test_migrations.py`**: the schema migrates down to base and back up, and a
  fresh instance starts with the right settings. Moves the test database's schema,
  always ending at head. Needs a Postgres.
- **`test_web.py`** — the HTTP routes via FastAPI's TestClient (auth gating, the
  rendered pages, pagination markup, `/media` robustness + SSRF guard). Needs a
  Postgres.

## Running

Install the dev deps once:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Just the fast unit tests (no database needed):

```bash
pytest tests/test_unit.py
```

The full suite needs a Postgres. Point `TEST_DATABASE_URL` at a throwaway one —
the tests apply the migrations and truncate between tests, so use a **disposable**
database, never a real one:

```bash
docker run -d --name tracker-test-pg \
  -e POSTGRES_USER=tracker -e POSTGRES_PASSWORD=tracker -e POSTGRES_DB=tracker \
  -p 55432:5432 postgres:16

TEST_DATABASE_URL=postgresql://tracker:tracker@localhost:55432/tracker pytest

docker rm -f tracker-test-pg
```

If no reachable database is configured, the DB/web tests **skip** (they don't
fail), so a bare `pytest` still runs the unit suite.
