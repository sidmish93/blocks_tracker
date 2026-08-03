"""Latest quarterly results for each company, from NSE Integrated Filings.

Takeaways are short points a reader can scan: growth, margins, and whether
profitability improved — not a dump of absolute rupee figures, which mean little
without context. Numbers come from the XBRL filing itself (and, when available,
the same quarter a year earlier).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import quote

from nse_xbrl import FilingResult

from .config import CRORE, RESULTS_TTL_SECONDS, RESULTS_WORKERS
from .http_client import nse_get_text, read_cache, write_cache

INTEGRATED_URL = (
    "https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol={symbol}"
)
ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/api/corporate-announcements?index=equities"
    "&symbol={symbol}&from_date={start}&to_date={end}"
)
LEGACY_RESULTS_URL = (
    "https://www.nseindia.com/api/corporates-financial-results"
    "?index=equities&symbol={symbol}&period=Quarterly"
)

_FINANCIAL = re.compile(r"integrated\s+filing[\s-]*financials", re.IGNORECASE)
_BAD_PDF = re.compile(r"/null/?$", re.IGNORECASE)
_MONTHS = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def _parse_day(text) -> date | None:
    if not text:
        return None
    text = str(text).strip()
    head = text.split()[0].title() if text else ""
    for candidate in (head, text[:10], text[:19]):
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y %H:%M:%S"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _quarter_label(ended: date) -> str:
    """Indian financial-year quarter ending on this date, plus the calendar month."""
    if ended.month in (4, 5, 6):
        q, fy = 1, ended.year + 1
    elif ended.month in (7, 8, 9):
        q, fy = 2, ended.year + 1
    elif ended.month in (10, 11, 12):
        q, fy = 3, ended.year + 1
    else:
        q, fy = 4, ended.year
    return f"Q{q} FY{fy % 100:02d} ({_MONTHS[ended.month]} {ended.year})"


def _cr(value) -> float | None:
    if value is None:
        return None
    return round(value / CRORE, 2)


def _pct(part, whole) -> float | None:
    if part is None or not whole:
        return None
    return round(part / whole * 100, 1)


def _yoy(current, prior) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return round((current / prior - 1) * 100, 1)


def _shown(points: float) -> str:
    return f"{abs(points):.0f}" if abs(points) >= 10 else f"{abs(points):.1f}"


def _money(value) -> str:
    """Rupee amount in crores, Indian grouping for the big ones."""
    cr = _cr(value)
    if cr is None:
        return ""
    whole, _, fraction = f"{abs(cr):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        whole = ",".join(pairs + [tail])
    text = f"{whole}.{fraction}"
    return f"-Rs {text} cr" if cr < 0 else f"Rs {text} cr"


def _move_words(points: float, versus: str = "YoY") -> str:
    if abs(points) < 0.5:
        return f"flat {versus}"
    if points >= 100:
        return f"more than doubled {versus}"
    if points <= -50:
        return f"more than halved {versus}"
    direction = "up" if points > 0 else "down"
    return f"{direction} {_shown(points)}% {versus}"


def _move(points: float | None, versus: str = "YoY") -> str:
    """Compact growth clause to hang after an absolute figure."""
    return f", {_move_words(points, versus)}" if points is not None else ""


def _margin_shift(current: float | None, prior: float | None, versus: str = "YoY") -> str:
    if current is None or prior is None:
        return ""
    delta = round(current - prior, 1)
    if abs(delta) < 0.3:
        return f", margin steady {versus}"
    direction = "up" if delta > 0 else "down"
    return f", margin {direction} {abs(delta):.1f} pt {versus}"


def _ebitda_of(filing) -> float | None:
    """EBITDA as filed, or rebuilt from EBIT + depreciation when the field is blank."""
    if filing is None:
        return None
    if filing.q_ebitda is not None:
        return filing.q_ebitda
    if filing.q_ebit is not None and filing.q_depreciation is not None:
        return filing.q_ebit + filing.q_depreciation
    return None


def takeaways(current: FilingResult, prior: FilingResult | None = None, versus: str = "YoY") -> list:
    """Short points: absolute figure first, then how it moved.

    `versus` is YoY when the same quarter a year earlier is on NSE, otherwise QoQ
    against the previous quarter — some names (Adani Green is one) simply have no
    year-ago filing in either NSE feed yet.
    """
    revenue = current.q_revenue
    pat = current.q_pat
    pbt = current.q_pbt
    ebitda = _ebitda_of(current)
    eps = current.q_basic_eps if current.q_basic_eps is not None else current.q_diluted_eps
    other_income = current.q_other_income
    finance = current.q_finance_costs
    exceptional = current.q_exceptional_items

    prior_revenue = prior.q_revenue if prior else None
    prior_pat = prior.q_pat if prior else None
    prior_pbt = prior.q_pbt if prior else None
    prior_ebitda = _ebitda_of(prior)
    prior_eps = None
    if prior:
        prior_eps = prior.q_basic_eps if prior.q_basic_eps is not None else prior.q_diluted_eps

    chg_rev = _yoy(revenue, prior_revenue)
    chg_ebitda = _yoy(ebitda, prior_ebitda)
    chg_pat = _yoy(pat, prior_pat)
    chg_pbt = _yoy(pbt, prior_pbt)
    chg_eps = _yoy(eps, prior_eps)

    ebitda_margin = _pct(ebitda, revenue)
    prior_ebitda_margin = _pct(prior_ebitda, prior_revenue)
    pat_margin = _pct(pat, revenue)
    prior_pat_margin = _pct(prior_pat, prior_revenue)

    ago = "a year ago" if versus == "YoY" else "last quarter"
    points = []

    if revenue is not None:
        points.append(f"Revenue {_money(revenue)}{_move(chg_rev, versus)}")

    if ebitda is not None:
        bit = f"EBITDA {_money(ebitda)}"
        if ebitda_margin is not None:
            bit += f" ({ebitda_margin:.1f}% margin)"
        bit += _move(chg_ebitda, versus)
        bit += _margin_shift(ebitda_margin, prior_ebitda_margin, versus)
        points.append(bit)

    # Profit / loss, with the absolute figure always in view.
    if prior_pat is not None and pat is not None and prior_pat < 0 <= pat:
        points.append(f"PAT {_money(pat)} — turned profitable vs a loss {ago}")
    elif prior_pat is not None and pat is not None and prior_pat >= 0 > pat:
        points.append(f"PAT {_money(pat)} — slipped into a loss vs a profit {ago}")
    elif pat is not None and pat < 0:
        if chg_pat is None:
            points.append(f"Loss {_money(abs(pat))} this quarter")
        elif chg_pat > 0.5:
            points.append(f"Loss {_money(abs(pat))}, widened {_shown(chg_pat)}% {versus}")
        elif chg_pat < -0.5:
            points.append(f"Loss {_money(abs(pat))}, narrowed {_shown(chg_pat)}% {versus}")
        else:
            points.append(f"Loss {_money(abs(pat))}, roughly flat {versus}")
    elif pat is not None:
        bit = f"PAT {_money(pat)}"
        if pat_margin is not None:
            bit += f" ({pat_margin:.1f}% of revenue)"
        bit += _move(chg_pat, versus)
        bit += _margin_shift(pat_margin, prior_pat_margin, versus)
        points.append(bit)
    elif pbt is not None:
        points.append(f"PBT {_money(pbt)}{_move(chg_pbt, versus)}")

    if eps is not None:
        points.append(f"EPS Rs {eps:,.2f}{_move(chg_eps, versus)}")

    # Operating leverage: did profits move with sales, or against them?
    if (
        chg_rev is not None
        and chg_pat is not None
        and pat is not None
        and pat >= 0
        and abs(chg_rev) >= 5
        and abs(chg_pat - chg_rev) >= 10
    ):
        if chg_pat > chg_rev + 10:
            points.append(
                f"Profits grew faster than sales (PAT {_move_words(chg_pat, versus)} vs revenue "
                f"{_move_words(chg_rev, versus)})"
            )
        elif chg_rev > 0 >= chg_pat:
            points.append(
                f"Revenue rose but profits did not (revenue {_move_words(chg_rev, versus)}, PAT "
                f"{_move_words(chg_pat, versus)})"
            )
        elif chg_rev > 0 and chg_pat < chg_rev - 10:
            points.append(
                f"Profits lagged sales (revenue {_move_words(chg_rev, versus)}, PAT "
                f"{_move_words(chg_pat, versus)})"
            )

    # One-offs and financing that change how the quarter should be read.
    if exceptional is not None:
        share = _pct(abs(exceptional), abs(pat) if pat else None)
        # Skip pocket-change one-offs; keep anything large in rupees or versus PAT.
        if abs(exceptional) >= 5 * CRORE or (share is not None and share >= 15):
            kind = "gain" if exceptional > 0 else "loss"
            bit = f"Exceptional {kind} {_money(abs(exceptional))}"
            if share is not None and share >= 10:
                bit += f" ({share:.0f}% of |PAT|)"
            points.append(bit)

    if other_income is not None and revenue and other_income / revenue >= 0.05:
        points.append(
            f"Other income {_money(other_income)} ({_pct(other_income, revenue):.1f}% of revenue)"
        )

    if finance is not None and ebitda and ebitda > 0 and finance / ebitda >= 0.25:
        points.append(
            f"Finance cost {_money(finance)} ({_pct(finance, ebitda):.0f}% of EBITDA)"
        )

    # Year-to-date when it adds something beyond the quarter itself. Growth on
    # FYTD is only meaningful against the year-ago filing, not against last quarter.
    if current.ytd_revenue is not None and current.ytd_revenue != revenue:
        ytd_rev_move = (
            _yoy(current.ytd_revenue, prior.ytd_revenue if prior else None)
            if versus == "YoY"
            else None
        )
        bit = f"FYTD revenue {_money(current.ytd_revenue)}{_move(ytd_rev_move, 'YoY')}"
        if current.ytd_pat is not None and current.ytd_pat != pat:
            ytd_pat_move = (
                _yoy(current.ytd_pat, prior.ytd_pat if prior else None)
                if versus == "YoY"
                else None
            )
            bit += f"; FYTD PAT {_money(current.ytd_pat)}{_move(ytd_pat_move, 'YoY')}"
        points.append(bit)

    debt_equity = getattr(current, "debt_equity_ratio", None)
    if debt_equity is not None and debt_equity > 0:
        points.append(f"Debt-equity {debt_equity:.2f}x")

    return points[:7]


def _list_filings(symbol: str) -> list:
    name = f"nse_integrated_{symbol.upper()}.json"
    cached = read_cache(name, RESULTS_TTL_SECONDS)
    if cached is not None:
        return cached
    payload = json.loads(nse_get_text(INTEGRATED_URL.format(symbol=quote(symbol.upper()))))
    rows = payload.get("data") if isinstance(payload, dict) else payload
    rows = rows if isinstance(rows, list) else []
    write_cache(name, rows)
    return rows


def _financial_rows(rows) -> list:
    found = []
    for row in rows:
        if not _FINANCIAL.search(row.get("type") or ""):
            continue
        ended = _parse_day(row.get("qe_Date"))
        if not ended or not row.get("xbrl"):
            continue
        found.append({**row, "_ended": ended})
    found.sort(key=lambda row: (row["_ended"], row.get("broadcast_Date") or ""), reverse=True)
    return found


def _prefer_consolidated(rows) -> dict | None:
    if not rows:
        return None
    latest_end = rows[0]["_ended"]
    same = [row for row in rows if row["_ended"] == latest_end]
    for row in same:
        if (row.get("consolidated") or "").lower().startswith("consol"):
            return row
    return same[0]


def _match_ended(rows, ended: date) -> dict | None:
    """The consolidated (else first) filing that lands on this quarter-end."""
    target = date(ended.year, ended.month, min(ended.day, 28))
    candidates = [
        row
        for row in rows
        if row["_ended"].year == ended.year and row["_ended"].month == ended.month
    ]
    if not candidates:
        candidates = [row for row in rows if abs((row["_ended"] - target).days) <= 7]
    return _prefer_consolidated(candidates) if candidates else None


def _prior_year(rows, ended: date) -> dict | None:
    return _match_ended(rows, date(ended.year - 1, ended.month, min(ended.day, 28)))


def _prior_quarter(rows, ended: date) -> dict | None:
    """The most recent earlier quarter on the same feed, for a QoQ read."""
    earlier = [row for row in rows if row["_ended"] < ended]
    if not earlier:
        return None
    latest_end = max(row["_ended"] for row in earlier)
    return _prefer_consolidated([row for row in earlier if row["_ended"] == latest_end])


def _parse_filing(symbol: str, url: str, consolidated: bool) -> FilingResult:
    key = re.sub(r"[^A-Za-z0-9]+", "_", url)[-80:]
    name = f"nse_xbrl_{key}.json"
    cached = read_cache(name, RESULTS_TTL_SECONDS)
    if cached is not None and "xml" in cached:
        xml = cached["xml"]
    else:
        xml = nse_get_text(url)
        write_cache(name, {"xml": xml, "url": url})
    return FilingResult.from_xbrl(xml, symbol=symbol, is_consolidated=consolidated)


def _announcement_pdf(symbol: str, ended: date, broadcast: date | None) -> str:
    """Board-meeting outcome PDF that carries the financial results for this quarter."""
    end = broadcast or ended + timedelta(days=60)
    start = ended - timedelta(days=5)
    name = f"nse_ann_{symbol.upper()}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    cached = read_cache(name, RESULTS_TTL_SECONDS)
    if cached is None:
        url = ANNOUNCEMENTS_URL.format(
            symbol=quote(symbol.upper()),
            start=start.strftime("%d-%m-%Y"),
            end=end.strftime("%d-%m-%Y"),
        )
        try:
            cached = json.loads(nse_get_text(url))
        except Exception:
            cached = []
        if not isinstance(cached, list):
            cached = []
        write_cache(name, cached)

    scored = []
    for row in cached:
        text = f"{row.get('desc') or ''} {row.get('attchmntText') or ''}".lower()
        link = row.get("attchmntFile") or ""
        if not link or _BAD_PDF.search(link):
            continue
        if "financial result" not in text and "financial results" not in text:
            continue
        day = _parse_day(row.get("an_dt") or row.get("dt"))
        score = 0
        if "outcome of board" in text:
            score += 5
        if ended.strftime("%b").lower() in text or str(ended.year) in text:
            score += 2
        if day and broadcast and abs((day - broadcast).days) <= 2:
            score += 3
        scored.append((score, day or date.min, link))
    if not scored:
        return ""
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def _legacy_rows(symbol: str) -> list:
    """Older quarterly filings still live on the financial-results feed."""
    name = f"nse_finresults_{symbol.upper()}.json"
    cached = read_cache(name, RESULTS_TTL_SECONDS)
    if cached is None:
        try:
            cached = json.loads(nse_get_text(LEGACY_RESULTS_URL.format(symbol=quote(symbol.upper()))))
        except Exception:
            cached = []
        if not isinstance(cached, list):
            cached = []
        write_cache(name, cached)

    rows = []
    for row in cached:
        ended = _parse_day(row.get("toDate"))
        if not ended or not row.get("xbrl"):
            continue
        if (row.get("period") or "").lower() != "quarterly":
            continue
        # A bare "-" on the older feed is not a downloadable filing.
        if str(row.get("xbrl")).rstrip("/").endswith("-"):
            continue
        rows.append({**row, "_ended": ended})
    rows.sort(key=lambda row: row["_ended"], reverse=True)
    return rows


def _legacy_latest(symbol: str) -> dict | None:
    rows = _legacy_rows(symbol)
    if not rows:
        return None
    return _prefer_consolidated([row for row in rows if row["_ended"] == rows[0]["_ended"]])


def _as_filing_row(row: dict) -> dict:
    """Normalise an integrated or legacy row into the shape _for_target expects."""
    return {
        **row,
        "xbrl": row["xbrl"],
        "ixbrl": row.get("ixbrl") or row.get("resultDetailedDataLink"),
        "consolidated": row.get("consolidated"),
        "audited": row.get("audited"),
        "broadcast_Date": row.get("broadcast_Date")
        or row.get("broadCastDate")
        or row.get("filingDate"),
        "_ended": row["_ended"],
    }


def _load_prior(symbol: str, filings, ended: date) -> tuple:
    """Year-ago filing if NSE has it; otherwise the previous quarter for a QoQ read.

    Returns (row, FilingResult|None, 'YoY'|'QoQ'|'', note).
    """
    prior_row = _prior_year(filings, ended)
    versus = "YoY"
    note = ""

    if prior_row is None:
        # Integrated Filing often only keeps recent quarters; the year-ago print
        # may still sit on the older financial-results feed.
        prior_row = _prior_year(_legacy_rows(symbol), ended)

    if prior_row is None:
        prior_row = _prior_quarter(filings, ended)
        if prior_row is None:
            prior_row = _prior_quarter(_legacy_rows(symbol), ended)
        if prior_row is not None:
            versus = "QoQ"
            note = (
                f"{symbol}: NSE has no filing for the same quarter a year earlier, "
                f"so takeaways compare with the previous quarter ({_MONTHS[prior_row['_ended'].month]} "
                f"{prior_row['_ended'].year})."
            )

    if prior_row is None:
        return None, None, "", ""

    try:
        prior = _parse_filing(
            symbol,
            prior_row["xbrl"],
            (prior_row.get("consolidated") or "").lower().startswith("consol"),
        )
    except Exception:
        return prior_row, None, versus, note
    return prior_row, prior, versus, note


def _for_target(target) -> tuple:
    symbol = (target.nse_symbol or "").upper()
    name = target.name
    empty = {
        "key": target.key,
        "company_name": name,
        "ticker": target.ticker,
        "quarter": None,
        "period_end": None,
        "period_end_iso": None,
        "reported_on": None,
        "consolidated": None,
        "audited": None,
        "report_url": None,
        "report_label": None,
        "xbrl_url": None,
        "takeaways": [],
    }
    if not symbol:
        return empty, f"{name} is not on NSE, so no quarterly filing was pulled."

    try:
        filings = _financial_rows(_list_filings(symbol))
        latest = _prefer_consolidated(filings)
        if latest is None:
            legacy = _legacy_latest(symbol)
            if legacy is None:
                return empty, f"NSE has no quarterly financial filing for {name}."
            latest = _as_filing_row(legacy)
            filings = _legacy_rows(symbol)

        ended = latest["_ended"]
        broadcast = _parse_day(latest.get("broadcast_Date") or latest.get("creation_Date"))
        consolidated = (latest.get("consolidated") or "").lower().startswith("consol")
        current = _parse_filing(symbol, latest["xbrl"], consolidated)

        _, prior, versus, prior_note = _load_prior(symbol, filings, ended)

        pdf = _announcement_pdf(symbol, ended, broadcast)
        ixbrl = latest.get("ixbrl") or ""
        if pdf:
            report_url, report_label = pdf, "Results PDF"
        elif ixbrl and not _BAD_PDF.search(ixbrl):
            report_url, report_label = ixbrl, "NSE iXBRL report"
        else:
            report_url, report_label = latest.get("xbrl") or "", "NSE XBRL filing"

        points = takeaways(current, prior, versus=versus or "YoY")
        entry = {
            "key": target.key,
            "company_name": name,
            "ticker": target.ticker,
            "quarter": _quarter_label(ended),
            "period_end": f"{_MONTHS[ended.month]} {ended.year}",
            "period_end_iso": ended.isoformat(),
            "reported_on": broadcast.isoformat() if broadcast else None,
            "consolidated": "Consolidated" if consolidated else (latest.get("consolidated") or ""),
            "audited": latest.get("audited") or "",
            "report_url": report_url or None,
            "report_label": report_label if report_url else None,
            "xbrl_url": latest.get("xbrl"),
            "compare": versus or None,
            "takeaways": points,
        }
        if not points:
            return entry, f"{name}: the latest filing could not be turned into takeaways."
        # Prefer the company name in the note the reader sees.
        if prior_note:
            prior_note = prior_note.replace(f"{symbol}:", f"{name}:", 1)
        return entry, prior_note
    except Exception as exc:
        return empty, f"{name}: quarterly results unavailable ({exc})."


def collect(targets) -> tuple:
    """Latest quarter takeaways for every company. Returns (entries, notes)."""
    wanted = [target for target in targets if target.nse_symbol or target.bse_code]
    if not wanted:
        return [], []

    workers = max(1, min(RESULTS_WORKERS, len(wanted)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = list(pool.map(_for_target, wanted))

    entries, notes = [], []
    for entry, note in found:
        if entry.get("takeaways") or entry.get("report_url") or entry.get("quarter"):
            entries.append(entry)
        if note:
            notes.append(note)
    entries.sort(key=lambda item: item["company_name"])
    return entries, notes
