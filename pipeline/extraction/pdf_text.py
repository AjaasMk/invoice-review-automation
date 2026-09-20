import pdfplumber

MIN_TEXT_LENGTH = 20


def has_text_layer(pdf_path: str) -> bool:
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if len(text.strip()) >= MIN_TEXT_LENGTH:
                return True
    return False


def extract_raw_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        pages_text = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages_text)
