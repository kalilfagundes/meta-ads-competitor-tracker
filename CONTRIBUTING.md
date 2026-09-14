# Contributing

Bug reports, fixes, docs and features are all welcome.

## Before you start

- **Bugs:** open an issue using the *Bug report* template, and include steps to reproduce.
- **Features or larger changes:** open an issue first so we can agree on the approach before you
  put time into it.
- **Security issues:** don't open a public issue. See [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/kalilfagundes/meta-ads-competitor-tracker.git
cd meta-ads-competitor-tracker
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

To run the whole stack, use `./setup.sh --defaults && docker compose up -d --build`. To run the web app and worker
directly, see [docs/configuration.md](docs/configuration.md#running-without-docker).

## Tests

Every change should keep the suite green, and new behaviour should come with a test.

```bash
# Unit and translation tests: no database needed
pytest tests/test_unit.py tests/test_i18n.py

# Full suite: needs a *disposable* Postgres (the tests truncate tables)
docker run -d --name tracker-test-pg -e POSTGRES_USER=tracker -e POSTGRES_PASSWORD=tracker \
  -e POSTGRES_DB=tracker -p 55432:5432 postgres:16
TEST_DATABASE_URL=postgresql://tracker:tracker@localhost:55432/tracker pytest
```

Tests should check **behaviour** (data shapes, status codes, redirects), not UI copy or markup, so
that design changes don't break them. See [tests/README.md](tests/README.md).

## Conventions

- **Vocabulary:** use the terms defined in [CONTEXT.md](CONTEXT.md), such as *Company*, *Page*,
  *Ad*, *Creative*, *Landing page*, *Collection run*. Keep *active* (running on Meta) and
  *tracked* (collected by us) distinct.
- **Language:** code, comments and docs are in English. Every UI string is marked for translation
  (see [Translations](#translations)).
- **Schema changes:** always add a new Alembic migration under `migrations/versions/`. Never edit
  one that has already been released.
- **Config:** infra and secrets go in env vars (`tracker/config.py`). Anything an admin should
  change belongs in the database and the Settings UI.
- **Frontend:** server-rendered Jinja plus the CSS design system in `tracker/web/static/css/app.css`.
  There is no Node build step and no CSS framework; reuse the existing tokens and components.
- Match the style of the surrounding code, and keep functions small and comments meaningful.

## Translations

The interface is available in English (the source), Spanish and Portuguese. Catalogs are standard
gettext files, so they work with any `.po` editor (Poedit, Weblate, Crowdin):

- `tracker/locale/messages.pot`: every string in the code (generated, don't edit by hand);
- `tracker/locale/<lang>/LC_MESSAGES/messages.po`: the translations.

Mark strings where you write them:

- templates: `{{ _("Save") }}`, `{{ _("Page %(page)s of %(pages)s", page=1, pages=3) }}`,
  `{{ ngettext("%(num)s ad", "%(num)s ads", count) }}`;
- scripts in `tracker/web/static/js/`: the same `_()` and `ngettext()` calls;
- Python: `N_("Text")` for a message translated later, or `translate(lang, "Text")`.

Placeholders are always `%(name)s`, never string concatenation, so translators can reorder
words. After adding or changing strings, update the catalogs and fill in the new entries:

```bash
pybabel extract -F babel.cfg -k N_ --no-wrap -o tracker/locale/messages.pot .
pybabel update -i tracker/locale/messages.pot -d tracker/locale --no-wrap
```

`tests/test_i18n.py` fails when the template is out of date or a string is untranslated.

**Adding a language:** run `pybabel init -i tracker/locale/messages.pot -d tracker/locale -l <code>`,
translate the new `messages.po`, and add the code to `LANGUAGES` in `tracker/i18n.py`.

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Keep each PR focused on one thing, and describe what changed and why.
3. Make sure `pytest` passes. CI runs the full suite against Postgres on every PR.
4. Update the docs (README, `docs/`, `CONTEXT.md`) when behaviour or vocabulary changes.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
