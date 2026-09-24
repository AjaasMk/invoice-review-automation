"""Create deterministic invoices for the recorded demo's exception scenarios."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas


OUTPUT = Path("output/pdf")
INVOICES = [
    {
        "filename": "zenith_vendor_not_found.pdf",
        "vendor": "Zenith Logistics LLP",
        "number": "ZEN-2026-001",
        "date": "2026-09-18",
        "po": "PO-1021",
        "description": "Freight and warehouse handling",
        "amount": 1800.00,
    },
    {
        "filename": "globex_amount_over_tolerance.pdf",
        "vendor": "Globex Industries LLC",
        "number": "GLO-2026-OVER-1008",
        "date": "2026-09-12",
        "po": "PO-1008",
        "description": "Factory safety equipment - additional charge",
        "amount": 5600.00,
    },
]


def right(canvas: Canvas, text: str, x: float, y: float, size: int = 10, bold: bool = False) -> None:
    canvas.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    canvas.drawRightString(x, y, text)


def create_invoice(data: dict) -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    output = OUTPUT / data["filename"]
    canvas = Canvas(str(output), pagesize=A4)
    width, height = A4
    left, right_edge = 24 * mm, width - 24 * mm
    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.rect(0, height - 58 * mm, width, 58 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 28)
    canvas.drawString(left, height - 30 * mm, "INVOICE")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(left, height - 39 * mm, data["vendor"])
    right(canvas, f"Invoice no.  {data['number']}", right_edge, height - 28 * mm, bold=True)
    right(canvas, f"Issue date  {data['date']}", right_edge, height - 36 * mm)
    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(left, height - 82 * mm, "BILLED BY")
    canvas.drawString(right_edge - 58 * mm, height - 82 * mm, "PURCHASE ORDER")
    canvas.setFont("Helvetica", 10)
    canvas.drawString(left, height - 91 * mm, "Invoice processing demonstration")
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(right_edge - 58 * mm, height - 91 * mm, data["po"])
    y = height - 132 * mm
    canvas.setFillColor(colors.HexColor("#EDF0F5"))
    canvas.rect(left, y, right_edge - left, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#172033"))
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(left + 5 * mm, y + 4 * mm, "DESCRIPTION")
    right(canvas, "AMOUNT", right_edge - 5 * mm, y + 4 * mm, bold=True)
    canvas.setFont("Helvetica", 11)
    canvas.drawString(left + 5 * mm, y - 11 * mm, data["description"])
    right(canvas, f"USD {data['amount']:,.2f}", right_edge - 5 * mm, y - 11 * mm, 11)
    canvas.setStrokeColor(colors.HexColor("#C9D0DA"))
    canvas.line(left, y - 17 * mm, right_edge, y - 17 * mm)
    total_y = y - 43 * mm
    right(canvas, "Subtotal", right_edge - 40 * mm, total_y)
    right(canvas, f"USD {data['amount']:,.2f}", right_edge, total_y)
    canvas.line(right_edge - 70 * mm, total_y - 6 * mm, right_edge, total_y - 6 * mm)
    right(canvas, "TOTAL", right_edge - 40 * mm, total_y - 16 * mm, 15, True)
    right(canvas, f"USD {data['amount']:,.2f}", right_edge, total_y - 16 * mm, 15, True)
    canvas.setFillColor(colors.HexColor("#5E6B7F"))
    canvas.setFont("Helvetica", 8.5)
    canvas.drawString(left, 24 * mm, f"Please quote {data['po']} with payment. Thank you.")
    canvas.save()
    return output


if __name__ == "__main__":
    for invoice in INVOICES:
        print(create_invoice(invoice))
