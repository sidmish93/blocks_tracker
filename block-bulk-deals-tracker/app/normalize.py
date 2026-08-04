import re

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

# The two exchanges spell the same counterparty differently often enough that
# clubbing has to happen on a canonical form rather than the raw string.
_TOKEN_ALIASES = {
    "LIMITED": "LTD",
    "PRIVATE": "PVT",
    "COMPANY": "CO",
    "CORPORATION": "CORP",
    "INCORPORATED": "INC",
    "AND": "&",
    "PUBLIC": "PUB",
    "INTERNATIONAL": "INTL",
    "INVESTMENTS": "INVESTMENT",
    "SECURITIES": "SEC",
    "SERVICES": "SERVICE",
}

_DROP_TOKENS = {"THE", "A"}


def canonical(text: str) -> str:
    if not text:
        return ""
    cleaned = _NON_ALNUM.sub(" ", text.upper()).strip()
    tokens = [_TOKEN_ALIASES.get(token, token) for token in cleaned.split()]
    tokens = [token for token in tokens if token not in _DROP_TOKENS]
    return " ".join(tokens)


_COMPANY_SUFFIXES = ("LTD", "PVT LTD", "PVT", "CORP", "INC", "PLC")


def canonical_company(name: str) -> str:
    text = canonical(name)
    changed = True
    while changed:
        changed = False
        for suffix in _COMPANY_SUFFIXES:
            if text.endswith(" " + suffix):
                text = text[: -(len(suffix) + 1)].strip()
                changed = True
    return text


def title_case_company(name: str) -> str:
    """NSE and BSE both ship shouty names; render them readably without
    mangling short all-caps tokens that are genuine acronyms."""
    if not name:
        return ""
    if not name.isupper():
        return name.strip()
    words = []
    for word in name.strip().split():
        if len(word) <= 3 and word.isalpha():
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


def to_number(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("\u20b9", "").strip()
    if not text or text == "-":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0
