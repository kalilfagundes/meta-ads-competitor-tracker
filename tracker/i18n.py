"""Interface translations: English (source), Spanish, Portuguese.

Strings are marked in the code with `_()` / `ngettext()` (templates, JS) or
`N_()` (Python constants translated later), extracted with Babel into
`tracker/locale/messages.pot`, and translated in standard gettext catalogs,
`tracker/locale/<lang>/LC_MESSAGES/messages.po`. See CONTRIBUTING.md.

The catalogs are read straight from the `.po` files (no compile step). A string
missing from a catalog falls back to English. Placeholders use Python's
`%(name)s` style everywhere, JS included; a literal percent sign is `%%`.

The language is an instance setting (chosen in the setup wizard, changed in
Settings); until it's chosen, the browser's Accept-Language decides, else English.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from functools import lru_cache
from pathlib import Path

from babel import Locale
from babel.messages.pofile import read_po

LANGUAGES = {"en": "English", "es": "Español", "pt": "Português"}
DEFAULT_LANGUAGE = "en"
LOCALE_DIR = Path(__file__).parent / "locale"


def N_(message: str) -> str:
    """Mark a string for extraction without translating it yet."""
    return message


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str | tuple[str, ...]]:
    """msgid -> translation (a tuple of plural forms for plural messages)."""
    path = LOCALE_DIR / lang / "LC_MESSAGES" / "messages.po"
    if lang == DEFAULT_LANGUAGE or not path.exists():
        return {}
    with path.open("rb") as fh:
        po = read_po(fh, locale=lang)
    out: dict[str, str | tuple[str, ...]] = {}
    for msg in po:
        if not msg.id or msg.fuzzy:
            continue
        if isinstance(msg.id, tuple):
            forms = tuple(msg.string)
            if all(forms):
                out[msg.id[0]] = forms
        elif msg.string:
            out[msg.id] = msg.string
    return out


def gettext(lang: str, message: str) -> str:
    value = catalog(lang).get(message)
    return value if isinstance(value, str) else message


def ngettext(lang: str, singular: str, plural: str, n: int) -> str:
    # English, Spanish and Portuguese all use "one" for exactly 1, "other" otherwise.
    value = catalog(lang).get(singular)
    if isinstance(value, tuple):
        return value[0] if n == 1 else value[-1]
    return singular if n == 1 else plural


def translate(lang: str, message: str, **params) -> str:
    """gettext + `%(name)s` formatting, for strings built in Python."""
    return gettext(lang, message) % params if params else gettext(lang, message)


def negotiate(preferred: str | None, accept_language: str | None) -> str:
    """The language to serve: the chosen one, else the browser's preference."""
    if preferred in LANGUAGES:
        return preferred
    choices = []
    for i, part in enumerate((accept_language or "").split(",")):
        tag, _, q = part.strip().partition(";q=")
        base = tag.split("-")[0].lower()
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        if base in LANGUAGES and weight > 0:
            choices.append((-weight, i, base))
    return min(choices)[2] if choices else DEFAULT_LANGUAGE


def guess_country(accept_language: str | None, lang: str) -> str:
    """A sensible default collection country from the browser (pt-BR -> BR)."""
    for part in (accept_language or "").split(","):
        tag = part.strip().split(";")[0]
        if "-" in tag:
            region = tag.split("-")[-1].upper()
            if len(region) == 2 and region.isalpha():
                return region
    return {"pt": "BR", "es": "ES"}.get(lang, "US")


@lru_cache(maxsize=None)
def countries(lang: str) -> list[tuple[str, str]]:
    """(ISO-2 code, name in `lang`) for every country, sorted by name."""
    # The same codes in every language (CLDR locales differ slightly), named in `lang`.
    english = Locale.parse(DEFAULT_LANGUAGE).territories
    names = Locale.parse(lang).territories
    return sorted(
        ((code, names.get(code, name)) for code, name in english.items()
         if len(code) == 2 and code.isalpha() and code not in _NOT_COUNTRIES),
        key=lambda item: _sort_key(item[1]),
    )


def _sort_key(name: str) -> str:
    """Accent-insensitive, so "Áustria" sorts with the A's."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


# ISO-2-shaped territory codes that aren't countries Meta can target.
_NOT_COUNTRIES = {"EU", "EZ", "UN", "QO", "XA", "XB", "ZZ"}


@lru_cache(maxsize=None)
def js_catalog(lang: str) -> str:
    """The catalog as JSON, for `window.I18N` (plural messages become arrays)."""
    return json.dumps(catalog(lang), ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=None)
def catalog_version(lang: str) -> str:
    """Changes whenever the catalog does (cache-busts the JS catalog URL)."""
    return hashlib.sha1(js_catalog(lang).encode()).hexdigest()[:10]
