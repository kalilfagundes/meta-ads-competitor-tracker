"""Translation catalogs — no database, no network.

These keep the Spanish and Portuguese interfaces from silently falling back to
English: every string in the code must be in the template (`messages.pot`), and
every template string must be translated, with the same placeholders.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
from babel.messages.pofile import read_po

from tests.conftest import REPO_ROOT
from tracker import i18n

POT = REPO_ROOT / "tracker" / "locale" / "messages.pot"
_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def _ids(path):
    with path.open("rb") as fh:
        return {m.id if isinstance(m.id, str) else m.id[0] for m in read_po(fh) if m.id}


def test_template_is_in_sync_with_the_code(tmp_path):
    fresh = tmp_path / "messages.pot"
    subprocess.run(
        [sys.executable, "-m", "babel.messages.frontend", "extract", "-F", "babel.cfg",
         "-k", "N_", "-o", str(fresh), "."],
        cwd=str(REPO_ROOT), check=True, capture_output=True,
    )
    missing = _ids(fresh) - _ids(POT)
    stale = _ids(POT) - _ids(fresh)
    assert not missing and not stale, (
        "tracker/locale/messages.pot is out of date: run the extract/update commands in "
        f"CONTRIBUTING.md. New: {sorted(missing)[:5]} Removed: {sorted(stale)[:5]}"
    )


@pytest.mark.parametrize("lang", [code for code in i18n.LANGUAGES if code != i18n.DEFAULT_LANGUAGE])
def test_catalog_translates_every_string_with_the_same_placeholders(lang):
    with (REPO_ROOT / "tracker" / "locale" / lang / "LC_MESSAGES" / "messages.po").open("rb") as fh:
        catalog = read_po(fh)
    untranslated, wrong = [], []
    for msg in catalog:
        if not msg.id:
            continue
        ids = msg.id if isinstance(msg.id, tuple) else (msg.id,)
        strings = msg.string if isinstance(msg.string, tuple) else (msg.string,)
        if msg.fuzzy or not all(strings):
            untranslated.append(ids[0])
            continue
        expected = set(_PLACEHOLDER.findall(ids[-1]))
        # a plural's "one" form may leave out the number ("Every hour"), nothing else
        if set(_PLACEHOLDER.findall(strings[-1])) != expected or any(
                not set(_PLACEHOLDER.findall(s)) <= expected for s in strings[:-1]):
            wrong.append(ids[0])
    assert _ids(POT) <= {m.id if isinstance(m.id, str) else m.id[0] for m in catalog if m.id}
    assert not untranslated, f"{lang}: untranslated {untranslated[:5]}"
    assert not wrong, f"{lang}: placeholders differ {wrong[:5]}"


def test_lookup_falls_back_to_english():
    assert i18n.gettext("en", "Settings") == "Settings"
    assert i18n.gettext("pt", "not a catalog string") == "not a catalog string"
    assert i18n.gettext("pt", "Settings") != "Settings"
    assert i18n.ngettext("es", "%(num)s ad", "%(num)s ads", 1) != i18n.ngettext("es", "%(num)s ad", "%(num)s ads", 2)
    assert i18n.ngettext("xx", "%(num)s ad", "%(num)s ads", 2) == "%(num)s ads"


@pytest.mark.parametrize("cookie,header,expected", [
    ("pt", "es-ES", "pt"),                     # the switcher's choice wins
    ("xx", "es-MX,es;q=0.9", "es"),            # an unknown cookie is ignored
    (None, "fr-FR,pt-BR;q=0.8,en;q=0.5", "pt"),
    (None, "en;q=0.2,es;q=0.9", "es"),         # by weight, not order
    (None, "de-DE", "en"),
    (None, None, "en"),
    (None, "pt;q=abc", "en"),                  # malformed weight
])
def test_negotiate(cookie, header, expected):
    assert i18n.negotiate(cookie, header) == expected


def test_guess_country():
    assert i18n.guess_country("pt-BR,pt;q=0.9", "pt") == "BR"
    assert i18n.guess_country("es-MX", "es") == "MX"
    assert i18n.guess_country("en", "en") == "US"
    assert i18n.guess_country(None, "pt") == "BR"


def test_countries_are_iso2_and_localised():
    en, pt = dict(i18n.countries("en")), dict(i18n.countries("pt"))
    assert en.keys() == pt.keys()
    assert all(len(code) == 2 and code.isalpha() for code in en)
    assert {"US", "BR", "MX", "ES"} <= en.keys()
    assert "EU" not in en and "001" not in en
    assert en["DE"] != pt["DE"]
