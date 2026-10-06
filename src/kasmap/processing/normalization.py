"""Normalization of names, phones and websites (pure Python, no third-party deps).

Names in Kazakhstan appear in Russian, Kazakh and Latin script, often for the same
place ("Старбакс" / "Starbucks").  For matching we compare transliterated, lower-cased
"core" names with legal forms, branch numbers and generic category words removed.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

# Russian + Kazakh Cyrillic → Latin (simple, matching-oriented, not a standard).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
    # Kazakh-specific letters
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h",
    "і": "i",
}

LEGAL_FORMS = frozenset({
    "тоо", "ип", "ао", "оао", "зао", "ооо", "чп", "кх", "жшс", "too", "llp", "llc", "ltd",
    "inc", "gmbh", "corp", "co", "jsc", "plc", "sa", "ltda", "kk",
})

# Words that describe the category rather than identify the business.
GENERIC_WORDS = frozenset({
    # ru
    "кафе", "кофейня", "кофе", "ресторан", "бар", "аптека", "магазин", "маркет", "минимаркет",
    "супермаркет", "пекарня", "салон", "красоты", "барбершоп", "фитнес", "клуб", "клиника",
    "стоматология", "филиал", "центр", "торговый", "гипермаркет", "продукты",
    # kk
    "дәріхана", "дүкен", "мейрамхана", "кофехана", "наубайхана", "емхана",
    # en
    "cafe", "coffee", "shop", "restaurant", "pharmacy", "store", "market", "minimarket",
    "supermarket", "bakery", "salon", "beauty", "barbershop", "fitness", "club", "clinic",
    "dental", "center", "centre", "branch", "the",
})

# Transliterated generic words, so core names match across scripts.
_GENERIC_LATIN: frozenset[str] = frozenset()  # filled below

_BRANCH_RE = re.compile(r"(?:#|№|no\.?\s*)\s*\d+|\b\d+\s*(?:филиал|branch)\b", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """NFKC, casefold, unify quotes/dashes, collapse whitespace."""
    if not value:
        return ""
    s = unicodedata.normalize("NFKC", value).casefold()
    s = s.replace("ё", "е")
    s = _NON_WORD_RE.sub(" ", s)
    s = s.replace("_", " ")
    return _SPACES_RE.sub(" ", s).strip()


def transliterate(value: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in value)


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def name_norm(value: str | None) -> str:
    """Normalized, Latin-script name for search and storage (`poi.place.name_norm`)."""
    return strip_accents(transliterate(normalize_text(value)))


def core_name(value: str | None) -> str:
    """Identity-bearing part of a name: no legal forms, branch numbers, generic words."""
    if not value:
        return ""
    s = _BRANCH_RE.sub(" ", unicodedata.normalize("NFKC", value))
    tokens = normalize_text(s).split()
    kept = [t for t in tokens if t not in LEGAL_FORMS and t not in GENERIC_WORDS]
    latin = [strip_accents(transliterate(t)) for t in kept]
    latin = [t for t in latin if t not in LEGAL_FORMS and t not in _GENERIC_LATIN]
    if not latin:  # the name was only generic words ("Аптека") — keep it, weakly
        latin = [strip_accents(transliterate(t)) for t in tokens]
    return " ".join(latin)


_GENERIC_LATIN = frozenset(strip_accents(transliterate(w)) for w in GENERIC_WORDS)

# ───────────────────────────── phones ─────────────────────────────

# Calling code and national significant number length for countries we test first.
_PHONE_RULES: dict[str, tuple[str, tuple[int, ...]]] = {
    "KZ": ("7", (10,)),
    "RU": ("7", (10,)),
    "US": ("1", (10,)),
    "DE": ("49", (6, 7, 8, 9, 10, 11)),
    "AE": ("971", (8, 9)),
    "JP": ("81", (9, 10)),
    "BR": ("55", (10, 11)),
}


def normalize_phone(raw: str | None, country_iso2: str = "KZ") -> str | None:
    """Best-effort E.164.  Returns None when the number cannot be interpreted."""
    if not raw:
        return None
    raw = raw.strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if raw.startswith("+") or raw.startswith("00"):
        digits = digits[2:] if raw.startswith("00") else digits
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    rule = _PHONE_RULES.get(country_iso2.upper())
    if rule is None:
        return None
    code, lengths = rule
    if code == "7" and len(digits) == 11 and digits[0] in "78":
        return f"+7{digits[1:]}"
    if code == "1" and len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    if digits.startswith(code) and len(digits) - len(code) in lengths:
        return f"+{digits}"
    national = digits[1:] if digits.startswith("0") else digits
    if len(national) in lengths:
        return f"+{code}{national}"
    return None


# ──────────────────────────── websites ────────────────────────────

# Hosts that do not identify a business (social networks, aggregators, link hubs).
NON_IDENTIFYING_HOSTS = frozenset({
    "instagram.com", "facebook.com", "fb.com", "vk.com", "t.me", "wa.me", "whatsapp.com",
    "2gis.kz", "2gis.ru", "linktr.ee", "taplink.cc", "tiktok.com", "youtube.com",
    "google.com", "goo.gl", "yandex.kz", "yandex.ru", "ok.ru", "twitter.com", "x.com",
})


def normalize_domain(url: str | None) -> str | None:
    """Lower-cased host without 'www.'; None for social/aggregator links."""
    if not url:
        return None
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        host = urlsplit(candidate).hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    host = host.lower().removeprefix("www.")
    if host in NON_IDENTIFYING_HOSTS or any(host.endswith("." + h) for h in NON_IDENTIFYING_HOSTS):
        return None
    return host
