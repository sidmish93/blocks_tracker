"""Recent company news, restricted to six Indian business publications.

Each source is searched separately so one prolific publisher cannot crowd out
the rest. A search engine matches body text as well as headlines, so a query for
one company routinely returns stories about another; results are therefore kept
only when the headline itself names the company, and stock-quote and topic
landing pages are discarded because they are not news.
"""

import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .config import NEWS_PER_COMPANY, NEWS_TTL_SECONDS, NEWS_WINDOW_DAYS, NEWS_WORKERS
from .http_client import plain_get_text, read_cache, write_cache

SOURCES = [
    ("CNBC TV18", "cnbctv18.com"),
    ("Moneycontrol", "moneycontrol.com"),
    ("NDTV Profit", "ndtvprofit.com"),
    ("Economic Times", "economictimes.indiatimes.com"),
    ("Business Standard", "business-standard.com"),
    ("Mint", "livemint.com"),
]

SEARCH_URL = "https://www.bing.com/news/search?q={query}&format=RSS&sortbydate=1"

# Corporate suffixes carry no meaning for a headline match. Only trailing ones are
# stripped: "Container Corporation of India" must not become "Container of India".
_TRAILING_SUFFIX = re.compile(
    r"\s+(limited|ltd|private|pvt|corporation|corp|company|co|plc|inc|holdings)$",
    re.IGNORECASE,
)
_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)
_SPACES = re.compile(r"\s+")
_UNSAFE = re.compile(r"[^A-Za-z0-9]+")

# Quote pages, topic hubs and screener rows live under these paths on the six sites.
_JUNK_PATH = re.compile(
    r"/(topic|topics|market-stats|stockreports|stocksupdate|company-facts"
    r"|company-fact-sheet|technical-analysis|price-chart)/"
    r"|[/-]share-price(/|$)"
    r"|-share-price-\d+",
    re.IGNORECASE,
)
# Landing pages whose whole title is the company plus a label.
_JUNK_TITLE = re.compile(
    r"^.{0,70}?\b(share|stock)\s+price\b.{0,30}$"
    r"|^.{0,45}\bq[1-4]\s+(results?|earnings)\s*$"
    r"|^.{0,45}\b(shareholding|share holding|dividend|balance sheet|profit\s*&\s*loss)\s*$",
    re.IGNORECASE,
)


@dataclass
class Article:
    key: str
    company_name: str
    source: str
    headline: str
    url: str
    published: datetime
    score: float

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "company_name": self.company_name,
            "source": self.source,
            "headline": self.headline,
            "url": self.url,
            "published": self.published.astimezone().isoformat(timespec="minutes"),
        }


def short_name(name: str) -> str:
    """The company name a headline would actually use."""
    text = _SPACES.sub(" ", (name or "").replace(".", " ")).strip()
    text = _LEADING_THE.sub("", text)
    while True:
        trimmed = _TRAILING_SUFFIX.sub("", text)
        if trimmed == text:
            return text.strip(" ,-&")
        text = trimmed


def aliases(name: str, ticker: str) -> list:
    """Names a headline might use for this company.

    The two-leading-word form matters for family groups: "Adani Energy" and
    "Adani Enterprises" are different companies, and matching on "Adani" alone
    would attribute every story to all of them.
    """
    found = []
    trimmed = short_name(name)
    if trimmed:
        found.append(trimmed)
        words = trimmed.split()
        if len(words) > 2:
            found.append(" ".join(words[:2]))
    ticker = (ticker or "").strip()
    if len(ticker) >= 3:
        found.append(ticker)

    unique = []
    for alias in found:
        if alias.lower() not in {item.lower() for item in unique}:
            unique.append(alias)
    return unique


def _pattern(alias: str):
    # Word boundaries that survive punctuation such as the ampersand in M&M.
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", re.IGNORECASE
    )


def headline_position(headline: str, alias_patterns) -> int:
    """Where the company is first named, or -1 when it is not named at all."""
    positions = [
        match.start()
        for match in (pattern.search(headline or "") for pattern in alias_patterns)
        if match
    ]
    return min(positions) if positions else -1


def is_article(url: str, headline: str) -> bool:
    path = urllib.parse.urlparse(url or "").path
    if not path or _JUNK_PATH.search(path):
        return False
    return not _JUNK_TITLE.match((headline or "").strip())


def relevance(headline: str, position: int, age_days: float, by_ticker: bool = False) -> float:
    """Rank a company's stories: named early, focused, and recent."""
    score = 100.0
    # A headline that opens with the company is about the company; one that
    # names it in passing at the end usually is not.
    score -= min(position, 80) * 0.4
    # Roundups listing a dozen tickers say little about any one of them.
    score -= min((headline or "").count(","), 8) * 4.0
    score += max(0.0, NEWS_WINDOW_DAYS - age_days) * 6.0
    # A ticker can name something other than the company: MCX is an exchange as
    # well as a listed firm, so "MCX gold slips" ranks below news about the firm.
    if by_ticker:
        score -= 12.0
    return round(score, 2)


def _direct_url(link: str) -> str:
    """The search engine wraps results in a click tracker; the real article URL
    is carried in its query string."""
    parsed = urllib.parse.urlparse(link or "")
    if "bing.com" not in parsed.netloc:
        return link
    target = urllib.parse.parse_qs(parsed.query).get("url", [""])[0]
    return target or link


def _search(query: str, domain: str) -> list:
    slug = _UNSAFE.sub("-", f"{query}-{domain}").strip("-").lower()[:110]
    name = f"news_{slug}.json"
    cached = read_cache(name, NEWS_TTL_SECONDS)
    if cached is not None:
        return cached

    url = SEARCH_URL.format(query=urllib.parse.quote(f"{query} site:{domain}"))
    try:
        root = ET.fromstring(plain_get_text(url, attempts=2))
    except ET.ParseError:
        return []

    items = []
    for item in root.findall(".//item"):
        items.append(
            {
                "title": (item.findtext("title") or "").strip(),
                "link": _direct_url(item.findtext("link") or ""),
                "published": (item.findtext("pubDate") or "").strip(),
            }
        )
    write_cache(name, items)
    return items


def _published(raw: str):
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _query_for(name: str, ticker: str) -> str:
    """Cast wide: the headline filter is what enforces relevance."""
    parts = aliases(name, ticker)
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(f'"{part}"' for part in parts) + ")"


def for_target(target, now=None) -> list:
    now = now or datetime.now(timezone.utc)
    names = aliases(target.name, target.ticker)
    if not names:
        return []
    ticker = (target.ticker or "").strip().lower()
    name_patterns = [_pattern(alias) for alias in names if alias.lower() != ticker]
    ticker_patterns = [_pattern(alias) for alias in names if alias.lower() == ticker]
    query = _query_for(target.name, target.ticker)

    found = {}
    for label, domain in SOURCES:
        for item in _search(query, domain):
            moment = _published(item["published"])
            if moment is None:
                continue
            age_days = (now - moment).total_seconds() / 86400
            if age_days < 0 or age_days > NEWS_WINDOW_DAYS:
                continue

            headline, url = item["title"], item["link"]
            if not url or not is_article(url, headline):
                continue
            position = headline_position(headline, name_patterns)
            by_ticker = position < 0
            if by_ticker:
                position = headline_position(headline, ticker_patterns)
            if position < 0:
                continue

            # The same story reaches us through more than one query; keep the
            # first sighting of a headline rather than repeating it.
            fingerprint = _UNSAFE.sub("", headline).lower()[:90]
            if fingerprint in found:
                continue
            found[fingerprint] = Article(
                key=target.key,
                company_name=target.name,
                source=label,
                headline=headline,
                url=url,
                published=moment,
                score=relevance(headline, position, age_days, by_ticker),
            )

    ranked = sorted(found.values(), key=lambda item: (-item.score, -item.published.timestamp()))
    return ranked[:NEWS_PER_COMPANY]


def collect(targets) -> tuple:
    """News for each target, fetched in parallel. Returns (articles, warnings)."""
    targets = list(targets)
    if not targets:
        return [], []

    now = datetime.now(timezone.utc)
    workers = max(1, min(NEWS_WORKERS, len(targets)))

    def safely(target):
        try:
            return for_target(target, now), None
        except Exception as exc:
            return [], f"{target.name}: news unavailable ({exc})."

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(safely, targets))

    articles, warnings = [], []
    for found, problem in results:
        articles.extend(found)
        if problem:
            warnings.append(problem)

    articles.sort(key=lambda item: (item.company_name, -item.score))
    return [article.as_dict() for article in articles], warnings
