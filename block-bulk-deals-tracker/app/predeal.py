"""What the stock was doing in the month before each deal.

The snapshot in market.py describes a company as it stands today. These figures
describe it as it stood when a particular block printed: how it had traded, how
much ordinary liquidity the block landed in, and how far off the last close the
block was struck. All of it is read from NSE's daily archive.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from .config import (
    CLOSED_PERIOD_TTL_SECONDS,
    CRORE,
    MAX_SESSION_GAP_DAYS,
    OPEN_PERIOD_TTL_SECONDS,
    PRE_DEAL_LOOKBACK_DAYS,
    PRE_DEAL_WINDOW_DAYS,
    PRE_DEAL_WORKERS,
)
from .market import NSE_SERIES, corporate_actions, fetch_series, parse_session

# Written onto every row, so a company NSE has no history for reads as blank
# rather than as a row that quietly lost its last few columns.
FIELDS = (
    "prev_close",
    "prev_close_day",
    "discount_pct",
    "pre_return_1m_pct",
    "pre_adtv_1m_cr",
    "pre_vwap_1m",
    "pre_delivery_1m_pct",
    "pre_sessions_1m",
)


def _quarter_of(day: date) -> tuple:
    start = date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
    month = start.month + 3
    end = date(start.year + month // 13, month % 12 or 12, 1) - timedelta(days=1)
    return start, end


def _chunks_needed(days) -> list:
    """The quarters a deal and its lookback fall into.

    Quarters rather than years because NSE caps a history request at 70 sessions
    and returns the most recent ones without saying so: a year of Delhivery comes
    back as its last three months, which would leave every earlier deal blank.
    """
    wanted = {_quarter_of(day) for day in days}
    wanted |= {_quarter_of(day - timedelta(days=PRE_DEAL_LOOKBACK_DAYS)) for day in days}
    return sorted(wanted)


def _years_of(days) -> set:
    """Corporate actions come whole-market, and that endpoint has no such cap."""
    years = set()
    for day in days:
        years.add(day.year)
        years.add((day - timedelta(days=PRE_DEAL_LOOKBACK_DAYS)).year)
    return years


def _ttl_for(chunk_end: date) -> int:
    return CLOSED_PERIOD_TTL_SECONDS if chunk_end < date.today() else OPEN_PERIOD_TTL_SECONDS


def _behind(by_day, days) -> bool:
    """Has every deal got trading close behind it to be measured against?"""
    gap = timedelta(days=MAX_SESSION_GAP_DAYS)
    return all(
        any(day - gap <= traded < day for traded in by_day) for day in days
    )


def _history(symbol: str, chunks, days) -> list:
    """Raw daily sessions across the quarters asked for, oldest first.

    Left unadjusted on purpose. Each deal restates its own window into the share
    count that applied on its trade date, because these figures sit beside a
    price that was actually printed that day, not beside today's price.

    Series are merged rather than taking the first one that answers. NSE moves a
    security between segments and the series it left stops dead: Sterlite
    Technologies leaves the EQ archive in May 2026 and continues elsewhere, so
    trusting EQ alone would price a June block against a May close.
    """
    today = date.today()
    chunks = [(start, min(end, today)) for start, end in chunks if start <= today]

    by_day = {}
    for series in NSE_SERIES:
        for start, end in chunks:
            for record in fetch_series(symbol, series, start, end, _ttl_for(end)):
                try:
                    session = parse_session(record)
                except (KeyError, ValueError):
                    continue
                if session.close > 0 and session.quantity > 0:
                    by_day.setdefault(session.day, session)
        # Ordinary equity answers for almost everything, so this normally stops
        # after one pass and the other segments are never asked for.
        if by_day and _behind(by_day, days):
            break

    return sorted(by_day.values(), key=lambda session: session.day)


def _actions(symbol: str, years) -> list:
    today = date.today()
    found = []
    for year in sorted(years):
        start, end = date(year, 1, 1), min(date(year, 12, 31), today)
        if end < start:
            continue
        found.extend(corporate_actions(start, end).get(symbol.upper(), []))
    return found


def _restatement(actions, deal_day: date):
    """Multiplier putting an older session into the share count of the deal date.

    A five-for-one split inside the window leaves the earlier prices five times
    too high and the earlier volumes a fifth too small, which would otherwise
    read as a crash on no volume in the week before the block.
    """

    def factor(day: date) -> float:
        multiplier = 1.0
        for ex_date, value in actions:
            if day < ex_date <= deal_day:
                multiplier *= value
        return multiplier

    return factor


def measure(sessions, actions, deal_day: date) -> dict:
    """The month of trading up to, but not including, the deal date."""
    prior = [session for session in sessions if session.day < deal_day]
    if not prior:
        return {}

    # The deal is judged against the last close before it, so a deal on a Monday
    # is measured from the Friday rather than from a day the market was shut.
    anchor = prior[-1]
    # A close from weeks earlier says nothing about where the block was struck,
    # and a discount measured off it would be worse than no discount at all.
    if (deal_day - anchor.day).days > MAX_SESSION_GAP_DAYS:
        return {}
    factor = _restatement(actions, deal_day)
    price = lambda session: session.close * factor(session.day)  # noqa: E731

    cutoff = anchor.day - timedelta(days=PRE_DEAL_WINDOW_DAYS)
    inside = [session for session in prior if session.day > cutoff]
    earlier = [session for session in prior if session.day <= cutoff]
    if not inside:
        return {}

    result = {
        "prev_close": round(price(anchor), 2),
        "prev_close_day": anchor.day.isoformat(),
        "pre_sessions_1m": len(inside),
    }

    reference = earlier[-1] if earlier else None
    if reference is not None and price(reference) > 0:
        result["pre_return_1m_pct"] = round((price(anchor) / price(reference) - 1) * 100, 2)

    traded_value = sum(session.value for session in inside)
    result["pre_adtv_1m_cr"] = round(traded_value / len(inside) / CRORE, 2)

    traded_qty = sum(session.quantity / factor(session.day) for session in inside)
    if traded_qty > 0:
        result["pre_vwap_1m"] = round(traded_value / traded_qty, 2)

    # Delivery is a ratio over the sessions that report it, so a series NSE
    # publishes none for stays blank rather than reading as nothing delivered.
    disclosed = [session for session in inside if session.delivery_qty is not None]
    disclosed_qty = sum(session.quantity for session in disclosed)
    if disclosed_qty > 0:
        delivered = sum(session.delivery_qty for session in disclosed)
        result["pre_delivery_1m_pct"] = round(delivered / disclosed_qty * 100, 2)

    return result


def deal_price(row) -> float:
    """The deal's own quantity-weighted price.

    Deal size and quantity are both taken from the larger disclosed side, so
    their ratio is that side's weighted average. Averaging the printed prices
    instead would weigh a small odd lot the same as the bulk of the trade, and
    those prices are already per-client averages, so it would average averages.
    """
    quantity = row.get("quantity") or 0
    value = (row.get("deal_size_cr") or 0) * CRORE
    return value / quantity if quantity > 0 and value > 0 else 0.0


def discount(row, prev_close):
    """How far below the last close the deal was struck, as a percentage.

    Positive is a discount, which is how blocks usually print. A block placed
    above the last close reads negative.
    """
    price = deal_price(row)
    if price <= 0 or not prev_close or prev_close <= 0:
        return None
    return round((prev_close - price) / prev_close * 100, 2)


def _fill(symbol: str, rows) -> str:
    name = rows[0]["company_name"]
    days = sorted({date.fromisoformat(row["trade_date_iso"]) for row in rows})
    try:
        sessions = _history(symbol, _chunks_needed(days), days)
        actions = _actions(symbol, _years_of(days))
    except Exception as exc:
        return f"{name}: figures for the month before its deals are unavailable ({exc})."

    if not sessions:
        return f"NSE published no price history for {symbol}, so {name} has no pre-deal figures."

    # Several deals in one company often share a date across exchanges, and the
    # window is the same for all of them.
    measured = {}
    missing = 0
    for row in rows:
        day = date.fromisoformat(row["trade_date_iso"])
        if day not in measured:
            measured[day] = measure(sessions, actions, day)
        found = measured[day]
        if not found:
            missing += 1
            continue
        row.update(found)
        row["discount_pct"] = discount(row, found.get("prev_close"))

    if missing:
        return (
            f"{name}: NSE shows no trading in the days before {missing} of its "
            f"{'deal' if missing == 1 else 'deals'}, so those rows are blank."
        )
    return ""


def attach(rows, targets) -> list:
    """Add the pre-deal window to every row. Returns warnings."""
    for row in rows:
        for field in FIELDS:
            row.setdefault(field, None)

    symbols = {target.key: target.nse_symbol for target in targets}
    grouped = defaultdict(list)
    unlisted = {}
    for row in rows:
        symbol = symbols.get(row.get("market_key"), "")
        if symbol:
            grouped[symbol].append(row)
        else:
            # Blank columns with no explanation read as a fault in the tracker.
            unlisted.setdefault(row["company_name"], 0)
            unlisted[row["company_name"]] += 1

    notes = []
    if unlisted:
        names = ", ".join(sorted(unlisted))
        notes.append(
            f"{names} {'is' if len(unlisted) == 1 else 'are'} not on NSE, and these "
            "figures come from NSE's daily archive, so those rows are blank."
        )

    if grouped:
        jobs = list(grouped.items())
        workers = max(1, min(PRE_DEAL_WORKERS, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            notes.extend(note for note in pool.map(lambda job: _fill(*job), jobs) if note)
    return notes
