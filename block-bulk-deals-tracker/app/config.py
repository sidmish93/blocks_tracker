from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

CRORE = 10_000_000

# NSE rejects historical ranges wider than about a year, so requests are split
# into whole calendar years. Its bulk/block archive begins in 2005.
NSE_DATA_START = date(2005, 1, 1)
BSE_DATA_START = date(1995, 1, 1)

# Reference masters change slowly; deal data for closed periods never changes.
REFDATA_TTL_SECONDS = 24 * 3600
CLOSED_PERIOD_TTL_SECONDS = 7 * 24 * 3600
OPEN_PERIOD_TTL_SECONDS = 30 * 60

# Two legs are treated as the same underlying trade reported in both the bulk and
# the block feed when client, side and quantity match and prices agree this closely.
DUPLICATE_PRICE_TOLERANCE = 0.01

# Trailing windows for the per-company market data, in calendar days.
METRIC_WINDOWS = [("1d", 1), ("1w", 7), ("1m", 30)]

# History pulled for those windows. Comfortably longer than the widest window so
# the reference close for a one-month return survives a long holiday stretch.
METRIC_HISTORY_DAYS = 75

# Companies are independent of each other, so their history is fetched together.
METRIC_WORKERS = 6

# Live CMP / open / previous close — short TTL so Excel and PDF in the same
# generate share one quote without going stale across runs.
QUOTE_TTL_SECONDS = 60

# Several named companies are searched at once, each over its own date range.
DEAL_WORKERS = 5

# NSE's price history endpoint answers with at most 70 sessions, always the most
# recent ones inside the range asked for, and says nothing about the truncation.
# Asking for a whole year quietly returns only its closing months, so history is
# pulled a quarter at a time: roughly 62 sessions, comfortably inside the cap.
NSE_HISTORY_ROW_LIMIT = 70

# The stretch of trading a deal is judged against, ending the session before it.
PRE_DEAL_WINDOW_DAYS = 30
# History pulled around each deal. Wide enough that the close the return is
# measured from survives a long holiday stretch at the start of the window.
PRE_DEAL_LOOKBACK_DAYS = 60
PRE_DEAL_WORKERS = 6

# How far the last close may sit from the day it is being used for. NSE moves a
# security between segments and the series it left simply stops, so a run of
# missing days is the sign of an incomplete download rather than a quiet stock.
# Wide enough for a long weekend against a cluster of holidays.
MAX_SESSION_GAP_DAYS = 7

# Company news: how far back to look, how much to keep, and how hard to parallelise.
NEWS_WINDOW_DAYS = 3
NEWS_PER_COMPANY = 15
NEWS_WORKERS = 12
NEWS_TTL_SECONDS = 15 * 60

# Latest quarterly filings from NSE. They settle after broadcast, so a short TTL
# is enough; several companies are fetched together.
RESULTS_TTL_SECONDS = 6 * 3600
RESULTS_WORKERS = 6

REQUEST_TIMEOUT = 90
