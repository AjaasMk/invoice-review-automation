from rapidfuzz import fuzz

from pipeline.config import VENDOR_MATCH_THRESHOLD
from pipeline.schemas import VendorRecord


def resolve_vendor(vendor_name_raw: str, vendors: list[VendorRecord]) -> VendorRecord | None:
    normalized_input = _normalize_name(vendor_name_raw)
    best_vendor: VendorRecord | None = None
    best_score = 0.0
    for vendor in vendors:
        for candidate in [vendor.canonical_name, *vendor.aliases]:
            score = fuzz.token_sort_ratio(normalized_input, _normalize_name(candidate))
            if score > best_score:
                best_score = score
                best_vendor = vendor
    if best_score >= VENDOR_MATCH_THRESHOLD:
        return best_vendor
    return None


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().replace(",", "").replace(".", "").split())
