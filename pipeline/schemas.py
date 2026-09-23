from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class IncomingDocument(BaseModel):
    doc_id: str
    source: Literal["imap", "folder", "upload"]
    received_at: datetime
    sender: str | None
    filename: str
    content_path: str
    content_sha256: str | None = None
    intake_score: int | None = None
    intake_decision: Literal["process", "review", "ignore"] | None = None
    intake_reasons: list[str] = Field(default_factory=list)


class TriageResult(BaseModel):
    looks_like_invoice: bool
    preview_text: str
    reason: str


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


class Invoice(BaseModel):
    invoice_number: str | None = None
    invoice_date: date | None = None
    vendor_name_raw: str
    po_reference: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str = "USD"
    extraction_path: Literal["pdf_text", "vision"]
    extraction_confidence: float
    missing_fields: list[str] = Field(default_factory=list)


class VendorRecord(BaseModel):
    vendor_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    approved: bool = True


class PurchaseOrder(BaseModel):
    po_id: str
    vendor_id: str
    amount: Decimal
    currency: str = "USD"
    issued_date: date
    tax_treatment: Literal["inclusive", "exclusive"]
    invoiced_to_date: Decimal = Decimal("0")


class MatchEvidence(BaseModel):
    label: str
    detail: str
    weight: float


class MatchResult(BaseModel):
    po: PurchaseOrder | None = None
    vendor: VendorRecord | None = None
    score: float = 0.0
    evidence: list[MatchEvidence] = Field(default_factory=list)
    comparison_amount: Decimal | None = None
    amount_within_tolerance: bool = False
    remaining_po_balance: Decimal | None = None
    duplicate_of: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    outcome: Literal["AUTO_APPROVE", "NEEDS_REVIEW", "REJECT"]
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    evidence: list[MatchEvidence] = Field(default_factory=list)


class RunRecord(BaseModel):
    run_id: str
    doc_id: str
    status: Literal["AWAITING_APPROVAL", "REJECTED_AT_GATE", "RUNNING", "DONE", "ERROR"]
    decision: Decision | None = None
    created_at: datetime
    updated_at: datetime
