from pathlib import Path

from pdfplumber import open as open_pdf

INVOICE_DIR = Path(__file__).resolve().parent.parent / "data" / "invoices"


def test_happy_path_pdf_contains_expected_fields() -> None:
    with open_pdf(INVOICE_DIR / "happy_path.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    assert "INV-3001" in text
    assert "PO-1001" in text
    assert "5000" in text


def test_edge_a_split_invoices_reference_same_po_with_partial_amounts() -> None:
    with open_pdf(INVOICE_DIR / "edge_a_split_1.pdf") as pdf:
        text_1 = pdf.pages[0].extract_text()
    with open_pdf(INVOICE_DIR / "edge_a_split_2.pdf") as pdf:
        text_2 = pdf.pages[0].extract_text()
    assert "PO-1002" in text_1 and "6000" in text_1
    assert "PO-1002" in text_2 and "4000" in text_2


def test_edge_b_tax_invoice_shows_subtotal_tax_and_total() -> None:
    with open_pdf(INVOICE_DIR / "edge_b_tax.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    assert "3000" in text
    assert "240" in text
    assert "3240" in text


def test_edge_c_duplicate_invoices_are_close_but_distinct() -> None:
    with open_pdf(INVOICE_DIR / "edge_c_dup_1.pdf") as pdf:
        text_1 = pdf.pages[0].extract_text()
    with open_pdf(INVOICE_DIR / "edge_c_dup_2.pdf") as pdf:
        text_2 = pdf.pages[0].extract_text()
    assert "INV-6001" in text_1
    assert "INV-6002" in text_2
    assert "1995" in text_2


def test_edge_d_degraded_scan_exists_as_image() -> None:
    from PIL import Image

    with Image.open(INVOICE_DIR / "edge_d_degraded.png") as img:
        assert img.format == "PNG"
        assert img.size[0] > 100 and img.size[1] > 100
