from pipeline.schemas import VendorRecord
from pipeline.vendor_resolve import resolve_vendor

VENDORS = [
    VendorRecord(vendor_id="V-ACME", canonical_name="Acme Corporation Pvt Ltd", aliases=["Acme Corp", "ACME CORP.", "Acme Corporation"], approved=True),
    VendorRecord(vendor_id="V-GLOBEX", canonical_name="Globex Industries LLC", aliases=["Globex Industries", "Globex"], approved=True),
]


def test_resolves_exact_canonical_name() -> None:
    result = resolve_vendor("Globex Industries LLC", VENDORS)
    assert result is not None
    assert result.vendor_id == "V-GLOBEX"


def test_resolves_alias_with_different_casing_and_punctuation() -> None:
    result = resolve_vendor("acme corp.", VENDORS)
    assert result is not None
    assert result.vendor_id == "V-ACME"


def test_resolves_close_fuzzy_variant() -> None:
    result = resolve_vendor("Acme Corporation Pvt. Ltd", VENDORS)
    assert result is not None
    assert result.vendor_id == "V-ACME"


def test_returns_none_for_unrelated_name() -> None:
    result = resolve_vendor("Totally Unrelated Vendor Co", VENDORS)
    assert result is None
