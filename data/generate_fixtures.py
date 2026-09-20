from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path(__file__).resolve().parent / "invoices"


def draw_invoice_pdf(
    path: Path,
    invoice_number: str,
    invoice_date: str,
    vendor_name: str,
    po_reference: str,
    line_items: list[tuple[str, str, str, str]],
    subtotal: str,
    tax_amount: str,
    total_amount: str,
) -> None:
    c = canvas.Canvas(str(path))
    y = 760
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, f"INVOICE {invoice_number}")
    c.setFont("Helvetica", 11)
    y -= 24
    c.drawString(72, y, f"Date: {invoice_date}")
    y -= 18
    c.drawString(72, y, f"Vendor: {vendor_name}")
    y -= 18
    c.drawString(72, y, f"PO Reference: {po_reference}")
    y -= 30
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(360, y, "Unit Price")
    c.drawString(460, y, "Line Total")
    y -= 16
    c.setFont("Helvetica", 11)
    for description, quantity, unit_price, line_total in line_items:
        c.drawString(72, y, description)
        c.drawString(300, y, quantity)
        c.drawString(360, y, unit_price)
        c.drawString(460, y, line_total)
        y -= 16
    y -= 20
    c.drawString(360, y, f"Subtotal: {subtotal}")
    y -= 16
    c.drawString(360, y, f"Tax: {tax_amount}")
    y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawString(360, y, f"Total: {total_amount}")
    c.save()


def make_degraded_scan(path: Path) -> None:
    image = Image.new("L", (900, 1200), color=235)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((60, 80), "INVOICE", fill=80, font=font)
    draw.text((60, 110), "Vendor: Acme Corp", fill=90, font=font)
    draw.text((60, 130), "Total: 1500.00 USD", fill=90, font=font)
    draw.text((60, 150), "Invoice #: (smudged)", fill=110, font=font)
    for offset in range(0, 1200, 7):
        draw.line([(0, offset), (900, offset)], fill=200, width=1)
    image = image.rotate(9, expand=True, fillcolor=235)
    image = image.filter(ImageFilter.GaussianBlur(radius=3.5))
    image.convert("RGB").save(path, format="PNG")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    draw_invoice_pdf(
        OUTPUT_DIR / "happy_path.pdf",
        invoice_number="INV-3001",
        invoice_date="2026-08-02",
        vendor_name="Acme Corp",
        po_reference="PO-1001",
        line_items=[("Consulting services", "1", "5000.00", "5000.00")],
        subtotal="5000.00",
        tax_amount="0.00",
        total_amount="5000.00",
    )

    draw_invoice_pdf(
        OUTPUT_DIR / "edge_a_split_1.pdf",
        invoice_number="INV-4001",
        invoice_date="2026-08-06",
        vendor_name="Globex Industries LLC",
        po_reference="PO-1002",
        line_items=[("Phase 1 delivery (60%)", "1", "6000.00", "6000.00")],
        subtotal="6000.00",
        tax_amount="0.00",
        total_amount="6000.00",
    )
    draw_invoice_pdf(
        OUTPUT_DIR / "edge_a_split_2.pdf",
        invoice_number="INV-4002",
        invoice_date="2026-08-20",
        vendor_name="Globex Industries LLC",
        po_reference="PO-1002",
        line_items=[("Phase 2 delivery (40%)", "1", "4000.00", "4000.00")],
        subtotal="4000.00",
        tax_amount="0.00",
        total_amount="4000.00",
    )

    draw_invoice_pdf(
        OUTPUT_DIR / "edge_b_tax.pdf",
        invoice_number="INV-5001",
        invoice_date="2026-08-11",
        vendor_name="Acme Corp",
        po_reference="PO-1003",
        line_items=[("Annual license", "1", "3000.00", "3000.00")],
        subtotal="3000.00",
        tax_amount="240.00",
        total_amount="3240.00",
    )

    draw_invoice_pdf(
        OUTPUT_DIR / "edge_c_dup_1.pdf",
        invoice_number="INV-6001",
        invoice_date="2026-08-13",
        vendor_name="Acme Corp",
        po_reference="PO-1004",
        line_items=[("Support retainer", "1", "2000.00", "2000.00")],
        subtotal="2000.00",
        tax_amount="0.00",
        total_amount="2000.00",
    )
    draw_invoice_pdf(
        OUTPUT_DIR / "edge_c_dup_2.pdf",
        invoice_number="INV-6002",
        invoice_date="2026-08-16",
        vendor_name="Acme Corp",
        po_reference="PO-1004",
        line_items=[("Support retainer", "1", "1995.00", "1995.00")],
        subtotal="1995.00",
        tax_amount="0.00",
        total_amount="1995.00",
    )

    make_degraded_scan(OUTPUT_DIR / "edge_d_degraded.png")


if __name__ == "__main__":
    main()
