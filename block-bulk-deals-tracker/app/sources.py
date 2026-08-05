import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime

from .config import (
    BSE_DATA_START,
    CLOSED_PERIOD_TTL_SECONDS,
    NSE_DATA_START,
    OPEN_PERIOD_TTL_SECONDS,
)
from .http_client import bse_get_json, nse_get_text, read_cache, write_cache
from .normalize import to_number

BULK = "Bulk"
BLOCK = "Block"

NSE_DEALS_URL = (
    "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
    "?optionType={option}&from={start}&to={end}&csv=true"
)
BSE_DEALS_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/BulkDealData_ng/w"
    "?DealType={deal_type}&sc_code={scrip}&FDate={start}&TDate={end}"
)


@dataclass
class Leg:
    """One disclosed side (buy or sell) of one deal, as reported by an exchange."""

    exchange: str
    deal_type: str
    trade_date: date
    symbol: str
    security_name: str
    scrip_code: str
    client: str
    side: str
    quantity: float
    price: float
    consumed: bool = field(default=False, compare=False)

    @property
    def value(self) -> float:
        return self.quantity * self.price


def _calendar_years(start: date, end: date):
    """Whole calendar years, so cache keys stay stable as 'today' moves and any
    two overlapping queries reuse the same downloads."""
    for year in range(start.year, end.year + 1):
        yield date(year, 1, 1), date(year, 12, 31)


def _ttl_for(chunk_end: date) -> int:
    return CLOSED_PERIOD_TTL_SECONDS if chunk_end < date.today() else OPEN_PERIOD_TTL_SECONDS


def _cached_fetch(cache_name: str, chunk_end: date, fetch):
    cached = read_cache(cache_name, _ttl_for(chunk_end))
    if cached is not None:
        return cached
    payload = fetch()
    write_cache(cache_name, payload)
    return payload


def _fetch_nse_chunk(deal_type: str, start: date, end: date, symbol: str) -> list:
    option = "bulk_deals" if deal_type == BULK else "block_deals"
    scope = symbol or "all"
    name = f"nse_{option}_{scope}_{start:%Y%m%d}_{end:%Y%m%d}.json"

    def fetch():
        url = NSE_DEALS_URL.format(
            option=option, start=start.strftime("%d-%m-%Y"), end=end.strftime("%d-%m-%Y")
        )
        if symbol:
            url += f"&symbol={symbol}"
        text = nse_get_text(url).lstrip("\ufeff")
        return [
            {(key or "").strip(): (value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(io.StringIO(text))
        ]

    return _cached_fetch(name, end, fetch)


def fetch_nse(deal_type: str, start: date, end: date, symbol: str = "") -> list:
    start = max(start, NSE_DATA_START)
    if start > end:
        return []

    legs = []
    for chunk_start, chunk_end in _calendar_years(start, end):
        for row in _fetch_nse_chunk(deal_type, chunk_start, chunk_end, symbol):
            raw_date = row.get("Date", "")
            if not raw_date:
                continue
            try:
                trade_date = datetime.strptime(raw_date, "%d-%b-%Y").date()
            except ValueError:
                continue
            if not start <= trade_date <= end:
                continue
            side = "BUY" if row.get("Buy / Sell", "").upper().startswith("B") else "SELL"
            row_symbol = row.get("Symbol", "").upper()
            legs.append(
                Leg(
                    exchange="NSE",
                    deal_type=deal_type,
                    trade_date=trade_date,
                    symbol=row_symbol,
                    security_name=row.get("Security Name", "") or row_symbol,
                    scrip_code="",
                    client=row.get("Client Name", ""),
                    side=side,
                    quantity=to_number(row.get("Quantity Traded")),
                    price=to_number(row.get("Trade Price / Wght. Avg. Price")),
                )
            )
    return legs


def _fetch_bse_chunk(deal_type: str, start: date, end: date, scrip_code: str) -> list:
    code = 1 if deal_type == BULK else 2
    scope = scrip_code or "all"
    name = f"bse_{deal_type.lower()}_{scope}_{start:%Y%m%d}_{end:%Y%m%d}.json"

    def fetch():
        url = BSE_DEALS_URL.format(
            deal_type=code,
            scrip=scrip_code,
            start=start.strftime("%d/%m/%Y"),
            end=end.strftime("%d/%m/%Y"),
        )
        payload = bse_get_json(url)
        if isinstance(payload, list):
            return payload
        return payload.get("Table", []) or []

    return _cached_fetch(name, end, fetch)


def fetch_bse(deal_type: str, start: date, end: date, scrip_code: str = "") -> list:
    start = max(start, BSE_DATA_START)
    if start > end:
        return []

    # Filtered by scrip the response is small, so the whole history fits in one
    # request; unfiltered it has to be broken up to stay under BSE's row cap.
    if scrip_code:
        windows = [(start, end)]
    else:
        windows = list(_calendar_years(start, end))

    legs = []
    for chunk_start, chunk_end in windows:
        for row in _fetch_bse_chunk(deal_type, chunk_start, chunk_end, scrip_code):
            raw_date = str(row.get("DEAL_DATE") or "")
            if not raw_date:
                continue
            try:
                trade_date = datetime.fromisoformat(raw_date).date()
            except ValueError:
                continue
            if not start <= trade_date <= end:
                continue
            side = "BUY" if str(row.get("TRANSACTION_TYPE", "")).upper().startswith("P") else "SELL"
            legs.append(
                Leg(
                    exchange="BSE",
                    deal_type=deal_type,
                    trade_date=trade_date,
                    symbol=str(row.get("scripname") or "").upper(),
                    security_name="",
                    scrip_code=str(row.get("SCRIP_CODE") or "").strip(),
                    client=str(row.get("CLIENT_NAME") or ""),
                    side=side,
                    quantity=to_number(row.get("QUANTITY")),
                    price=to_number(row.get("PRICE")),
                )
            )
    return legs


def fetch_all(start: date, end: date, company=None):
    """Collect every leg in the window. When a company is given, each exchange is
    queried only if the company trades there."""
    nse_symbol = company.nse_symbol if company else ""
    bse_code = company.bse_code if company else ""
    use_nse = nse_symbol or company is None
    use_bse = bse_code or company is None

    tasks = []
    if use_nse:
        tasks.append(("NSE block deals", lambda: fetch_nse(BLOCK, start, end, nse_symbol)))
        tasks.append(("NSE bulk deals", lambda: fetch_nse(BULK, start, end, nse_symbol)))
    if use_bse:
        tasks.append(("BSE block deals", lambda: fetch_bse(BLOCK, start, end, bse_code)))
        tasks.append(("BSE bulk deals", lambda: fetch_bse(BULK, start, end, bse_code)))

    legs = []
    errors = []
    for label, fetch in tasks:
        try:
            legs.extend(fetch())
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    return legs, errors
