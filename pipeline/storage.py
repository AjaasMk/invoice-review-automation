import json
import sqlite3
from hashlib import sha256
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
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
    content_sha256 TEXT,
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
CREATE TABLE IF NOT EXISTS email_intake_records (
    message_key TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (message_key, content_sha256)
);
CREATE TABLE IF NOT EXISTS exception_resolutions (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    note TEXT,
    payload_json TEXT NOT NULL,
    resolved_at TEXT NOT NULL
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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "content_sha256" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN content_sha256 TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_content_sha256 ON runs(content_sha256)")
            self._backfill_content_hashes(conn)

    def seed_reference_data(self, vendors: list[VendorRecord], purchase_orders: list[PurchaseOrder]) -> None:
        with self._connect() as conn:
            for vendor in vendors:
                conn.execute(
                    "INSERT OR REPLACE INTO vendors (vendor_id, canonical_name, aliases_json, approved) VALUES (?, ?, ?, ?)",
                    (vendor.vendor_id, vendor.canonical_name, json.dumps(vendor.aliases), int(vendor.approved)),
                )
            for po in purchase_orders:
                conn.execute(
                    """
                    INSERT INTO purchase_orders (po_id, vendor_id, amount, currency, issued_date, tax_treatment, invoiced_to_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(po_id) DO UPDATE SET
                        vendor_id = excluded.vendor_id,
                        amount = excluded.amount,
                        currency = excluded.currency,
                        issued_date = excluded.issued_date,
                        tax_treatment = excluded.tax_treatment
                    """,
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

    def add_vendor(self, vendor: VendorRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO vendors (vendor_id, canonical_name, aliases_json, approved) VALUES (?, ?, ?, ?)",
                (vendor.vendor_id, vendor.canonical_name, json.dumps(vendor.aliases), int(vendor.approved)),
            )

    def add_purchase_order(self, purchase_order: PurchaseOrder) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO purchase_orders (po_id, vendor_id, amount, currency, issued_date, tax_treatment, invoiced_to_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    purchase_order.po_id,
                    purchase_order.vendor_id,
                    str(purchase_order.amount),
                    purchase_order.currency,
                    purchase_order.issued_date.isoformat(),
                    purchase_order.tax_treatment,
                    str(purchase_order.invoiced_to_date),
                ),
            )

    def update_po_balance_if_current(
        self,
        po_id: str,
        expected_invoiced_to_date: Decimal,
        new_invoiced_to_date: Decimal,
    ) -> bool:
        """Atomically update a PO only if another run has not changed its balance."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE purchase_orders SET invoiced_to_date = ? WHERE po_id = ? AND invoiced_to_date = ?",
                (str(new_invoiced_to_date), po_id, str(expected_invoiced_to_date)),
            )
            return cursor.rowcount == 1

    def create_run(self, run_id: str, doc_id: str, status: str, content_sha256: str | None = None) -> None:
        with self._connect() as conn:
            now = _now_iso()
            conn.execute(
                "INSERT INTO runs (run_id, doc_id, content_sha256, status, decision_json, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (run_id, doc_id, content_sha256, status, now, now),
            )

    def find_prior_run_by_content_hash(self, content_sha256: str | None, exclude_run_id: str) -> dict | None:
        """Find a completed earlier run for an identical attachment, if any."""
        if not content_sha256:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, status, decision_json, created_at
                FROM runs
                WHERE content_sha256 = ? AND run_id != ? AND decision_json IS NOT NULL
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (content_sha256, exclude_run_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "status": row["status"],
            "decision": json.loads(row["decision_json"]),
            "created_at": row["created_at"],
        }

    def reset_demo_state(self) -> dict[str, int]:
        """Clear processing history while retaining vendors and PO master data."""
        with self._connect() as conn:
            run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            invoice_count = conn.execute("SELECT COUNT(*) FROM invoice_records").fetchone()[0]
            conn.execute("DELETE FROM run_stages")
            conn.execute("DELETE FROM invoice_records")
            conn.execute("DELETE FROM email_intake_records")
            conn.execute("DELETE FROM exception_resolutions")
            conn.execute("DELETE FROM runs")
            conn.execute("UPDATE purchase_orders SET invoiced_to_date = '0'")
        return {"runs_cleared": run_count, "invoice_records_cleared": invoice_count}

    def claim_email_attachment(self, message_key: str, content_sha256: str) -> bool:
        """Record an email attachment once; False means it was already imported."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO email_intake_records (message_key, content_sha256, created_at) VALUES (?, ?, ?)",
                (message_key, content_sha256, _now_iso()),
            )
            return cursor.rowcount == 1

    def update_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?", (status, _now_iso(), run_id))

    def set_run_decision(self, run_id: str, decision: Decision) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET decision_json = ?, updated_at = ? WHERE run_id = ?",
                (decision.model_dump_json(), _now_iso(), run_id),
            )

    def set_exception_resolution(self, run_id: str, status: str, action: str, note: str | None, payload: dict) -> dict:
        """Persist the latest exception state; append_stage keeps the full action history."""
        record = {
            "status": status,
            "action": action,
            "note": note or None,
            "payload": payload,
            "resolved_at": _now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO exception_resolutions (run_id, status, action, note, payload_json, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status, action = excluded.action, note = excluded.note,
                    payload_json = excluded.payload_json, resolved_at = excluded.resolved_at
                """,
                (run_id, record["status"], record["action"], record["note"], json.dumps(record["payload"], default=str), record["resolved_at"]),
            )
        return record

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
            return [self._run_row_to_dict(row, conn) for row in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return self._run_row_to_dict(row, conn) if row is not None else None

    def _run_row_to_dict(self, row: sqlite3.Row, conn: sqlite3.Connection) -> dict:
        result = {
            "run_id": row["run_id"],
            "doc_id": row["doc_id"],
            "status": row["status"],
            "decision": json.loads(row["decision_json"]) if row["decision_json"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        resolution_row = conn.execute("SELECT * FROM exception_resolutions WHERE run_id = ?", (row["run_id"],)).fetchone()
        if resolution_row is not None:
            result["resolution"] = {
                "status": resolution_row["status"],
                "action": resolution_row["action"],
                "note": resolution_row["note"],
                "payload": json.loads(resolution_row["payload_json"]),
                "resolved_at": resolution_row["resolved_at"],
            }
        for stage_name in ("intake", "validate"):
            stage_row = conn.execute(
                "SELECT payload_json FROM run_stages WHERE run_id = ? AND stage = ? ORDER BY seq DESC LIMIT 1",
                (row["run_id"], stage_name),
            ).fetchone()
            if stage_row is None:
                continue
            payload = json.loads(stage_row["payload_json"])
            if stage_name == "intake":
                result["filename"] = payload.get("filename")
                result["source"] = payload.get("source")
            else:
                result["po_reference"] = payload.get("po_reference")
                result["invoice"] = {
                    "invoice_number": payload.get("invoice_number"),
                    "vendor_name": payload.get("vendor_name_raw"),
                    "total_amount": payload.get("total_amount"),
                    "currency": payload.get("currency"),
                }
        return result

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

    def _backfill_content_hashes(self, conn: sqlite3.Connection) -> None:
        """Add fingerprints to historical runs so duplicate checks survive an upgrade."""
        rows = conn.execute(
            """
            SELECT runs.run_id, run_stages.payload_json
            FROM runs
            JOIN run_stages ON run_stages.run_id = runs.run_id AND run_stages.stage = 'intake'
            WHERE runs.content_sha256 IS NULL
            """
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            fingerprint = payload.get("content_sha256")
            if not fingerprint:
                try:
                    fingerprint = sha256(Path(payload["content_path"]).read_bytes()).hexdigest()
                except (KeyError, OSError):
                    continue
            conn.execute("UPDATE runs SET content_sha256 = ? WHERE run_id = ?", (fingerprint, row["run_id"]))


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
