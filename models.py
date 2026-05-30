from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List, Union


# ── Input Models (AI-extracted payload) ──

class InvoiceItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    Line_ID: Optional[int] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    invoice_amount: Optional[float] = None
    customer_invoice_number: Optional[str] = None
    store_no: Optional[str] = Field(None, alias="storeNo")
    description: Optional[str] = None

    @field_validator(
        "invoice_number", "invoice_date", "customer_invoice_number",
        "store_no", "description",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, v):
        if v == "":
            return None
        if v is not None:
            return str(v).strip()
        return v


class ReceiptRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    customer_name: Optional[str] = None
    payment_reference: Optional[str] = None
    payment_date: Optional[str] = None
    header_id: Optional[int] = None
    total_amount: Optional[float] = None
    confidence_label: Optional[str] = None
    confidence_score: Optional[Union[int, float]] = None
    invoices: List[InvoiceItem] = []

    @field_validator("customer_name", mode="before")
    @classmethod
    def clean_customer_name(cls, v):
        if not v or str(v).strip() == "":
            return None
        return str(v).strip()

    @field_validator(
        "payment_reference", "payment_date", "confidence_label",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, v):
        if v == "":
            return None
        return v


# ── Output Models (Validated / Fused) ──

class FusedInvoiceItem(BaseModel):
    Line_ID: Optional[int] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    invoice_amount: Optional[float] = None
    customer_invoice_number: Optional[str] = None
    store_no: Optional[str] = None
    description: Optional[str] = None
    fusion_invoice_number: Optional[str] = None
    fusion_invoice_date: Optional[str] = None
    fusion_invoice_amount: Optional[float] = None
    fusion_invoice_type: Optional[str] = None
    fusion_invoice_status: Optional[str] = None
    invoice_match_scenario: Optional[str] = None
    invoice_match_reason: Optional[str] = None
    invoice_no_match_reason: Optional[str] = None


class MatchedRecord(BaseModel):
    customer_name: Optional[str] = None
    payment_reference: Optional[str] = None
    payment_date: Optional[str] = None
    header_id: Optional[int] = None
    total_amount: Optional[float] = None
    confidence_label: Optional[str] = None
    confidence_score: Optional[Union[int, float]] = None
    fusion_receipt_number: Optional[str] = None
    fusion_receipt_date: Optional[str] = None
    fusion_receipt_amount: Optional[float] = None
    fusion_customer_name: Optional[str] = None
    fusion_customer_number: Optional[str] = None
    fusion_currency: Optional[str] = None
    fusion_receipt_status: Optional[str] = None
    fusion_applied_amount: Optional[float] = None
    receipt_match_scenario: Optional[str] = None
    receipt_match_reason: Optional[str] = None
    receipt_no_match_reason: Optional[str] = None
    invoices: List[FusedInvoiceItem] = []
