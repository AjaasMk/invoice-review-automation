import json
from pathlib import Path

from pipeline.schemas import PurchaseOrder, VendorRecord

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def test_vendors_load_and_parse() -> None:
    raw = json.loads((DATA_DIR / "vendors.json").read_text())
    vendors = [VendorRecord.model_validate(v) for v in raw]
    assert len(vendors) == 3
    by_id = {v.vendor_id: v for v in vendors}
    assert by_id["V-ACME"].approved is True
    assert by_id["V-INITECH"].approved is False


def test_purchase_orders_load_and_parse() -> None:
    raw = json.loads((DATA_DIR / "purchase_orders.json").read_text())
    pos = [PurchaseOrder.model_validate(p) for p in raw]
    assert len(pos) == 5
    by_id = {p.po_id: p for p in pos}
    assert by_id["PO-1002"].vendor_id == "V-GLOBEX"
    assert by_id["PO-1001"].tax_treatment == "exclusive"
