from pathlib import Path

from reportlab.pdfgen import canvas

from pipeline.extraction.router import route_and_extract


class FakeClient:
    def __init__(self) -> None:
        self.text_calls: list[str] = []
        self.image_calls: list[tuple[bytes, str]] = []

    def structure_from_text(self, text: str) -> dict:
        self.text_calls.append(text)
        return {"invoice_number": "INV-1", "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}

    def structure_from_image(self, image_bytes: bytes, media_type: str) -> dict:
        self.image_calls.append((image_bytes, media_type))
        return {"invoice_number": None, "invoice_date": None, "vendor_name": "Acme", "total_amount": None}


class MultiImageFakeClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.multi_image_calls: list[list[tuple[bytes, str]]] = []

    def structure_from_images(self, images: list[tuple[bytes, str]]) -> dict:
        self.multi_image_calls.append(images)
        return {"invoice_number": "INV-MULTI", "invoice_date": "2026-08-02", "vendor_name": "Acme", "total_amount": 5000}


def _make_text_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "Invoice INV-1 from Acme Corp, total 5000 USD")
    c.save()


def test_text_pdf_routes_to_pdf_text_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "clean.pdf"
    _make_text_pdf(pdf_path)
    client = FakeClient()
    raw, extraction_path, confidence = route_and_extract(str(pdf_path), client)
    assert extraction_path == "pdf_text"
    assert len(client.text_calls) == 1
    assert "Acme" in client.text_calls[0]
    assert confidence == 1.0


def test_image_attachment_routes_to_vision_path(tmp_path: Path) -> None:
    from PIL import Image

    img_path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), color="white").save(img_path)
    client = FakeClient()
    raw, extraction_path, confidence = route_and_extract(str(img_path), client)
    assert extraction_path == "vision"
    assert len(client.image_calls) == 1
    assert client.image_calls[0][1] == "image/png"


def test_scanned_pdf_with_no_text_layer_routes_to_vision_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.showPage()
    c.save()
    client = FakeClient()
    raw, extraction_path, confidence = route_and_extract(str(pdf_path), client)
    assert extraction_path == "vision"
    assert len(client.image_calls) == 1


def test_scanned_multipage_pdf_sends_every_page_to_vision_client(tmp_path: Path) -> None:
    pdf_path = tmp_path / "multipage-scan.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.showPage()
    c.showPage()
    c.save()
    client = MultiImageFakeClient()
    _, extraction_path, _ = route_and_extract(str(pdf_path), client)
    assert extraction_path == "vision"
    assert len(client.multi_image_calls) == 1
    assert len(client.multi_image_calls[0]) == 2
