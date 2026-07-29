"""Per-company market data, computed from NSE's daily price/volume/delivery
archive and BSE's live market capitalisation.

Everything here is a trailing snapshot taken from the latest session a security
traded in, so it does not move with the deal period the user picked.
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
    METRIC_HISTORY_DAYS,
    METRIC_WINDOWS,
    METRIC_WORKERS,
    OPEN_PERIOD_TTL_SECONDS,
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
BSE_QUOTE_URL = "https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?flag=&scripcode={code}"

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
    # Multiplier that restates this day's price in terms of today's share count.
    factor: float = 1.0

    @property
    def adjusted_close(self) -> float:
        return self.close * self.factor

    @property
    def adjusted_quantity(self) -> float:
        return self.quantity / self.factor if self.factor else self.quantity


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


def _parse_session(record) -> Session:
    delivered = record.get("COP_DELIV_QTY")
    return Session(
        day=datetime.strptime(record["mTIMESTAMP"], "%d-%b-%Y").date(),
        close=to_number(record.get("CH_CLOSING_PRICE")),
        quantity=to_number(record.get("CH_TOT_TRADED_QTY")),
        value=to_number(record.get("CH_TOT_TRADED_VAL")),
        delivery_qty=None if delivered in (None, "", "-") else to_number(delivered),
    )


def _fetch_series(symbol: str, series: str, start: date, end: date) -> list:
    slug = _UNSAFE.sub("-", symbol).strip("-").upper() or "unknown"
    name = f"nse_hist_{slug}_{series}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    cached = read_cache(name, OPEN_PERIOD_TTL_SECONDS)
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

    records = []
    for series in NSE_SERIES:
        records = _fetch_series(symbol, series, start, end)
        if records:
            break
    if not records:
        return []

    sessions = []
    for record in records:
        try:
            session = _parse_session(record)
        except (KeyError, ValueError):
            continue
        if session.close > 0 and session.quantity > 0:
            sessions.append(session)
    sessions.sort(key=lambda item: item.day)

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
    }
    notes = []

    try:
        sessions = fetch_history(target.nse_symbol, start, end)
        if sessions:
            result.update(_measure(sessions))
        elif target.nse_symbol:
            notes.append(f"NSE published no recent trading history for {target.nse_symbol}.")
        else:
            notes.append(f"{target.name} is not listed on NSE, so traded stats are unavailable.")
    except Exception as exc:
        notes.append(f"{target.name}: NSE history unavailable ({exc}).")

    try:
        result["market_cap_cr"] = _market_cap_cr(target.bse_code)
        if result["market_cap_cr"] is None and not target.bse_code:
            notes.append(f"{target.name} is not listed on BSE, so market cap is unavailable.")
    except Exception as exc:
        notes.append(f"{target.name}: BSE market cap unavailable ({exc}).")

    result["notes"] = notes
    return result


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

    warnings = []
    for result in results:
        warnings.extend(result.pop("notes", []))
    results.sort(key=lambda item: item["company_name"])
    return results, warnings
