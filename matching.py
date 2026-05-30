import re
import logging
from typing import Any, Optional, List, Dict

from models import ReceiptRecord, InvoiceItem, FusedInvoiceItem
from config import AMOUNT_TOLERANCE

logger = logging.getLogger(__name__)

MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


# ═══════════════════════════════════════════════════════════════
#  DATE HELPERS
# ═══════════════════════════════════════════════════════════════

def _normalize_date(date_str: str) -> Optional[str]:
    if not date_str:
        return None
    date_str = date_str.strip()

    # YYYY-MM-DDThh:mm:ss (ISO 8601 — strip time portion)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T", date_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # YYYY-MM-DD or YYYY/MM/DD
    m = re.match(r"^(\d{4})[/-](\d{2})[/-](\d{2})$", date_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # DD-MM-YYYY or DD/MM/YYYY — also handles MM-DD-YYYY when month > 12
    m = re.match(r"^(\d{2})[/-](\d{2})[/-](\d{4})$", date_str)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            # a can't be month → treat as DD-MM-YYYY
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if b > 12 and a <= 12:
            # b can't be month → treat as MM-DD-YYYY
            return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        if a <= 12 and b <= 12:
            # ambiguous — default to DD-MM-YYYY
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # DD-Mon-YYYY or DD-MON-YYYY (e.g. 15-May-2026, 15-MAY-2026)
    m = re.match(r"^(\d{1,2})[/-]([A-Za-z]{3})[/-](\d{4})$", date_str)
    if m:
        mon = MONTH_MAP.get(m.group(2).lower())
        if mon:
            return f"{m.group(3)}-{mon}-{int(m.group(1)):02d}"

    # DD-Mon-YY (e.g. 15-May-26)
    m = re.match(r"^(\d{1,2})[/-]([A-Za-z]{3})[/-](\d{2})$", date_str)
    if m:
        mon = MONTH_MAP.get(m.group(2).lower())
        if mon:
            year = int(m.group(3))
            full_year = 2000 + year if year < 100 else year
            return f"{full_year}-{mon}-{int(m.group(1)):02d}"

    return None


def _dates_match(date_a: Optional[str], date_b: Optional[str]) -> bool:
    if not date_a or not date_b:
        return False
    norm_a = _normalize_date(date_a)
    norm_b = _normalize_date(date_b)
    if norm_a and norm_b:
        return norm_a == norm_b
    return False


def _format_date_for_output(date_str: str) -> Optional[str]:
    norm = _normalize_date(date_str)
    if norm:
        return norm.replace("-", "/")
    return None


# ═══════════════════════════════════════════════════════════════
#  AMOUNT HELPER
# ═══════════════════════════════════════════════════════════════

def _amounts_match(csv_amount_str: str, expected: Optional[float]) -> bool:
    if expected is None:
        return False
    try:
        csv_val = float(str(csv_amount_str).replace(",", ""))
    except (ValueError, TypeError):
        return False
    return abs(abs(csv_val) - abs(expected)) < AMOUNT_TOLERANCE


# ═══════════════════════════════════════════════════════════════
#  RECEIPT MATCHING — Rule 2
#  A] payment_reference not null: A1 → A2 → A3 → A4 → A5
#  B] payment_reference null:     B1 → B2 → B3
# ═══════════════════════════════════════════════════════════════

def _extract_receipt_fields(row: Dict) -> Dict[str, Any]:
    receipt_amount = None
    applied_amount = None
    try:
        receipt_amount = float(str(row.get("RECEIPT_AMOUNT", "0")).replace(",", ""))
    except (ValueError, TypeError):
        pass
    try:
        applied_amount = float(str(row.get("APPLIED_AMOUNT", "0")).replace(",", ""))
    except (ValueError, TypeError):
        pass

    return {
        "fusion_receipt_number": (row.get("RECEIPT_NUMBER") or "").strip(),
        "fusion_receipt_date": _format_date_for_output(
            (row.get("RECEIPT_DATE") or "").strip()
        ),
        "fusion_receipt_amount": receipt_amount,
        "fusion_customer_name": (row.get("BILL_CUSTOMER_NAME") or "").strip(),
        "fusion_customer_number": (row.get("BILL_CUSTOMER_NUMBER") or "").strip(),
        "fusion_currency": (row.get("CURRENCY") or "").strip(),
        "fusion_receipt_status": (row.get("RECEIPT_STATUS_CODE") or "").strip(),
        "fusion_applied_amount": applied_amount,
    }


_NO_RECEIPT_MATCH: Dict[str, Any] = {
    "fusion_receipt_number": None,
    "fusion_receipt_date": None,
    "fusion_receipt_amount": None,
    "fusion_customer_name": None,
    "fusion_customer_number": None,
    "fusion_currency": None,
    "fusion_receipt_status": None,
    "fusion_applied_amount": None,
    "receipt_match_scenario": None,
    "receipt_match_reason": None,
    "receipt_no_match_reason": None,
}


def _step_reason(step: str, count: int, criteria: str) -> str:
    if count == 0:
        return f"{step}: 0 rows matched ({criteria})"
    return f"{step}: {count} rows matched - ambiguous ({criteria})"


def match_receipt(
    record: ReceiptRecord, receipt_rows: List[Dict]
) -> Dict[str, Optional[str]]:

    logger.info(
        "Receipt matching: customer='%s', ref='%s', date='%s', amount=%s, rows=%d",
        record.customer_name, record.payment_reference,
        record.payment_date, record.total_amount, len(receipt_rows),
    )

    cust_name_lower = record.customer_name.strip().lower() if record.customer_name else ""

    # ── Scenario A: payment_reference IS provided ──
    if record.payment_reference:
        ref_lower = record.payment_reference.lower()
        reasons = []

        # A1: payment_reference substring + total_amount + (customer_name if not null)
        matches = [
            row for row in receipt_rows
            if ref_lower in (row.get("RECEIPT_NUMBER") or "").strip().lower()
            and _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            and (not cust_name_lower or (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower)
        ]
        logger.info("A1: %d matches (ref substring + amount + customer)", len(matches))
        if len(matches) == 1:
            result = _extract_receipt_fields(matches[0])
            result["receipt_match_scenario"] = "A1"
            result["receipt_match_reason"] = "Matched by payment_reference substring + amount" + (" + customer_name" if cust_name_lower else "")
            return result
        reasons.append(_step_reason("A1", len(matches), "payment_reference substring + amount" + (" + customer_name" if cust_name_lower else "")))

        # A2: payment_reference substring + (customer_name if not null)
        matches = [
            row for row in receipt_rows
            if ref_lower in (row.get("RECEIPT_NUMBER") or "").strip().lower()
            and (not cust_name_lower or (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower)
        ]
        logger.info("A2: %d matches (ref substring + customer)", len(matches))
        if len(matches) == 1:
            result = _extract_receipt_fields(matches[0])
            result["receipt_match_scenario"] = "A2"
            result["receipt_match_reason"] = "Matched by payment_reference substring" + (" + customer_name" if cust_name_lower else "")
            return result
        reasons.append(_step_reason("A2", len(matches), "payment_reference substring" + (" + customer_name" if cust_name_lower else "")))

        # A3: payment_reference substring + total_amount + payment_date + (customer_name if not null)
        matches = [
            row for row in receipt_rows
            if ref_lower in (row.get("RECEIPT_NUMBER") or "").strip().lower()
            and _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            and _dates_match(record.payment_date, row.get("RECEIPT_DATE", ""))
            and (not cust_name_lower or (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower)
        ]
        logger.info("A3: %d matches (ref substring + amount + date + customer)", len(matches))
        if len(matches) == 1:
            result = _extract_receipt_fields(matches[0])
            result["receipt_match_scenario"] = "A3"
            result["receipt_match_reason"] = "Matched by payment_reference substring + amount + date" + (" + customer_name" if cust_name_lower else "")
            return result
        reasons.append(_step_reason("A3", len(matches), "payment_reference substring + amount + date" + (" + customer_name" if cust_name_lower else "")))

        # A4: customer_name + total_amount
        if cust_name_lower:
            matches = [
                row for row in receipt_rows
                if (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower
                and _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            ]
            logger.info("A4: %d matches (customer + amount)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "A4"
                result["receipt_match_reason"] = "Matched by customer_name + amount"
                return result
            reasons.append(_step_reason("A4", len(matches), "customer_name + amount"))
        else:
            logger.info("A4: skipped (no customer_name)")
            reasons.append("A4: skipped (no customer_name)")

        # A5: customer_name + payment_date
        if cust_name_lower:
            matches = [
                row for row in receipt_rows
                if (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower
                and _dates_match(record.payment_date, row.get("RECEIPT_DATE", ""))
            ]
            logger.info("A5: %d matches (customer + date)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "A5"
                result["receipt_match_reason"] = "Matched by customer_name + date"
                return result
            reasons.append(_step_reason("A5", len(matches), "customer_name + date"))
        else:
            logger.info("A5: skipped (no customer_name)")
            reasons.append("A5: skipped (no customer_name)")

        # A6: total_amount + payment_date (no customer_name needed)
        if record.total_amount is not None and record.payment_date:
            matches = [
                row for row in receipt_rows
                if _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
                and _dates_match(record.payment_date, row.get("RECEIPT_DATE", ""))
            ]
            logger.info("A6: %d matches (amount + date)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "A6"
                result["receipt_match_reason"] = "Matched by amount + date"
                return result
            reasons.append(_step_reason("A6", len(matches), "amount + date"))
        else:
            missing = []
            if record.total_amount is None:
                missing.append("total_amount")
            if not record.payment_date:
                missing.append("payment_date")
            logger.info("A6: skipped (no %s)", ", ".join(missing))
            reasons.append(f"A6: skipped (no {', '.join(missing)})")

        # A7: total_amount only (last resort)
        if record.total_amount is not None:
            matches = [
                row for row in receipt_rows
                if _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            ]
            logger.info("A7: %d matches (amount only)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "A7"
                result["receipt_match_reason"] = "Matched by amount only"
                return result
            reasons.append(_step_reason("A7", len(matches), "amount only"))
        else:
            logger.info("A7: skipped (no total_amount)")
            reasons.append("A7: skipped (no total_amount)")

        # All A steps failed
        logger.warning("No receipt match across A1-A7")
        no_match = dict(_NO_RECEIPT_MATCH)
        no_match["receipt_no_match_reason"] = "; ".join(reasons)
        return no_match

    # ── Scenario B: payment_reference IS NULL ──
    else:
        reasons = []

        # B1: total_amount + payment_date + (customer_name if not null)
        matches = [
            row for row in receipt_rows
            if _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            and _dates_match(record.payment_date, row.get("RECEIPT_DATE", ""))
            and (not cust_name_lower or (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower)
        ]
        logger.info("B1: %d matches (amount + date + customer)", len(matches))
        if len(matches) == 1:
            result = _extract_receipt_fields(matches[0])
            result["receipt_match_scenario"] = "B1"
            result["receipt_match_reason"] = "Matched by amount + date" + (" + customer_name" if cust_name_lower else "")
            return result
        reasons.append(_step_reason("B1", len(matches), "amount + date" + (" + customer_name" if cust_name_lower else "")))

        # B2: customer_name + total_amount
        if cust_name_lower:
            matches = [
                row for row in receipt_rows
                if (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower
                and _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            ]
            logger.info("B2: %d matches (customer + amount)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "B2"
                result["receipt_match_reason"] = "Matched by customer_name + amount"
                return result
            reasons.append(_step_reason("B2", len(matches), "customer_name + amount"))
        else:
            logger.info("B2: skipped (no customer_name)")
            reasons.append("B2: skipped (no customer_name)")

        # B3: customer_name + payment_date
        if cust_name_lower:
            matches = [
                row for row in receipt_rows
                if (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower
                and _dates_match(record.payment_date, row.get("RECEIPT_DATE", ""))
            ]
            logger.info("B3: %d matches (customer + date)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "B3"
                result["receipt_match_reason"] = "Matched by customer_name + date"
                return result
            reasons.append(_step_reason("B3", len(matches), "customer_name + date"))
        else:
            logger.info("B3: skipped (no customer_name)")
            reasons.append("B3: skipped (no customer_name)")

        # B4: total_amount only (last resort, no customer_name needed)
        if record.total_amount is not None:
            matches = [
                row for row in receipt_rows
                if _amounts_match(row.get("RECEIPT_AMOUNT", ""), record.total_amount)
            ]
            logger.info("B4: %d matches (amount only)", len(matches))
            if len(matches) == 1:
                result = _extract_receipt_fields(matches[0])
                result["receipt_match_scenario"] = "B4"
                result["receipt_match_reason"] = "Matched by amount only"
                return result
            reasons.append(_step_reason("B4", len(matches), "amount only"))
        else:
            logger.info("B4: skipped (no total_amount)")
            reasons.append("B4: skipped (no total_amount)")

        # All B steps failed
        logger.warning("No receipt match across B1-B4")
        no_match = dict(_NO_RECEIPT_MATCH)
        no_match["receipt_no_match_reason"] = "; ".join(reasons)
        return no_match


# ═══════════════════════════════════════════════════════════════
#  INVOICE MATCHING — Rule 3
#  Order: Step 0 → 1a → 1a-sub → 1b → 2 → 3   (per invoice line)
# ═══════════════════════════════════════════════════════════════

def _build_fused_invoice(
    invoice: InvoiceItem,
    row: Optional[Dict] = None,
    step: Optional[str] = None,
    match_reason: Optional[str] = None,
    no_match_reason: Optional[str] = None,
) -> FusedInvoiceItem:

    fusion_number = None
    fusion_date = None
    fusion_amount = None
    fusion_type = None
    fusion_status = None

    if row is not None:
        fusion_number = (row.get("TRANSACTION_NUMBER") or "").strip()
        fusion_date = _format_date_for_output(
            (row.get("TRANSACTION_DATE") or "").strip()
        )
        try:
            fusion_amount = float(
                str(row.get("TOTAL_AMOUNTS", "0")).replace(",", "")
            )
        except (ValueError, TypeError):
            fusion_amount = None
        fusion_type = (row.get("INVOICE_TYPE") or "").strip() or None
        fusion_status = (row.get("INVOICE_STATUS") or "").strip() or None

    return FusedInvoiceItem(
        Line_ID=invoice.Line_ID,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        invoice_amount=invoice.invoice_amount,
        customer_invoice_number=invoice.customer_invoice_number,
        store_no=invoice.store_no,
        description=invoice.description,
        fusion_invoice_number=fusion_number,
        fusion_invoice_date=fusion_date,
        fusion_invoice_amount=fusion_amount,
        fusion_invoice_type=fusion_type,
        fusion_invoice_status=fusion_status,
        invoice_match_scenario=step,
        invoice_match_reason=match_reason,
        invoice_no_match_reason=no_match_reason,
    )


def match_invoice_item(
    invoice: InvoiceItem, invoice_rows: List[Dict], customer_name: str = ""
) -> FusedInvoiceItem:

    inv_num = invoice.invoice_number.strip().lower() if invoice.invoice_number else None
    cust_name_lower = customer_name.strip().lower() if customer_name else ""

    logger.info(
        "Invoice matching: num='%s', date='%s', amount=%s, rows=%d",
        invoice.invoice_number, invoice.invoice_date,
        invoice.invoice_amount, len(invoice_rows),
    )

    # ── Step 0: invoice_number is NULL → match by date + amount + customer ──
    if inv_num is None:
        if cust_name_lower and invoice.invoice_date and invoice.invoice_amount is not None:
            matches = [
                row
                for row in invoice_rows
                if _dates_match(invoice.invoice_date, row.get("TRANSACTION_DATE", ""))
                and _amounts_match(row.get("TOTAL_AMOUNTS", ""), invoice.invoice_amount)
                and (row.get("BILL_CUSTOMER_NAME") or "").strip().lower() == cust_name_lower
            ]

            if len(matches) == 1:
                logger.info("Invoice matched at Step 0 (date+amount+customer)")
                return _build_fused_invoice(invoice, matches[0], step="0", match_reason="Matched by date + amount + customer_name")

            reason = _step_reason("Step 0", len(matches), "date + amount + customer_name")
        else:
            missing = []
            if not cust_name_lower:
                missing.append("customer_name")
            if not invoice.invoice_date:
                missing.append("invoice_date")
            if invoice.invoice_amount is None:
                missing.append("invoice_amount")
            reason = f"Step 0: skipped (missing {', '.join(missing)})"

        logger.warning("No invoice match — invoice_number is null")
        return _build_fused_invoice(invoice, row=None, step=None, no_match_reason=reason)

    reasons = []

    # ── Step 1a: Exact match on invoice_number ONLY ──
    matches = [
        row
        for row in invoice_rows
        if (row.get("TRANSACTION_NUMBER") or "").strip().lower() == inv_num
    ]

    logger.info("Step 1a: %d matches (exact invoice_number)", len(matches))
    if len(matches) == 1:
        return _build_fused_invoice(invoice, matches[0], step="1a", match_reason="Matched by exact invoice_number")
    reasons.append(_step_reason("Step 1a", len(matches), "exact invoice_number"))

    # ── Step 1a-sub: Substring match on invoice_number + amount ──
    matches = [
        row
        for row in invoice_rows
        if inv_num in (row.get("TRANSACTION_NUMBER") or "").strip().lower()
        and _amounts_match(row.get("TOTAL_AMOUNTS", ""), invoice.invoice_amount)
    ]

    logger.info("Step 1a-sub: %d matches (substring + amount)", len(matches))
    if len(matches) == 1:
        return _build_fused_invoice(invoice, matches[0], step="1a-sub", match_reason="Matched by substring invoice_number + amount")
    reasons.append(_step_reason("Step 1a-sub", len(matches), "substring invoice_number + amount"))

    # ── Step 1b: invoice_number + invoice_date + invoice_amount ──
    if invoice.invoice_date:
        matches = [
            row
            for row in invoice_rows
            if (row.get("TRANSACTION_NUMBER") or "").strip().lower() == inv_num
            and _dates_match(invoice.invoice_date, row.get("TRANSACTION_DATE", ""))
            and _amounts_match(row.get("TOTAL_AMOUNTS", ""), invoice.invoice_amount)
        ]

        logger.info("Step 1b: %d matches (num + date + amount)", len(matches))
        if len(matches) == 1:
            return _build_fused_invoice(invoice, matches[0], step="1b", match_reason="Matched by exact invoice_number + date + amount")
        reasons.append(_step_reason("Step 1b", len(matches), "exact invoice_number + date + amount"))
    else:
        reasons.append("Step 1b: skipped (no invoice_date)")

    # ── Step 2: customer_invoice_number + date + amount ──
    if invoice.customer_invoice_number and invoice.invoice_date:
        cust_inv_num = invoice.customer_invoice_number.strip().lower()
        matches = [
            row
            for row in invoice_rows
            if (row.get("TRANSACTION_NUMBER") or "").strip().lower() == cust_inv_num
            and _dates_match(invoice.invoice_date, row.get("TRANSACTION_DATE", ""))
            and _amounts_match(row.get("TOTAL_AMOUNTS", ""), invoice.invoice_amount)
        ]

        logger.info("Step 2: %d matches (cust_inv_num + date + amount)", len(matches))
        if len(matches) == 1:
            return _build_fused_invoice(invoice, matches[0], step="2", match_reason="Matched by customer_invoice_number + date + amount")
        reasons.append(_step_reason("Step 2", len(matches), "customer_invoice_number + date + amount"))
    else:
        missing = []
        if not invoice.customer_invoice_number:
            missing.append("customer_invoice_number")
        if not invoice.invoice_date:
            missing.append("invoice_date")
        reasons.append(f"Step 2: skipped (no {', '.join(missing)})")

    # ── Step 3: Substring fallback + date + amount ──
    if invoice.invoice_date:
        matches = [
            row
            for row in invoice_rows
            if inv_num in (row.get("TRANSACTION_NUMBER") or "").strip().lower()
            and _dates_match(invoice.invoice_date, row.get("TRANSACTION_DATE", ""))
            and _amounts_match(row.get("TOTAL_AMOUNTS", ""), invoice.invoice_amount)
        ]

        logger.info("Step 3: %d matches (substring + date + amount)", len(matches))
        if len(matches) == 1:
            return _build_fused_invoice(invoice, matches[0], step="3", match_reason="Matched by substring invoice_number + date + amount")
        reasons.append(_step_reason("Step 3", len(matches), "substring invoice_number + date + amount"))
    else:
        reasons.append("Step 3: skipped (no invoice_date)")

    # ── No match found ──
    no_match_reason = "; ".join(reasons)
    if invoice_rows:
        sample = invoice_rows[0]
        logger.warning(
            "No invoice match for '%s'. Sample row: TRANSACTION_NUMBER='%s', TRANSACTION_DATE='%s'",
            invoice.invoice_number,
            sample.get("TRANSACTION_NUMBER", "<MISSING>"),
            sample.get("TRANSACTION_DATE", "<MISSING>"),
        )
    else:
        logger.warning("No invoice match — Oracle returned 0 invoice rows")
    return _build_fused_invoice(invoice, row=None, step=None, no_match_reason=no_match_reason)
