from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = Path("output/test_invoices")
OUT.mkdir(parents=True, exist_ok=True)
styles = getSampleStyleSheet()

def make_pdf(path, vendor, invoice, po, total):
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=22*mm, leftMargin=22*mm, topMargin=20*mm, bottomMargin=20*mm)
    body = styles["BodyText"]
    title = styles["Title"]
    story = [Paragraph("INVOICE", title), Paragraph(f"<b>{vendor}</b><br/>Accounts Payable", body), Spacer(1, 13*mm)]
    meta = [["INVOICE NUMBER", "DATE", "PURCHASE ORDER"], [invoice, "2026-09-22", po]]
    table = Table(meta, colWidths=[55*mm, 40*mm, 55*mm])
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#EAEAEA")), ("BOX", (0,0), (-1,-1), .6, colors.grey), ("INNERGRID", (0,0), (-1,-1), .4, colors.lightgrey), ("TOPPADDING", (0,0), (-1,-1), 9), ("BOTTOMPADDING", (0,0), (-1,-1), 9)]))
    story += [table, Spacer(1, 14*mm)]
    lines = [["DESCRIPTION", "QTY", "AMOUNT"], ["Professional services and invoice processing", "1", total], ["", "TOTAL DUE", total]]
    items = Table(lines, colWidths=[110*mm, 28*mm, 34*mm])
    items.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.black), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("GRID", (0,0), (-1,-1), .4, colors.lightgrey), ("ALIGN", (1,0), (-1,-1), "RIGHT"), ("SPAN", (0,2), (0,2)), ("TOPPADDING", (0,0), (-1,-1), 10), ("BOTTOMPADDING", (0,0), (-1,-1), 10)]))
    story += [items, Spacer(1, 17*mm), Paragraph("Please reference the invoice number when remitting payment.", body)]
    doc.build(story)

make_pdf(OUT / "invoice_acme_inv_2048.pdf", "Acme Supplies", "INV-2048", "PO-83921", "$12,480.00")
make_pdf(OUT / "invoice_globex_inv_2049.pdf", "Globex Industries", "INV-2049", "PO-1002", "$3,210.00")

W, H = 1600, 2200
img = Image.new("RGB", (W, H), "#f7f7f7")
draw = ImageDraw.Draw(img)
regular = "C:/Windows/Fonts/arial.ttf"
bold = "C:/Windows/Fonts/arialbd.ttf"
def text(x, y, value, size=30, heavy=False, fill="#222222"):
    path = bold if heavy and Path(bold).exists() else regular
    draw.text((x, y), value, font=ImageFont.truetype(path, size), fill=fill)
text(125, 120, "INVOICE", 88, True)
text(125, 260, "Office Hub", 48, True)
text(125, 330, "Office supplies and workplace goods", 28, False, "#666666")
draw.line((125, 445, 1475, 445), fill="#777777", width=3)
for x, heading, value in [(125, "INVOICE NUMBER", "INV-2050"), (575, "DATE", "2026-09-22"), (1025, "PURCHASE ORDER", "PO-1003")]:
    text(x, 500, heading, 22, False, "#777777")
    text(x, 545, value, 34, True)
draw.rectangle((125, 700, 1475, 790), fill="#222222")
text(150, 725, "DESCRIPTION", 24, True, "white")
text(1135, 725, "AMOUNT", 24, True, "white")
text(150, 850, "Office chairs and desk accessories", 30)
text(1140, 850, "$980.00", 30, True)
draw.line((125, 950, 1475, 950), fill="#bbbbbb", width=2)
text(1010, 1030, "TOTAL DUE", 32, True)
text(1235, 1030, "$980.00", 36, True)
text(125, 1500, "Thank you.", 30, False, "#555555")
text(125, 1980, "Scanned document - Office Hub - INV-2050", 22, False, "#888888")
img.filter(ImageFilter.GaussianBlur(0.25)).save(OUT / "invoice_office_hub_inv_2050_scanned.png", optimize=True)
print("created", *(str(path) for path in sorted(OUT.iterdir())), sep="\n")
