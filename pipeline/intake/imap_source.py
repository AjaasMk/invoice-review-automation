import email
import imaplib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pipeline.schemas import IncomingDocument

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


class ImapClient(Protocol):
    def select(self, mailbox: str) -> None: ...
    def search_unseen(self) -> list[bytes]: ...
    def fetch_message(self, msg_id: bytes) -> bytes: ...
    def mark_seen(self, msg_id: bytes) -> None: ...


class RealImapClient:
    def __init__(self, host: str, username: str, app_password: str) -> None:
        self._conn = imaplib.IMAP4_SSL(host)
        status, _ = self._conn.login(username, app_password)
        if status != "OK":
            raise RuntimeError(f"IMAP login failed for {username}@{host}: {status}")

    def select(self, mailbox: str) -> None:
        status, _ = self._conn.select(mailbox)
        if status != "OK":
            raise RuntimeError(f"IMAP select failed for mailbox {mailbox}: {status}")

    def search_unseen(self) -> list[bytes]:
        status, data = self._conn.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        return data[0].split()

    def fetch_message(self, msg_id: bytes) -> bytes:
        status, data = self._conn.fetch(msg_id, "(RFC822)")
        if status != "OK":
            raise RuntimeError(f"IMAP fetch failed for message {msg_id!r}: {status}")
        return data[0][1]

    def mark_seen(self, msg_id: bytes) -> None:
        self._conn.store(msg_id, "+FLAGS", "\\Seen")


class ImapSource:
    def __init__(self, client: ImapClient, storage_dir: str, mailbox: str = "INBOX") -> None:
        self._client = client
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._mailbox = mailbox

    def poll(self) -> list[IncomingDocument]:
        self._client.select(self._mailbox)
        documents: list[IncomingDocument] = []
        for msg_id in self._client.search_unseen():
            raw_bytes = self._client.fetch_message(msg_id)
            message = email.message_from_bytes(raw_bytes)
            sender = message.get("From")
            for part in message.walk():
                filename = part.get_filename()
                if not filename or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                documents.append(self._save_attachment(sender, filename, payload))
            self._client.mark_seen(msg_id)
        return documents

    def _save_attachment(self, sender: str | None, filename: str, payload: bytes) -> IncomingDocument:
        doc_id = str(uuid.uuid4())
        suffix = Path(filename).suffix.lower()
        destination = self._storage_dir / f"{doc_id}{suffix}"
        destination.write_bytes(payload)
        return IncomingDocument(
            doc_id=doc_id,
            source="imap",
            received_at=datetime.now(timezone.utc),
            sender=sender,
            filename=filename,
            content_path=str(destination),
        )
