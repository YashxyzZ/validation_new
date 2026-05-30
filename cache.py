import time
import threading
import logging
from typing import Optional, List, Dict

from config import CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)

# ── In-Memory Cache Store ──
# Key   = report identifier ("receipt" or "invoice")
# Value = {"rows": List[Dict], "timestamp": float}
_cache: Dict[str, Dict] = {}
_lock = threading.Lock()


def get_cached(report_key: str) -> Optional[List[Dict]]:
    """Return cached rows if they exist and are fresh (< 5 min), else None."""
    with _lock:
        if report_key not in _cache:
            logger.info("Cache MISS for '%s' — no entry found", report_key)
            return None

        entry = _cache[report_key]
        age_seconds = time.time() - entry["timestamp"]

        if age_seconds >= CACHE_TTL_SECONDS:
            logger.info(
                "Cache STALE for '%s' — age %.1f s (threshold %d s)",
                report_key, age_seconds, CACHE_TTL_SECONDS,
            )
            del _cache[report_key]
            return None

        logger.info(
            "Cache HIT for '%s' — age %.1f s, %d rows",
            report_key, age_seconds, len(entry["rows"]),
        )
        return entry["rows"]


def set_cache(report_key: str, rows: List[Dict]) -> None:
    """Store rows in cache with current timestamp."""
    with _lock:
        _cache[report_key] = {
            "rows": rows,
            "timestamp": time.time(),
        }
    logger.info("Cache SET for '%s' — %d rows stored", report_key, len(rows))


def clear_cache() -> None:
    """Wipe all cached data (useful for testing or manual refresh)."""
    with _lock:
        _cache.clear()
    logger.info("Cache CLEARED — all entries removed")


def cache_info() -> Dict:
    """Return current cache state for debugging."""
    now = time.time()
    info = {}
    with _lock:
        for key, entry in _cache.items():
            age = now - entry["timestamp"]
            info[key] = {
                "rows": len(entry["rows"]),
                "age_seconds": round(age, 1),
                "fresh": age < CACHE_TTL_SECONDS,
            }
    return info
