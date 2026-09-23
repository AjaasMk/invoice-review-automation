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
        page_images = rasterize_pdf_pages(file_path)
        structure_from_images = getattr(client, "structure_from_images", None)
        if callable(structure_from_images):
            raw = structure_from_images([(image_bytes, "image/png") for image_bytes in page_images])
        else:
            raw = client.structure_from_image(page_images[0], "image/png")
        return raw, "vision", compute_confidence(raw)

    raise ValueError(f"unsupported attachment type: {suffix}")


def rasterize_first_page(pdf_path: str) -> bytes:
    return rasterize_pdf_pages(pdf_path, max_pages=1)[0]


def rasterize_pdf_pages(pdf_path: str, max_pages: int | None = None) -> list[bytes]:
    document = pymupdf.open(pdf_path)
    try:
        page_count = len(document) if max_pages is None else min(len(document), max_pages)
        if page_count == 0:
            raise ValueError("PDF has no pages")
        return [document.load_page(index).get_pixmap(dpi=200).tobytes("png") for index in range(page_count)]
    finally:
        document.close()
