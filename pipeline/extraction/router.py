from pathlib import Path

import pymupdf

from pipeline.extraction.client import ExtractionClient
from pipeline.extraction.confidence import compute_confidence
from pipeline.extraction.pdf_text import extract_raw_text, has_text_layer

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def route_and_extract(file_path: str, client: ExtractionClient) -> tuple[dict, str, float]:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        image_bytes = path.read_bytes()
        raw = client.structure_from_image(image_bytes, IMAGE_MEDIA_TYPES[suffix])
        return raw, "vision", compute_confidence(raw)

    if suffix == ".pdf":
        if has_text_layer(file_path):
            text = extract_raw_text(file_path)
            raw = client.structure_from_text(text)
            return raw, "pdf_text", compute_confidence(raw)
        image_bytes = rasterize_first_page(file_path)
        raw = client.structure_from_image(image_bytes, "image/png")
        return raw, "vision", compute_confidence(raw)

    raise ValueError(f"unsupported attachment type: {suffix}")


def rasterize_first_page(pdf_path: str) -> bytes:
    document = pymupdf.open(pdf_path)
    try:
        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=200)
        return pixmap.tobytes("png")
    finally:
        document.close()
