"""Create clean invoice PDFs whose vendors, POs, dates, and amounts match demo data."""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas


OUTPUT = Path("output/pdf")
SAMPLES = [
    {
        "filename": "invoice_acme_po_1001.pdf",
        "vendor": "Acme Corporation Pvt Ltd",
        "address": "221 Market Street\nNew York, NY 10001",
        "number": "ACME-2026-1001",
        "date": "2026-08-01",
        "due": "2026-08-31",
        "po": "PO-1001",
        "description": "Workflow automation implementation",
        "amount": 5000.00,
    },
    {
        "filename": "invoice_globex_po_1008.pdf",
        "vendor": "Globex Industries LLC",
        "address": "18 Industrial Avenue\nChicago, IL 60601",
        "number": "GLOBEX-2026-1008",
        "date": "2026-08-20",
        "due": "2026-09-19",
        "po": "PO-1008",
        "description": "Factory safety equipment",
        "amount": 4500.00,
    },
    {
        "filename": "invoice_northstar_po_1014.pdf",
        "vendor": "Northstar Office Supplies LLC",
        "address": "90 River Road\nBoston, MA 02110",
        "number": "NS-2026-1014",
        "date": "2026-09-02",
        "due": "2026-10-02",
        "po": "PO-1014",
        "description": "Office supplies and stationery",
        "amount": 950.00,
    },
]


def draw_right(canvas: Canvas, text: str, x: float, y: float, font: str = "Helvetica", size: int = 10) -> None:
    canvas.setFont(font, size)
    canvas.drawRightString(x, y, text)


def create_invoice(sample: dict) -> None:
    output = OUTPUT / sample["filename"]
    canvas = Canvas(str(output), pagesize=A4)
    width, height = A4
    left, right = 24 * mm, width - 24 * mm

    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.rect(0, height - 58 * mm, width, 58 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 29)
    canvas.drawString(left, height - 30 * mm, "INVOICE")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(left, height - 39 * mm, sample["vendor"])

    draw_right(canvas, f"Invoice no.  {sample['number']}", right, height - 28 * mm, "Helvetica-Bold", 10)
    draw_right(canvas, f"Issue date  {sample['date']}", right, height - 36 * mm)
    draw_right(canvas, f"Due date  {sample['due']}", right, height - 44 * mm)

    canvas.setFillColor(colors.HexColor("#172033"))
    y = height - 83 * mm
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(left, y, "BILLED BY")
    canvas.drawString(right - 58 * mm, y, "PURCHASE ORDER")
    canvas.setFont("Helvetica", 10)
    for index, line in enumerate(sample["address"].splitlines()):
        canvas.drawString(left, y - (8 + index * 6) * mm, line)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(right - 58 * mm, y - 8 * mm, sample["po"])

    table_y = height - 130 * mm
    canvas.setFillColor(colors.HexColor("#EDF0F5"))
    canvas.rect(left, table_y, right - left, 12 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(left + 5 * mm, table_y + 4.2 * mm, "DESCRIPTION")
    draw_right(canvas, "AMOUNT", right - 5 * mm, table_y + 4.2 * mm, "Helvetica-Bold", 9)
    canvas.setFont("Helvetica", 11)
    canvas.drawString(left + 5 * mm, table_y - 11 * mm, sample["description"])
    draw_right(canvas, f"USD {sample['amount']:,.2f}", right - 5 * mm, table_y - 11 * mm, "Helvetica", 11)
    canvas.setStrokeColor(colors.HexColor("#C9D0DA"))
    canvas.line(left, table_y - 17 * mm, right, table_y - 17 * mm)

    total_y = table_y - 43 * mm
    canvas.setFont("Helvetica", 10)
    draw_right(canvas, "Subtotal", right - 40 * mm, total_y)
    draw_right(canvas, f"USD {sample['amount']:,.2f}", right, total_y)
    canvas.setStrokeColor(colors.HexColor("#172033"))
    canvas.line(right - 70 * mm, total_y - 6 * mm, right, total_y - 6 * mm)
    canvas.setFont("Helvetica-Bold", 15)
    draw_right(canvas, "TOTAL", right - 40 * mm, total_y - 16 * mm)
    draw_right(canvas, f"USD {sample['amount']:,.2f}", right, total_y - 16 * mm, "Helvetica-Bold", 15)

    canvas.setFillColor(colors.HexColor("#5E6B7F"))
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(left, 24 * mm, f"Reference {sample['po']} with payment. Thank you for your business.")
    canvas.save()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for sample in SAMPLES:
        create_invoice(sample)
        print(OUTPUT / sample["filename"])


if __name__ == "__main__":
    main()
