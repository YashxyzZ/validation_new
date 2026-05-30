import csv
import io
import logging
from typing import List, Dict

from cache import get_cached, set_cache
from client import fetch_report_from_oracle
from config import (
    RECEIPT_REPORT_PATH,
    INVOICE_REPORT_PATH,
)

logger = logging.getLogger(__name__)


def _normalize_key(key: str) -> str:
    return key.strip().upper().replace(" ", "_")


def _normalize_rows(rows: List[Dict]) -> List[Dict]:
    normalized = [{_normalize_key(k): v for k, v in row.items()} for row in rows]
    if normalized:
        logger.info("CSV columns (normalized): %s", list(normalized[0].keys()))
        logger.info("CSV sample row: %s", normalized[0])
    else:
        logger.warning("CSV returned 0 rows")
    return normalized


def _parse_csv_bytes(csv_bytes: bytes) -> List[Dict]:
    text = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    logger.info("Parsed %d rows from Oracle CSV", len(rows))
    return _normalize_rows(rows)


def _fetch_rows(
    report_path: str,
    cache_key: str = None,
    params: Dict[str, str] = None,
) -> List[Dict]:
    """Fetch report rows. Uses cache when cache_key is set and no params."""
    if cache_key and not params:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached

    csv_bytes = fetch_report_from_oracle(report_path, params=params)
    rows = _parse_csv_bytes(csv_bytes)

    if cache_key and not params:
        set_cache(cache_key, rows)

    return rows


# ── Receipt helpers ──

def get_all_receipt_rows() -> List[Dict]:
    """Fetch all receipt rows (cached for 5 min)."""
    return _fetch_rows(RECEIPT_REPORT_PATH, cache_key="receipt")


def get_filtered_receipt_rows(
    customer_name: str = None,
    amount: float = None,
    receipt_num: str = None,
    receipt_date: str = None,
) -> List[Dict]:
    """Fetch applied receipt rows — try SOAP params one at a time until data comes back."""
    candidates = []
    if customer_name:
        candidates.append({"P_CUSTOMER_NAME": str(customer_name)})
    if receipt_num:
        candidates.append({"P_RECEIPT_NUMBER": str(receipt_num)})
    if amount is not None:
        candidates.append({"P_RECEIPT_AMOUNT": str(amount)})
    if receipt_date:
        candidates.append({"P_RECEIPT_DATE": str(receipt_date)})

    for params in candidates:
        rows = _fetch_rows(RECEIPT_REPORT_PATH, params=params)
        if rows and any(row.get("RECEIPT_NUMBER") for row in rows):
            logger.info("Receipt filter %s returned %d data rows", params, len(rows))
            return rows
        logger.info("Receipt filter %s returned no data rows, trying next", params)

    return []


# ── Invoice helpers ──

def get_all_invoice_rows() -> List[Dict]:
    """Fetch all invoice rows (cached for 5 min)."""
    return _fetch_rows(INVOICE_REPORT_PATH, cache_key="invoice")


def get_filtered_invoice_rows(
    customer_name: str = None,
    amount: float = None,
    invoice_num: str = None,
    invoice_date: str = None,
) -> List[Dict]:
    """Fetch invoice rows — try SOAP params one at a time until data comes back."""
    candidates = []
    if customer_name:
        candidates.append({"P_CUSTOMER_NAME": str(customer_name)})
    if invoice_num:
        candidates.append({"P_INVOICE_NUM": str(invoice_num)})
    if amount is not None:
        candidates.append({"P_INVOICE_AMOUNT": str(amount)})
    if invoice_date:
        candidates.append({"P_INVOICE_DATE": str(invoice_date)})

    for params in candidates:
        rows = _fetch_rows(INVOICE_REPORT_PATH, params=params)
        if rows and any(row.get("TRANSACTION_NUMBER") for row in rows):
            logger.info("Invoice filter %s returned %d data rows", params, len(rows))
            return rows
        logger.info("Invoice filter %s returned no data rows, trying next", params)

    return []
