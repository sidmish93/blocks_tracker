"""Per-company market data, computed from NSE's daily price/volume/delivery
archive, a generate-time live quote (CMP), and BSE market capitalisation.

Trailing 1D/1W/1M figures come from the latest NSE session. CMP / intraday /
daily returns prefer NSE live, then BSE live (NSE's quote API is often blocked),
then the last NSE history session.
"""

import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from .config import (
    CRORE,
    MAX_SESSION_GAP_DAYS,
    METRIC_HISTORY_DAYS,
    METRIC_WINDOWS,
    METRIC_WORKERS,
    OPEN_PERIOD_TTL_SECONDS,
    QUOTE_TTL_SECONDS,
    REFDATA_TTL_SECONDS,
)
from .http_client import bse_get_json, nse_get_text, read_cache, write_cache
from .normalize import to_number

NSE_HISTORY_URL = (
    "https://www.nseindia.com/api/historicalOR/generateSecurityWiseHistoricalData"
    "?from={start}&to={end}&symbol={symbol}&type=priceVolumeDeliverable&series={series}"
)
NSE_ACTIONS_URL = (
    "https://www.nseindia.com/api/corporates-corporateActions"
    "?index=equities&from_date={start}&to_date={end}"
)
NSE_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}"
BSE_QUOTE_URL = "https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?flag=&scripcode={code}"
BSE_HEADER_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
    "?Debtflag=&scripcode={code}"
)

# Ordinary equity, then the trade-for-trade, SME and InvIT/REIT segments. Nearly
# every security resolves on the first try.
NSE_SERIES = ["EQ", "BE", "SM", "ST", "IV"]

_SPLIT_FACES = re.compile(
    r"from\s+rs?e?\.?\s*([\d.]+).*?to\s+rs?e?\.?\s*([\d.]+)", re.IGNORECASE | re.DOTALL
)
_BONUS_RATIO = re.compile(r"bonus\D*(\d+)\s*:\s*(\d+)", re.IGNORECASE)
_UNSAFE = re.compile(r"[^A-Za-z0-9]+")


@dataclass
class Session:
    """One trading day for one security, as published by NSE."""

    day: date
    close: float
    quantity: float
    value: float
    # None where NSE publishes no deliverable data, as in the trade-for-trade
    # series. That is not the same as nothing having been delivered.
    delivery_qty: Optional[float] = None
    open: float = 0.0
    previous_close: float = 0.0
    last_traded: float = 0.0
    # Multiplier that restates this day's price in terms of today's share count.
    factor: float = 1.0

    @property
    def adjusted_close(self) -> float:
        return self.close * self.factor

    @property
    def adjusted_quantity(self) -> float:
        return self.quantity / self.factor if self.factor else self.quantity

    @property
    def adjusted_open(self) -> float:
        return self.open * self.factor if self.open else 0.0

    @property
    def adjusted_last_traded(self) -> float:
        price = self.last_traded or self.close
        return price * self.factor if price else 0.0


def _corporate_action_factor(subject: str):
    """Price multiplier applied to every session before the ex-date.

    A 10-to-2 face value split leaves five shares where there was one, so older
    prices are worth a fifth of their printed value once compared with today's.
    Dividends are ignored: these are price returns, not total returns. Rights
    issues are ignored too, since adjusting them needs the subscription terms.
    """
    text = subject or ""
    lowered = text.lower()

    if "split" in lowered or "sub-division" in lowered or "sub division" in lowered:
        match = _SPLIT_FACES.search(text)
        if match:
            old_face, new_face = float(match.group(1)), float(match.group(2))
            if old_face > 0 and new_face > 0 and new_face != old_face:
                return new_face / old_face
        return None

    if "bonus" in lowered:
        match = _BONUS_RATIO.search(text)
        if match:
            issued, held = float(match.group(1)), float(match.group(2))
            if issued > 0 and held > 0:
                return held / (issued + held)
    return None


def _load_corporate_actions(start: date, end: date) -> dict:
    """Splits and bonuses for the whole market in one request, keyed by symbol."""
    name = f"nse_corpactions_{start:%Y%m%d}_{end:%Y%m%d}.json"
    cached = read_cache(name, REFDATA_TTL_SECONDS)
    if cached is None:
        url = NSE_ACTIONS_URL.format(start=start.strftime("%d-%m-%Y"), end=end.strftime("%d-%m-%Y"))
        payload = json.loads(nse_get_text(url))
        cached = payload if isinstance(payload, list) else payload.get("data", [])
        write_cache(name, cached)

    actions = {}
    for record in cached:
        factor = _corporate_action_factor(record.get("subject", ""))
        if not factor:
            continue
        try:
            ex_date = datetime.strptime((record.get("exDate") or "").strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        symbol = (record.get("symbol") or "").strip().upper()
        if symbol:
            actions.setdefault(symbol, []).append((ex_date, factor))
    return actions


_actions_cache = {}


def corporate_actions(start: date, end: date) -> dict:
    key = (start, end)
    if key not in _actions_cache:
        try:
            _actions_cache[key] = _load_corporate_actions(start, end)
        except Exception:
            # Adjustment is a refinement; unadjusted prices still beat no prices.
            _actions_cache[key] = {}
    return _actions_cache[key]


def parse_session(record) -> Session:
    delivered = record.get("COP_DELIV_QTY")
    return Session(
        day=datetime.strptime(record["mTIMESTAMP"], "%d-%b-%Y").date(),
        close=to_number(record.get("CH_CLOSING_PRICE")),
        quantity=to_number(record.get("CH_TOT_TRADED_QTY")),
        value=to_number(record.get("CH_TOT_TRADED_VAL")),
        delivery_qty=None if delivered in (None, "", "-") else to_number(delivered),
        open=to_number(record.get("CH_OPENING_PRICE")),
        previous_close=to_number(record.get("CH_PREVIOUS_CLS_PRICE")),
        last_traded=to_number(record.get("CH_LAST_TRADED_PRICE")),
    )


def fetch_series(symbol: str, series: str, start: date, end: date, ttl: int = None) -> list:
    slug = _UNSAFE.sub("-", symbol).strip("-").upper() or "unknown"
    name = f"nse_hist_{slug}_{series}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    cached = read_cache(name, OPEN_PERIOD_TTL_SECONDS if ttl is None else ttl)
    if cached is None:
        url = NSE_HISTORY_URL.format(
            start=start.strftime("%d-%m-%Y"),
            end=end.strftime("%d-%m-%Y"),
            symbol=urllib.parse.quote(symbol),
            series=series,
        )
        payload = json.loads(nse_get_text(url))
        cached = payload if isinstance(payload, list) else payload.get("data", [])
        write_cache(name, cached)
    return cached


def apply_adjustments(sessions, actions) -> list:
    """Restate pre-event sessions in terms of the current share count, so a split
    or bonus does not read as a collapse in the price."""
    for ex_date, factor in actions:
        for session in sessions:
            if session.day < ex_date:
                session.factor *= factor
    return sessions


def fetch_history(symbol: str, start: date, end: date) -> list:
    """Daily sessions for a symbol, oldest first, adjusted for splits and bonuses."""
    if not symbol:
        return []

    by_day = {}
    for series in NSE_SERIES:
        for record in fetch_series(symbol, series, start, end):
            try:
                session = parse_session(record)
            except (KeyError, ValueError):
                continue
            if session.close > 0 and session.quantity > 0:
                by_day.setdefault(session.day, session)
        # A security moved between segments leaves the series it came from
        # stopped dead, so a trail that ends early means the rest is elsewhere.
        # Ordinary equity answers for almost everything, ending this at once.
        if by_day and max(by_day) >= end - timedelta(days=MAX_SESSION_GAP_DAYS):
            break

    if not by_day:
        return []
    sessions = sorted(by_day.values(), key=lambda item: item.day)
    return apply_adjustments(sessions, corporate_actions(start, end).get(symbol.upper(), []))


def _window(sessions, as_of: date, days: int):
    """Sessions inside the trailing window, plus the close it is measured against."""
    cutoff = as_of - timedelta(days=days)
    inside = [session for session in sessions if session.day > cutoff]
    earlier = [session for session in sessions if session.day <= cutoff]
    return inside, (earlier[-1] if earlier else None)


def _measure(sessions) -> dict:
    latest = sessions[-1]
    as_of = latest.day
    measures = {"as_of": as_of.isoformat(), "close": round(latest.adjusted_close, 2)}

    for label, days in METRIC_WINDOWS:
        inside, reference = _window(sessions, as_of, days)
        if not inside:
            continue

        traded_value = sum(session.value for session in inside)
        traded_qty = sum(session.adjusted_quantity for session in inside)

        if reference and reference.adjusted_close > 0:
            change = latest.adjusted_close / reference.adjusted_close - 1
            measures[f"return_{label}_pct"] = round(change * 100, 2)
        measures[f"adtv_{label}_cr"] = round(traded_value / len(inside) / CRORE, 2)
        if traded_qty > 0:
            measures[f"vwap_{label}"] = round(traded_value / traded_qty, 2)

        # Delivery is a ratio over the sessions that actually report it, so a
        # series NSE does not publish it for is left blank rather than as zero.
        disclosed = [session for session in inside if session.delivery_qty is not None]
        disclosed_qty = sum(session.quantity for session in disclosed)
        if disclosed_qty > 0:
            delivered = sum(session.delivery_qty for session in disclosed)
            measures[f"delivery_{label}_pct"] = round(delivered / disclosed_qty * 100, 2)

        measures[f"sessions_{label}"] = len(inside)

    return measures


def _market_cap_cr(scrip_code: str):
    code = str(scrip_code or "").strip()
    if not code:
        return None
    name = f"bse_mktcap_{code}.json"
    cached = read_cache(name, OPEN_PERIOD_TTL_SECONDS)
    if cached is None:
        cached = bse_get_json(BSE_QUOTE_URL.format(code=code)) or {}
        write_cache(name, cached)
    value = to_number(cached.get("MktCapFull"))
    return round(value, 2) if value > 0 else None


def _pct_move(cmp: float, base) -> float | None:
    """(CMP − base) / base as a percentage; blank when the base is missing."""
    if cmp is None or not base or base <= 0:
        return None
    return round((cmp / base - 1) * 100, 2)


def _live_fields(cmp, open_price, previous_close, quote_time=None) -> dict:
    fields = {
        "cmp": round(cmp, 2) if cmp and cmp > 0 else None,
        "open": round(open_price, 2) if open_price and open_price > 0 else None,
        "previous_close": round(previous_close, 2) if previous_close and previous_close > 0 else None,
        "quote_time": quote_time or None,
    }
    if fields["cmp"] is None:
        return {"cmp": None, "open": None, "previous_close": None, "quote_time": None,
                "intraday_return_pct": None, "daily_return_pct": None}
    fields["intraday_return_pct"] = _pct_move(fields["cmp"], fields["open"])
    fields["daily_return_pct"] = _pct_move(fields["cmp"], fields["previous_close"])
    return fields


def _quote_from_nse_live(symbol: str) -> dict | None:
    """NSE live quote-equity. Often blocked (403); callers fall back to BSE."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return None
    slug = _UNSAFE.sub("-", symbol).strip("-") or "unknown"
    name = f"nse_quote_{slug}.json"
    cached = read_cache(name, QUOTE_TTL_SECONDS)
    if cached is None:
        # One attempt — hard blocks just burn retries before BSE/history.
        payload = json.loads(
            nse_get_text(NSE_QUOTE_URL.format(symbol=urllib.parse.quote(symbol)), attempts=1)
        )
        info = payload.get("priceInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            return None
        cached = {
            "cmp": to_number(info.get("lastPrice")),
            "open": to_number(info.get("open")),
            "previous_close": to_number(info.get("previousClose")),
            "quote_time": info.get("lastUpdateTime") or None,
            "source": "NSE live",
        }
        write_cache(name, cached)
    if not cached.get("cmp"):
        return None
    fields = _live_fields(
        cached.get("cmp"),
        cached.get("open"),
        cached.get("previous_close"),
        cached.get("quote_time"),
    )
    fields["quote_source"] = "NSE live"
    return fields


def _quote_from_bse_live(scrip_code: str) -> dict | None:
    """BSE getScripHeaderData LTP — reliable live feed when NSE quote is blocked."""
    code = str(scrip_code or "").strip()
    if not code:
        return None
    name = f"bse_quote_{code}.json"
    cached = read_cache(name, QUOTE_TTL_SECONDS)
    if cached is None:
        payload = bse_get_json(BSE_HEADER_URL.format(code=code)) or {}
        header = payload.get("Header") if isinstance(payload, dict) else None
        if not isinstance(header, dict):
            return None
        curr = payload.get("CurrRate") if isinstance(payload.get("CurrRate"), dict) else {}
        cached = {
            "cmp": to_number(header.get("LTP") or curr.get("LTP")),
            "open": to_number(header.get("Open")),
            "previous_close": to_number(header.get("PrevClose")),
            "quote_time": header.get("Ason") or None,
            "source": "BSE live",
        }
        write_cache(name, cached)
    if not cached.get("cmp"):
        return None
    fields = _live_fields(
        cached.get("cmp"),
        cached.get("open"),
        cached.get("previous_close"),
        cached.get("quote_time"),
    )
    fields["quote_source"] = "BSE live"
    return fields


def _quote_from_nse_history(sessions) -> dict | None:
    """Last NSE session stand-in when no live feed is available."""
    if not sessions:
        return None
    latest = sessions[-1]
    cmp = latest.adjusted_last_traded
    if not cmp:
        return None
    open_price = latest.adjusted_open or None
    if len(sessions) >= 2 and sessions[-2].adjusted_close > 0:
        previous_close = sessions[-2].adjusted_close
    elif latest.previous_close > 0:
        previous_close = latest.previous_close * latest.factor
    else:
        previous_close = None
    fields = _live_fields(
        cmp,
        open_price,
        previous_close,
        f"{latest.day.isoformat()} NSE session",
    )
    fields["quote_source"] = "NSE session"
    return fields


def fetch_live_quote(nse_symbol: str = "", bse_code: str = "", sessions=None) -> dict:
    """CMP + intraday/daily: NSE live → BSE live → last NSE session."""
    blank = _live_fields(None, None, None)
    blank["quote_source"] = None
    if nse_symbol:
        try:
            quote = _quote_from_nse_live(nse_symbol)
            if quote and quote.get("cmp"):
                return quote
        except Exception:
            pass
    if bse_code:
        try:
            quote = _quote_from_bse_live(bse_code)
            if quote and quote.get("cmp"):
                return quote
        except Exception:
            pass
    quote = _quote_from_nse_history(sessions)
    if quote and quote.get("cmp"):
        return quote
    return blank


@dataclass
class Target:
    """A company to report market data for."""

    key: str
    name: str
    ticker: str
    nse_symbol: str = ""
    bse_code: str = ""


def _for_target(target: Target, start: date, end: date) -> dict:
    result = {
        "key": target.key,
        "company_name": target.name,
        "ticker": target.ticker,
        "as_of": None,
        "market_cap_cr": None,
        "cmp": None,
        "open": None,
        "previous_close": None,
        "quote_time": None,
        "quote_source": None,
        "intraday_return_pct": None,
        "daily_return_pct": None,
        "_nse_failed": False,
        "_bse_failed": False,
        "_quote_failed": False,
    }

    sessions = []
    try:
        sessions = fetch_history(target.nse_symbol, start, end)
        if sessions:
            result.update(_measure(sessions))
        elif target.nse_symbol:
            result["_nse_failed"] = True
    except Exception:
        result["_nse_failed"] = True

    try:
        result["market_cap_cr"] = _market_cap_cr(target.bse_code)
        if result["market_cap_cr"] is None and target.bse_code:
            result["_bse_failed"] = True
    except Exception:
        result["_bse_failed"] = True

    quote = fetch_live_quote(
        nse_symbol=target.nse_symbol,
        bse_code=target.bse_code,
        sessions=sessions,
    )
    result.update(quote)
    if quote.get("cmp") is None:
        result["_quote_failed"] = True

    return result


def _names(rows) -> str:
    labels = [row["company_name"] for row in rows]
    if len(labels) <= 3:
        return ", ".join(labels)
    return f"{', '.join(labels[:3])} and {len(labels) - 3} others"


def collect(targets) -> tuple:
    """Market data for each target, fetched in parallel. Returns (rows, warnings)."""
    targets = [target for target in targets if target.nse_symbol or target.bse_code]
    if not targets:
        return [], []

    end = date.today()
    start = end - timedelta(days=METRIC_HISTORY_DAYS)

    # Warm the shared corporate-action download once, so the pool does not race
    # to fetch the same thing several times over.
    corporate_actions(start, end)

    workers = max(1, min(METRIC_WORKERS, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda target: _for_target(target, start, end), targets))

    nse_failed = [row for row in results if row.pop("_nse_failed", False)]
    bse_failed = [row for row in results if row.pop("_bse_failed", False)]
    quote_failed = [row for row in results if row.pop("_quote_failed", False)]
    # Unlisted trusts leave blank cells — no need to narrate each one.
    warnings = []
    if nse_failed:
        warnings.append(f"NSE history unavailable right now for {_names(nse_failed)}.")
    if bse_failed:
        warnings.append(f"BSE market cap unavailable right now for {_names(bse_failed)}.")
    if quote_failed:
        warnings.append(f"CMP unavailable right now for {_names(quote_failed)}.")

    for row in results:
        if row.get("quote_time"):
            source = row.get("quote_source") or "live"
            warnings.insert(0, f"CMP from {source} ({row['quote_time']}).")
            break

    results.sort(key=lambda item: item["company_name"])
    return results, warnings
