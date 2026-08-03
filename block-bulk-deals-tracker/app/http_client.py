import json
import threading
import time
from pathlib import Path

from curl_cffi import requests

from .config import CACHE_DIR, REQUEST_TIMEOUT

NSE_HOME = "https://www.nseindia.com/"
NSE_REPORT_PAGE = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"

# A curl handle cannot be shared between threads, and company metrics are fetched
# on a pool, so every thread keeps its own sessions and its own NSE cookies.
_local = threading.local()
_cache_lock = threading.Lock()


def _new_session(impersonate: str = "chrome"):
    session = requests.Session(impersonate=impersonate)
    session.headers.update({"Accept-Language": "en-US,en;q=0.9"})
    return session


def nse_session():
    """NSE serves its APIs only to clients that already hold page cookies."""
    session = getattr(_local, "nse", None)
    if session is None:
        session = _new_session()
        session.get(NSE_HOME, timeout=REQUEST_TIMEOUT)
        session.get(NSE_REPORT_PAGE, timeout=REQUEST_TIMEOUT)
        session.headers.update(
            {"Referer": NSE_REPORT_PAGE, "Accept": "*/*", "X-Requested-With": "XMLHttpRequest"}
        )
        _local.nse = session
    return session


def reset_nse_session():
    _local.nse = None


def bse_session():
    session = getattr(_local, "bse", None)
    if session is None:
        session = _new_session()
        session.headers.update(
            {
                "Referer": "https://www.bseindia.com/",
                "Origin": "https://www.bseindia.com",
                "Accept": "application/json, text/plain, */*",
            }
        )
        _local.bse = session
    return session


def nse_get_text(url: str, attempts: int = 3) -> str:
    last_error = None
    for attempt in range(attempts):
        try:
            response = nse_session().get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.text
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # network hiccups are common against NSE
            last_error = repr(exc)
        reset_nse_session()
        time.sleep(1 + attempt)
    raise RuntimeError(f"NSE request failed ({last_error}): {url}")


def bse_get_json(url: str, attempts: int = 3):
    last_error = None
    for attempt in range(attempts):
        try:
            response = bse_session().get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(1 + attempt)
    raise RuntimeError(f"BSE request failed ({last_error}): {url}")


def plain_session():
    session = getattr(_local, "plain", None)
    if session is None:
        session = _new_session()
        _local.plain = session
    return session


def plain_get_text(url: str, attempts: int = 3) -> str:
    last_error = None
    for attempt in range(attempts):
        try:
            response = plain_session().get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.text
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(1 + attempt)
    raise RuntimeError(f"Request failed ({last_error}): {url}")


def cache_path(name: str) -> Path:
    return CACHE_DIR / name


def read_cache(name: str, ttl_seconds: int):
    path = cache_path(name)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > ttl_seconds:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_cache(name: str, payload) -> None:
    path = cache_path(name)
    # Written via a temporary file so a concurrent reader never sees half a file.
    temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        with _cache_lock:
            temporary.replace(path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
