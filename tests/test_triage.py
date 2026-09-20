from pathlib import Path

from reportlab.pdfgen import canvas

from pipeline.triage import run_triage


class FakeTriageClient:
    def __init__(self, response: dict) -> None:
        self._response = response
        self.text_calls: list[str] = []
        self.image_calls: list[tuple[bytes, str]] = []

    def triage_text(self, text: str) -> dict:
        self.text_calls.append(text)
        return self._response

    def triage_image(self, image_bytes: bytes, media_type: str) -> dict:
        self.image_calls.append((image_bytes, media_type))
        return self._response


def test_run_triage_uses_text_path_for_text_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "clean.pdf"
    c = canvas.Canvas(str(pdf_path))
    c.drawString(72, 720, "Invoice INV-1 from Acme Corp, total 5000 USD")
    c.save()
    client = FakeTriageClient({"looks_like_invoice": True, "preview_text": "Invoice from Acme", "reason": "has invoice number and total"})
    result = run_triage(str(pdf_path), client)
    assert result.looks_like_invoice is True
    assert len(client.text_calls) == 1
    assert len(client.image_calls) == 0


def test_run_triage_uses_image_path_for_image_attachment(tmp_path: Path) -> None:
    from PIL import Image

    img_path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), color="white").save(img_path)
    client = FakeTriageClient({"looks_like_invoice": False, "preview_text": "blank image", "reason": "no readable content"})
    result = run_triage(str(img_path), client)
    assert result.looks_like_invoice is False
    assert len(client.image_calls) == 1
