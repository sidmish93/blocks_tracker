"""Turn whatever the user types into a company the exchanges can be queried for.

People write "Delhivery", "Delhivery Ltd", "Delhivery Limited" or "Delhivery Pvt
Ltd" for the same business, while NSE only accepts a symbol. This module builds
one index from both exchanges' scrip masters and matches loosely against it.
"""

import difflib
import threading
from dataclasses import dataclass, field
from datetime import date

from .normalize import canonical, canonical_company, title_case_company
from .refdata import bse_full_master, bse_master, nse_master

# Scores above this are treated as a confident, unambiguous identification.
CONFIDENT_SCORE = 94


@dataclass
class Company:
    key: str
    name: str = ""
    isin: str = ""
    nse_symbol: str = ""
    bse_code: str = ""
    bse_ticker: str = ""
    listing_date: str = ""
    canonical_name: str = field(default="", repr=False)

    @property
    def ticker(self) -> str:
        return self.nse_symbol or self.bse_ticker

    @property
    def exchanges(self) -> list:
        found = []
        if self.nse_symbol:
            found.append("NSE")
        if self.bse_code:
            found.append("BSE")
        return found

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "ticker": self.ticker,
            "nse_symbol": self.nse_symbol,
            "bse_code": self.bse_code,
            "isin": self.isin,
            "listing_date": self.listing_date,
            "exchanges": self.exchanges,
        }

    def listing_day(self):
        try:
            return date.fromisoformat(self.listing_date)
        except ValueError:
            return None


_lock = threading.Lock()
_index = None
_extended_loaded = False


def _register(index, key, **fields):
    company = index.get(key)
    if company is None:
        company = Company(key=key)
        index[key] = company
    for name, value in fields.items():
        if value and not getattr(company, name):
            setattr(company, name, value)
    return company


def _finalise(company):
    if not company.name:
        company.name = company.ticker
    company.name = title_case_company(company.name)
    company.canonical_name = canonical_company(company.name)


def _searchable(code: str, record: dict) -> bool:
    """BSE's 75xxxx block holds short-lived rights entitlements whose 'name' is
    just the ticker. They are tradeable but they are not companies."""
    if code.startswith("75") and len(code) == 6:
        return False
    name = (record.get("name") or "").strip()
    if not name:
        return False
    if name.upper() == (record.get("ticker") or "").strip().upper() and not record.get("isin"):
        return False
    return True


def _build_index() -> dict:
    index = {}

    for symbol, record in nse_master().items():
        name = record.get("name", "")
        key = record.get("isin") or canonical_company(name) or symbol
        _register(
            index,
            key,
            name=name,
            isin=record.get("isin", ""),
            nse_symbol=symbol,
            listing_date=record.get("listing_date", ""),
        )

    for code, record in bse_master().items():
        if not _searchable(code, record):
            continue
        name = record.get("name", "")
        key = record.get("isin") or canonical_company(name) or code
        _register(
            index,
            key,
            name=name,
            isin=record.get("isin", ""),
            bse_code=code,
            bse_ticker=record.get("ticker", ""),
        )

    for company in index.values():
        _finalise(company)
    return index


def _load_extended(index) -> None:
    """InvITs, REITs and similar sit outside the equity master. That list is slow
    to pull, so it is only merged in when an ordinary search finds nothing."""
    global _extended_loaded
    if _extended_loaded:
        return
    _extended_loaded = True

    equity_codes = {company.bse_code for company in index.values() if company.bse_code}
    for code, record in bse_full_master().items():
        if code in equity_codes or not _searchable(code, record):
            continue
        name = record.get("name", "")
        key = record.get("isin") or canonical_company(name) or code
        company = _register(
            index,
            key,
            name=name,
            isin=record.get("isin", ""),
            bse_code=code,
            bse_ticker=record.get("ticker", ""),
        )
        _finalise(company)


def _get_index() -> dict:
    global _index
    with _lock:
        if _index is None:
            try:
                _index = _build_index()
            except Exception:
                _index = {}
        return _index


def _base_score(company: Company, query_name: str, query_raw: str) -> int:
    tickers = {company.nse_symbol, company.bse_ticker} - {""}
    if query_raw in tickers or query_name in tickers:
        return 100
    # Punctuated tickers such as M&M never survive canonicalisation intact, so
    # they are compared in canonical form on both sides.
    if query_raw and query_raw in {canonical(ticker) for ticker in tickers}:
        return 100
    if query_name and query_name == company.canonical_name:
        return CONFIDENT_SCORE + 1
    if query_raw and query_raw == canonical(company.name):
        return CONFIDENT_SCORE

    if len(query_raw) >= 3 and any(ticker.startswith(query_raw) for ticker in tickers):
        return 78
    if query_name and company.canonical_name.startswith(query_name + " "):
        return 72
    if query_name and query_name in company.canonical_name:
        return 58

    query_tokens = query_name.split()
    if query_tokens:
        name_tokens = set(company.canonical_name.split())
        if all(token in name_tokens for token in query_tokens):
            return 52
    return 0


def _score(company: Company, query_name: str, query_raw: str) -> int:
    if not company.canonical_name and not company.ticker:
        return 0
    score = _base_score(company, query_name, query_raw)
    if not score:
        return 0
    # Nudge the ordinary NSE-listed line ahead of DVR, partly-paid and
    # BSE-only variants that share a name.
    if company.nse_symbol:
        score += 2
    return score


def _ranked(index, query: str, limit: int):
    query_name = canonical_company(query)
    query_raw = canonical(query)
    if not query_name and not query_raw:
        return []

    scored = []
    for company in index.values():
        score = _score(company, query_name, query_raw)
        if score:
            scored.append((score, company))

    if not scored:
        scored = _fuzzy(index, query_name, limit)

    scored.sort(key=lambda item: (-item[0], len(item[1].name), item[1].name))
    return scored[:limit]


def _fuzzy(index, query_name: str, limit: int):
    """Last resort for typos, so a near miss suggests something instead of
    dead-ending on 'no match'."""
    if len(query_name) < 4:
        return []
    by_name = {}
    for company in index.values():
        by_name.setdefault(company.canonical_name, company)
    close = difflib.get_close_matches(query_name, by_name.keys(), n=limit, cutoff=0.72)
    return [(40, by_name[name]) for name in close]


def search(query: str, limit: int = 8) -> list:
    index = _get_index()
    ranked = _ranked(index, query, limit)
    if not ranked:
        _load_extended(index)
        ranked = _ranked(index, query, limit)
    return [company for _, company in ranked]


def resolve(query: str):
    """Return (company, alternatives). A company is returned only when the match
    is confident and unrivalled; otherwise the caller should ask the user."""
    index = _get_index()
    ranked = _ranked(index, query, 8)
    if not ranked:
        _load_extended(index)
        ranked = _ranked(index, query, 8)
    if not ranked:
        return None, []

    top_score, top_company = ranked[0]
    if len(ranked) == 1 and top_score >= 50:
        return top_company, []
    if top_score >= CONFIDENT_SCORE and ranked[1][0] < top_score - 1:
        return top_company, []
    return None, [company for _, company in ranked]


def get(key: str):
    index = _get_index()
    company = index.get(key)
    if company is None:
        _load_extended(index)
        company = index.get(key)
    return company


def find_by_ticker(symbol: str):
    """Used to fill in a company's other listing: a deal reported only by one
    exchange still needs the counterpart code to price and size the company."""
    wanted = (symbol or "").strip().upper()
    if not wanted:
        return None

    index = _get_index()
    for _ in range(2):
        for attribute in ("nse_symbol", "bse_ticker"):
            for company in index.values():
                if getattr(company, attribute) == wanted:
                    return company
        # InvITs and REITs sit outside the equity masters; pull them in and retry.
        if _extended_loaded:
            break
        _load_extended(index)
    return None
