from email.message import EmailMessage
from pathlib import Path

from pipeline.intake.imap_source import ImapSource


def _build_raw_email_with_pdf_attachment() -> bytes:
    msg = EmailMessage()
    msg["From"] = "vendor@example.com"
    msg["Subject"] = "Invoice attached"
    msg.set_content("Please find the invoice attached.")
    msg.add_attachment(b"%PDF-1.4 fake content", maintype="application", subtype="pdf", filename="invoice.pdf")
    return bytes(msg)


class FakeImapClient:
    def __init__(self, raw_messages: dict[bytes, bytes]) -> None:
        self._raw_messages = raw_messages
        self.marked_seen: list[bytes] = []
        self.selected_mailbox: str | None = None

    def select(self, mailbox: str) -> None:
        self.selected_mailbox = mailbox

    def search_unseen(self) -> list[bytes]:
        return list(self._raw_messages.keys())

    def fetch_message(self, msg_id: bytes) -> bytes:
        return self._raw_messages[msg_id]

    def mark_seen(self, msg_id: bytes) -> None:
        self.marked_seen.append(msg_id)


def test_poll_extracts_pdf_attachment_and_marks_seen(tmp_path: Path) -> None:
    client = FakeImapClient({b"1": _build_raw_email_with_pdf_attachment()})
    source = ImapSource(client, str(tmp_path / "storage"))

    documents = source.poll()

    assert len(documents) == 1
    assert documents[0].source == "imap"
    assert documents[0].filename == "invoice.pdf"
    assert documents[0].sender == "vendor@example.com"
    assert Path(documents[0].content_path).read_bytes().startswith(b"%PDF")
    assert client.selected_mailbox == "INBOX"
    assert client.marked_seen == [b"1"]


def test_poll_ignores_messages_without_supported_attachments(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "spam@example.com"
    msg.set_content("no attachment here")
    client = FakeImapClient({b"2": bytes(msg)})
    source = ImapSource(client, str(tmp_path / "storage"))

    documents = source.poll()

    assert documents == []
    assert client.marked_seen == [b"2"]
