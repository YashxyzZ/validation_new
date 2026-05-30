import csv
import io
import logging
import concurrent.futures
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from models import ReceiptRecord, MatchedRecord
from reports import (
    get_all_receipt_rows,
    get_all_invoice_rows,
    get_filtered_receipt_rows,
    get_filtered_invoice_rows,
)
from matching import match_receipt, match_invoice_item
from client import AuthError, ReportError
from cache import cache_info, clear_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CashApp Remittance Validation",
    version="3.0.0",
    description="Validates AI-extracted remittance data against Oracle Fusion reports",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════
#  POST /reports/match  — Single remittance validation
#  Two-step cascading: SUB 1 (unapplied/cached) → SUB 2 (applied/filtered)
# ═══════════════════════════════════════════════════════════════

@app.post("/reports/match", response_model=MatchedRecord)
def match_remittance(record: ReceiptRecord):

    # ── 1. Fetch all data (cached, parallel) for SUB 1 ──
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            receipt_future = pool.submit(get_all_receipt_rows)
            invoice_future = pool.submit(get_all_invoice_rows)

            all_receipt_rows = receipt_future.result()
            all_invoice_rows = invoice_future.result()

    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    logger.info("Fetched %d receipt rows, %d invoice rows", len(all_receipt_rows), len(all_invoice_rows))

    # ── 2. Receipt matching ──

    # SUB 1: Unapplied receipts (SQL returns only UNAPP/UNID when no params)
    logger.info("Receipt SUB 1: %d rows (unapplied from cache)", len(all_receipt_rows))
    receipt_result = match_receipt(record, all_receipt_rows)

    # SUB 2: Applied receipts with filters (SQL returns only APP/REV when params sent)
    if receipt_result["receipt_match_scenario"] is None:
        sub1_reason = receipt_result.get("receipt_no_match_reason", "")
        logger.info("Receipt SUB 1 no match — trying SUB 2 (applied with filters)")

        try:
            applied_receipt_rows = get_filtered_receipt_rows(
                customer_name=record.customer_name,
                amount=record.total_amount,
                receipt_num=record.payment_reference,
                receipt_date=record.payment_date,
            )
            logger.info("Receipt SUB 2: Oracle returned %d rows", len(applied_receipt_rows))
            receipt_result = match_receipt(record, applied_receipt_rows)

            if receipt_result["receipt_match_scenario"] is None:
                receipt_result["receipt_no_match_reason"] = (
                    f"Unapplied: {sub1_reason} | Applied: {receipt_result.get('receipt_no_match_reason', '')}"
                )

        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))
        except ReportError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    # ── 3. Invoice matching (per invoice line) ──
    fused_invoices = []
    for inv in record.invoices:
        # SUB 1: Try all cached invoice rows
        inv_result = match_invoice_item(inv, all_invoice_rows, customer_name=record.customer_name)

        # SUB 2: Filtered call (only if SUB 1 failed)
        if inv_result.invoice_match_scenario is None:
            sub1_inv_reason = inv_result.invoice_no_match_reason or ""
            logger.info("Invoice SUB 1 no match for '%s' — trying SUB 2", inv.invoice_number)

            try:
                filtered_inv_rows = get_filtered_invoice_rows(
                    customer_name=record.customer_name,
                    amount=inv.invoice_amount,
                    invoice_num=inv.invoice_number,
                    invoice_date=inv.invoice_date,
                )
                inv_result = match_invoice_item(inv, filtered_inv_rows, customer_name=record.customer_name)

                if inv_result.invoice_match_scenario is None:
                    inv_result.invoice_no_match_reason = (
                        f"Cached: {sub1_inv_reason} | Filtered: {inv_result.invoice_no_match_reason or ''}"
                    )

            except (AuthError, ReportError) as exc:
                logger.warning("Invoice SUB 2 failed for '%s': %s", inv.invoice_number, exc)

        fused_invoices.append(inv_result)

    # ── 4. Build validated output ──
    return MatchedRecord(
        customer_name=record.customer_name,
        payment_reference=record.payment_reference,
        payment_date=record.payment_date,
        header_id=record.header_id,
        total_amount=record.total_amount,
        confidence_label=record.confidence_label,
        confidence_score=record.confidence_score,
        fusion_receipt_number=receipt_result["fusion_receipt_number"],
        fusion_receipt_date=receipt_result["fusion_receipt_date"],
        fusion_receipt_amount=receipt_result.get("fusion_receipt_amount"),
        fusion_customer_name=receipt_result["fusion_customer_name"],
        fusion_customer_number=receipt_result.get("fusion_customer_number"),
        fusion_currency=receipt_result.get("fusion_currency"),
        fusion_receipt_status=receipt_result.get("fusion_receipt_status"),
        fusion_applied_amount=receipt_result.get("fusion_applied_amount"),
        receipt_match_scenario=receipt_result["receipt_match_scenario"],
        receipt_match_reason=receipt_result.get("receipt_match_reason"),
        receipt_no_match_reason=receipt_result.get("receipt_no_match_reason"),
        invoices=fused_invoices,
    )


# ═══════════════════════════════════════════════════════════════
#  Utility endpoints
# ═══════════════════════════════════════════════════════════════

@app.get("/cache/info")
def get_cache_info():
    """Check current cache state (age, row counts)."""
    return cache_info()


@app.post("/cache/clear")
def post_clear_cache():
    """Force-clear the in-memory cache."""
    clear_cache()
    return {"status": "cache cleared"}


@app.get("/reports/search")
def search_reports(customer: str = "", invoice: str = ""):
    """Search cached report data — verify what Oracle actually returned."""
    try:
        receipt_rows = get_all_receipt_rows()
        invoice_rows = get_all_invoice_rows()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    results = {"receipt_matches": [], "invoice_matches": [], "total_receipts": len(receipt_rows), "total_invoices": len(invoice_rows)}

    if customer:
        q = customer.strip().lower()
        results["receipt_matches"] = [
            row for row in receipt_rows
            if q in (row.get("BILL_CUSTOMER_NAME") or "").lower()
        ][:10]
        results["invoice_matches"] = [
            row for row in invoice_rows
            if q in (row.get("BILL_CUSTOMER_NAME") or "").lower()
        ][:10]

    if invoice:
        q = invoice.strip().lower()
        results["invoice_matches"] = [
            row for row in invoice_rows
            if q in (row.get("TRANSACTION_NUMBER") or "").lower()
        ][:10]

    return results


@app.get("/health")
def health():
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════
#  Download endpoints — export Oracle report data as CSV
# ═══════════════════════════════════════════════════════════════

def _rows_to_csv(rows: List[dict]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


@app.get("/reports/download/receipt")
def download_receipt_report():
    """Download the full receipt report as a CSV file."""
    try:
        rows = get_all_receipt_rows()
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    csv_content = _rows_to_csv(rows)
    return StreamingResponse(
        io.BytesIO(csv_content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=receipt_report.csv"},
    )


@app.get("/reports/download/invoice")
def download_invoice_report():
    """Download the full invoice report as a CSV file."""
    try:
        rows = get_all_invoice_rows()
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    csv_content = _rows_to_csv(rows)
    return StreamingResponse(
        io.BytesIO(csv_content.encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoice_report.csv"},
    )


# ═══════════════════════════════════════════════════════════════
#  Run with: python -m uvicorn main:app --reload --port 8000
# ═══════════════════════════════════════════════════════════════
