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

# Several named companies are searched at once, each over its own date range.
DEAL_WORKERS = 5

# Company news: how far back to look, how much to keep, and how hard to parallelise.
NEWS_WINDOW_DAYS = 3
NEWS_PER_COMPANY = 15
NEWS_WORKERS = 12
NEWS_TTL_SECONDS = 15 * 60

REQUEST_TIMEOUT = 90
