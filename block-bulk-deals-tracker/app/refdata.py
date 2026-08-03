import csv
import io
import threading
from datetime import datetime

from .config import REFDATA_TTL_SECONDS
from .http_client import bse_get_json, plain_get_text, read_cache, write_cache

NSE_SOURCES = [
    (
        "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        "SYMBOL",
        "NAME OF COMPANY",
        "ISIN NUMBER",
        "DATE OF LISTING",
    ),
    (
        "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv",
        "SYMBOL",
        "NAME_OF_COMPANY",
        "ISIN_NUMBER",
        "DATE_OF_LISTING",
    ),
    (
        "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv",
        "Symbol",
        "SecurityName",
        "ISINNumber",
        "DateofListing",
    ),
]

BSE_SCRIP_MASTER = (
    "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
    "?Group=&Scripcode=&industry=&segment={segment}&status="
)

_lock = threading.Lock()
_cache = {}


def _parse_listing_date(text: str) -> str:
    """The three NSE lists use two-digit and four-digit years interchangeably."""
    text = (text or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _load_nse_master() -> dict:
    cached = read_cache("nse_master_v2.json", REFDATA_TTL_SECONDS)
    if cached is not None:
        return cached

    master = {}
    for url, symbol_key, name_key, isin_key, listing_key in NSE_SOURCES:
        try:
            text = plain_get_text(url).lstrip("\ufeff")
        except Exception:
            continue  # a missing supplementary list must not break the main one
        for row in csv.DictReader(io.StringIO(text)):
            clean = {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            symbol = clean.get(symbol_key, "").upper()
            if not symbol or symbol in master:
                continue
            master[symbol] = {
                "name": clean.get(name_key, ""),
                "isin": clean.get(isin_key, ""),
                "listing_date": _parse_listing_date(clean.get(listing_key, "")),
            }

    if master:
        write_cache("nse_master_v2.json", master)
    return master


def _parse_bse_records(payload) -> dict:
    records = payload if isinstance(payload, list) else payload.get("Table", [])
    master = {}
    for record in records:
        code = str(record.get("SCRIP_CD") or "").strip()
        if not code:
            continue
        master[code] = {
            "name": (record.get("Scrip_Name") or record.get("Issuer_Name") or "").strip(),
            "isin": (record.get("ISIN_NUMBER") or "").strip(),
            "ticker": (record.get("scrip_id") or "").strip().upper(),
        }
    return master


def _load_bse_equity_master() -> dict:
    cached = read_cache("bse_master_equity.json", REFDATA_TTL_SECONDS)
    if cached is not None:
        return cached
    master = _parse_bse_records(bse_get_json(BSE_SCRIP_MASTER.format(segment="Equity")))
    if master:
        write_cache("bse_master_equity.json", master)
    return master


def _load_bse_full_master() -> dict:
    """Covers InvITs, REITs and other non-equity segments. Large and slow, so it
    is only pulled when an equity lookup misses."""
    cached = read_cache("bse_master_full.json", REFDATA_TTL_SECONDS)
    if cached is not None:
        return cached
    master = _parse_bse_records(bse_get_json(BSE_SCRIP_MASTER.format(segment="All")))
    if master:
        write_cache("bse_master_full.json", master)
    return master


def _get(key, loader):
    with _lock:
        if key not in _cache:
            try:
                _cache[key] = loader()
            except Exception:
                # Reference data only enriches output; deals must still load.
                _cache[key] = {}
        return _cache[key]


def nse_master() -> dict:
    return _get("nse", _load_nse_master)


def bse_master() -> dict:
    return _get("bse_equity", _load_bse_equity_master)


def bse_full_master() -> dict:
    return _get("bse_full", _load_bse_full_master)


def nse_lookup(symbol: str) -> dict:
    return nse_master().get((symbol or "").upper(), {})


def bse_lookup(scrip_code) -> dict:
    code = str(scrip_code or "").strip()
    if not code:
        return {}
    found = bse_master().get(code)
    if found:
        return found
    return bse_full_master().get(code, {})
