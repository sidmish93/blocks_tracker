import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import companies, market, news, predeal, results
from .aggregate import build_rows
from .config import BSE_DATA_START, DEAL_WORKERS, NEWS_WINDOW_DAYS
from .excel import build_workbook
from .pdf import build_pdf
from .sources import fetch_all

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Block & Bulk Deals Tracker", docs_url=None, redoc_url=None)


class TrackerRequest(BaseModel):
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    min_deal_size_cr: float = Field(default=200, ge=0)
    # Names the user typed, several of them separated by semicolons.
    company: Optional[str] = None
    # Names already pinned to a listing, so they need no resolving again.
    company_keys: Optional[List[str]] = None
    since_listing: bool = False
    # Each section is its own set of NSE/BSE trips — leave off what you do not need.
    include_deals: bool = True
    include_market: bool = True
    include_news: bool = True
    include_quarters: bool = True
    # Under Market data: which windows to show for ADTV / VWAP / Delivery.
    # Return % always keeps 1D, 1W and 1M regardless of these flags.
    market_volume_1d: bool = True
    market_volume_1w: bool = True
    market_volume_1m: bool = True

    @model_validator(mode="after")
    def check_period(self):
        if not any(
            (self.include_deals, self.include_market, self.include_news, self.include_quarters)
        ):
            raise ValueError("Select at least one section to generate.")
        if not self.include_deals and not (self.company or self.company_keys):
            raise ValueError(
                "Name at least one company when Block & bulk deals is unchecked."
            )
        if self.since_listing:
            if not (self.company or self.company_keys):
                raise ValueError("Name at least one company to search since listing.")
        else:
            if not self.from_date or not self.to_date:
                raise ValueError("Choose a 'From' and a 'To' date.")
            if self.to_date < self.from_date:
                raise ValueError("'To' date must not be earlier than 'From' date.")
        return self

    @property
    def sections(self) -> dict:
        return {
            "deals": self.include_deals,
            "market": self.include_market,
            "news": self.include_news,
            "quarters": self.include_quarters,
        }

    @property
    def market_windows(self) -> dict:
        return {
            "1d": self.market_volume_1d,
            "1w": self.market_volume_1w,
            "1m": self.market_volume_1m,
        }


@dataclass
class Plan:
    """A company to search, and the window to search it over. Since-listing gives
    each company a different start, so the window belongs to the company."""

    company: object
    start: date
    end: date


def split_terms(text: str) -> list:
    """Company names as typed: several at a time, separated by semicolons."""
    seen, terms = set(), []
    for part in (text or "").split(";"):
        term = part.strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def _resolve_companies(request: TrackerRequest) -> list:
    found, missing, ambiguous = [], [], []

    for key in request.company_keys or []:
        company = companies.get(key)
        if company is None:
            raise HTTPException(status_code=404, detail="That company is no longer in the list.")
        found.append(company)

    for term in split_terms(request.company):
        company, alternatives = companies.resolve(term)
        if company is not None:
            found.append(company)
        elif alternatives:
            ambiguous.append((term, alternatives))
        else:
            missing.append(term)

    # A name that matches nothing is a typo the user has to fix, so all of them are
    # reported at once rather than one reload at a time.
    if missing:
        names = ", ".join(f'"{term}"' for term in missing)
        raise HTTPException(status_code=404, detail=f"No listed company found for {names}.")

    # Ambiguity needs a choice, and a choice can only be made one name at a time.
    if ambiguous:
        term, alternatives = ambiguous[0]
        message = f'"{term}" matches more than one company. Pick one.'
        remaining = len(ambiguous) - 1
        if remaining:
            message += f" Then {remaining} more name{'s' if remaining > 1 else ''} to settle."
        raise HTTPException(
            status_code=409,
            detail={
                "message": message,
                "term": term,
                "candidates": [item.as_dict() for item in alternatives],
            },
        )

    unique = {}
    for company in found:
        unique.setdefault(company.key, company)
    # Alphabetical rather than as typed, so the company list, the market data and
    # the news all read in the same order however the request was put together.
    return sorted(unique.values(), key=lambda company: company.name.lower())


def _plans(request: TrackerRequest, chosen) -> list:
    if not chosen:
        return [Plan(None, request.from_date, request.to_date)]
    if not request.since_listing:
        return [Plan(company, request.from_date, request.to_date) for company in chosen]
    today = date.today()
    return [Plan(company, company.listing_day() or BSE_DATA_START, today) for company in chosen]


def _market_key(isin: str, nse_symbol: str, bse_code: str, fallback: str) -> str:
    """One identity per company, however the deals happened to be reported."""
    if isin:
        return isin
    if nse_symbol:
        return f"NSE:{nse_symbol}"
    if bse_code:
        return f"BSE:{bse_code}"
    return fallback


def _targets(rows, chosen):
    """One market-data target per company in the result. A deal reported by only
    one exchange still needs the other exchange's identifier, so each row is
    matched back to the listing masters. Named companies are included even when
    they had no qualifying deals, so their figures and news still come back."""
    targets = {}

    for company in chosen:
        key = _market_key(company.isin, company.nse_symbol, company.bse_code, company.key)
        targets[key] = market.Target(
            key=key,
            name=company.name,
            ticker=company.ticker,
            nse_symbol=company.nse_symbol,
            bse_code=company.bse_code,
        )

    for row in rows:
        listed = companies.get(row["isin"]) if row["isin"] else None
        if listed is None:
            listed = companies.find_by_ticker(row["nse_symbol"] or row["ticker"])
        nse_symbol = row["nse_symbol"] or (listed.nse_symbol if listed else "")
        bse_code = row["bse_code"] or (listed.bse_code if listed else "")
        isin = row["isin"] or (listed.isin if listed else "")

        key = _market_key(isin, nse_symbol, bse_code, row["security_key"])
        row["market_key"] = key
        if key not in targets:
            targets[key] = market.Target(
                key=key,
                name=row["company_name"],
                ticker=row["ticker"],
                nse_symbol=nse_symbol,
                bse_code=bse_code,
            )
    return list(targets.values())


@dataclass
class Result:
    rows: list
    errors: list
    stats: dict
    plans: list
    start: date
    end: date
    metrics: list
    # Kept apart so a document can print each explanation beside the figures it
    # is about, rather than piling them all up in one place.
    market_notes: list
    context_notes: list
    news_notes: list
    news: list
    quarters: list
    quarter_notes: list

    @property
    def chosen(self) -> list:
        return [plan.company for plan in self.plans if plan.company is not None]

    @property
    def notes(self) -> list:
        return self.market_notes + self.context_notes + self.news_notes + self.quarter_notes


def _fetch_deals(plans) -> tuple:
    if len(plans) == 1:
        plan = plans[0]
        return fetch_all(plan.start, plan.end, plan.company)

    legs, errors = [], []
    with ThreadPoolExecutor(max_workers=min(DEAL_WORKERS, len(plans))) as pool:
        for found, problems in pool.map(
            lambda plan: fetch_all(plan.start, plan.end, plan.company), plans
        ):
            legs.extend(found)
            errors.extend(problems)
    return legs, errors


def _build(request: TrackerRequest) -> Result:
    chosen = _resolve_companies(request)
    plans = _plans(request, chosen)

    errors = []
    if request.include_deals:
        legs, errors = _fetch_deals(plans)
        # Nothing at all plus a failure means the search never ran; a partial result
        # is reported as a warning instead, since one company may simply be quiet.
        if not legs and errors:
            raise HTTPException(status_code=502, detail=" | ".join(errors))
        rows, stats = build_rows(legs, request.min_deal_size_cr)
    else:
        rows, stats = [], {"self_trade_entities_excluded": 0}

    targets = _targets(rows, chosen)

    metrics, notes = [], []
    context_notes = []
    headlines, news_notes = [], []
    quarters, quarter_notes = [], []

    # Only kick off the trips the user asked for — each one is a full NSE/BSE round.
    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        if request.include_market and targets:
            jobs["market"] = pool.submit(market.collect, targets)
        if request.include_deals and rows:
            jobs["predeal"] = pool.submit(predeal.attach, rows, targets)
        if request.include_news and targets:
            jobs["news"] = pool.submit(news.collect, targets)
        if request.include_quarters and targets:
            jobs["quarters"] = pool.submit(results.collect, targets)

        if "market" in jobs:
            metrics, notes = jobs["market"].result()
        if "predeal" in jobs:
            context_notes = jobs["predeal"].result()
        if "news" in jobs:
            headlines, news_notes = jobs["news"].result()
        if "quarters" in jobs:
            quarters, quarter_notes = jobs["quarters"].result()

    return Result(
        rows=rows,
        errors=errors,
        stats=stats,
        plans=plans,
        start=min(plan.start for plan in plans),
        end=max(plan.end for plan in plans),
        metrics=metrics,
        market_notes=notes,
        context_notes=context_notes,
        news_notes=news_notes,
        news=headlines,
        quarters=quarters,
        quarter_notes=quarter_notes,
    )


@app.get("/api/companies")
def company_search(q: str = Query(default="", max_length=120)):
    if len(q.strip()) < 2:
        return {"companies": []}
    return {"companies": [company.as_dict() for company in companies.search(q, limit=8)]}


@app.post("/api/tracker")
def tracker(request: TrackerRequest):
    result = _build(request)
    return {
        "rows": result.rows,
        "market_data": result.metrics,
        "news": result.news,
        "news_window_days": NEWS_WINDOW_DAYS,
        "quarters": result.quarters,
        "sections": request.sections,
        "market_windows": request.market_windows,
        # Fetch failures, which make the tracker incomplete, are kept apart from
        # notes explaining why a particular company has no market data.
        "warnings": result.errors,
        "market_notes": result.market_notes,
        "quarter_notes": result.quarter_notes,
        # Each company carries its own window, because since-listing starts them
        # on different days.
        "companies": [
            {
                **plan.company.as_dict(),
                "from_date": plan.start.isoformat(),
                "to_date": plan.end.isoformat(),
            }
            for plan in result.plans
            if plan.company is not None
        ],
        "summary": {
            "deal_count": len(result.rows),
            "total_value_cr": round(sum(row["deal_size_cr"] for row in result.rows), 2),
            "self_trade_entities_excluded": result.stats["self_trade_entities_excluded"],
            "from_date": result.start.isoformat(),
            "to_date": result.end.isoformat(),
            "min_deal_size_cr": request.min_deal_size_cr,
            "since_listing": request.since_listing,
            "company_count": len(result.chosen),
        },
    }


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()


def _filename(chosen, start: date, end: date, minimum: float, suffix: str) -> str:
    if not chosen:
        scope = "block-bulk-deals"
    elif len(chosen) <= 3:
        names = "-".join(_slug(company.ticker or company.name) for company in chosen)
        scope = f"{names}_block-bulk-deals"
    else:
        scope = f"{len(chosen)}-companies_block-bulk-deals"
    return f"{scope}_{start:%Y%m%d}-{end:%Y%m%d}_min{int(minimum)}cr.{suffix}"


def _attachment(content: bytes, media_type: str, name: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/tracker.xlsx")
def tracker_excel(request: TrackerRequest):
    result = _build(request)
    return _attachment(
        build_workbook(
            result.rows,
            result.metrics,
            result.news,
            result.quarters,
            sections=request.sections,
            market_windows=request.market_windows,
        ),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _filename(result.chosen, result.start, result.end, request.min_deal_size_cr, "xlsx"),
    )


@app.post("/api/tracker.pdf")
def tracker_pdf(request: TrackerRequest):
    result = _build(request)
    meta = {
        "from_date": result.start.isoformat(),
        "to_date": result.end.isoformat(),
        "since_listing": request.since_listing,
        "min_deal_size_cr": request.min_deal_size_cr,
        "deal_count": len(result.rows),
        "total_value_cr": round(sum(row["deal_size_cr"] for row in result.rows), 2),
        "news_window_days": NEWS_WINDOW_DAYS,
        "companies": [company.as_dict() for company in result.chosen],
        "market_notes": result.market_notes,
        "deal_notes": result.context_notes,
        "news_notes": result.news_notes,
        "quarter_notes": result.quarter_notes,
        "sections": request.sections,
        "market_windows": request.market_windows,
    }
    return _attachment(
        build_pdf(result.rows, result.metrics, result.news, meta, result.quarters),
        "application/pdf",
        _filename(result.chosen, result.start, result.end, request.min_deal_size_cr, "pdf"),
    )


@app.get("/")
def index():
    response = FileResponse(STATIC_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
