import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Iterator

from pipeline.schemas import Decision, Invoice, PurchaseOrder, VendorRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    approved INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    issued_date TEXT NOT NULL,
    tax_treatment TEXT NOT NULL,
    invoiced_to_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_stages (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS invoice_records (
    run_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT,
    invoice_date TEXT,
    total_amount TEXT,
    created_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def seed_reference_data(self, vendors: list[VendorRecord], purchase_orders: list[PurchaseOrder]) -> None:
        with self._connect() as conn:
            for vendor in vendors:
                conn.execute(
                    "INSERT OR REPLACE INTO vendors (vendor_id, canonical_name, aliases_json, approved) VALUES (?, ?, ?, ?)",
                    (vendor.vendor_id, vendor.canonical_name, json.dumps(vendor.aliases), int(vendor.approved)),
                )
            for po in purchase_orders:
                conn.execute(
                    "INSERT OR REPLACE INTO purchase_orders (po_id, vendor_id, amount, currency, issued_date, tax_treatment, invoiced_to_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (po.po_id, po.vendor_id, str(po.amount), po.currency, po.issued_date.isoformat(), po.tax_treatment, str(po.invoiced_to_date)),
                )

    def get_vendors(self) -> list[VendorRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM vendors").fetchall()
        return [
            VendorRecord(
                vendor_id=row["vendor_id"],
                canonical_name=row["canonical_name"],
                aliases=json.loads(row["aliases_json"]),
                approved=bool(row["approved"]),
            )
            for row in rows
        ]

    def get_purchase_orders(self) -> list[PurchaseOrder]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM purchase_orders").fetchall()
        return [
            PurchaseOrder(
                po_id=row["po_id"],
                vendor_id=row["vendor_id"],
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                issued_date=date.fromisoformat(row["issued_date"]),
                tax_treatment=row["tax_treatment"],
                invoiced_to_date=Decimal(row["invoiced_to_date"]),
            )
            for row in rows
        ]

    def update_po_balance(self, po_id: str, new_invoiced_to_date: Decimal) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE purchase_orders SET invoiced_to_date = ? WHERE po_id = ?", (str(new_invoiced_to_date), po_id))

    def create_run(self, run_id: str, doc_id: str, status: str) -> None:
        with self._connect() as conn:
            now = _now_iso()
            conn.execute(
                "INSERT INTO runs (run_id, doc_id, status, decision_json, created_at, updated_at) VALUES (?, ?, ?, NULL, ?, ?)",
                (run_id, doc_id, status, now, now),
            )

    def update_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?", (status, _now_iso(), run_id))

    def set_run_decision(self, run_id: str, decision: Decision) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET decision_json = ?, updated_at = ? WHERE run_id = ?",
                (decision.model_dump_json(), _now_iso(), run_id),
            )

    def append_stage(self, run_id: str, stage: str, payload: dict) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM run_stages WHERE run_id = ?", (run_id,)).fetchone()
            seq = row["next_seq"]
            conn.execute(
                "INSERT INTO run_stages (run_id, seq, stage, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, stage, json.dumps(payload, default=str), _now_iso()),
            )
        return seq

    def get_stages(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM run_stages WHERE run_id = ? ORDER BY seq ASC", (run_id,)).fetchall()
        return [
            {"seq": row["seq"], "stage": row["stage"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def list_runs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [self._run_row_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_row_to_dict(row) if row is not None else None

    def _run_row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "run_id": row["run_id"],
            "doc_id": row["doc_id"],
            "status": row["status"],
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_invoice_record(self, run_id: str, vendor_id: str, invoice: Invoice) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO invoice_records (run_id, vendor_id, invoice_number, invoice_date, total_amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    vendor_id,
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                    str(invoice.total_amount) if invoice.total_amount is not None else None,
                    _now_iso(),
                ),
            )

    def get_prior_invoices(self, vendor_id: str, exclude_run_id: str) -> list[Invoice]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoice_records WHERE vendor_id = ? AND run_id != ?",
                (vendor_id, exclude_run_id),
            ).fetchall()
        return [
            Invoice(
                invoice_number=row["invoice_number"],
                invoice_date=date.fromisoformat(row["invoice_date"]) if row["invoice_date"] else None,
                vendor_name_raw="",
                total_amount=Decimal(row["total_amount"]) if row["total_amount"] is not None else None,
                extraction_path="pdf_text",
                extraction_confidence=1.0,
            )
            for row in rows
        ]


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
