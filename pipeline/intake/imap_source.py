import email
import hashlib
import imaplib
import ssl
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pipeline.schemas import IncomingDocument
from pipeline.intake.email_triage import classify_email

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _imap_quoted(value: str) -> str:
    """Quote one IMAP string argument, escaping embedded quotes/backslashes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ImapClient(Protocol):
    def select(self, mailbox: str) -> None: ...
    def search_unseen(self) -> list[bytes]: ...
    def search_all(self) -> list[bytes]: ...
    def fetch_message(self, msg_id: bytes) -> bytes: ...
    def mark_seen(self, msg_id: bytes) -> None: ...


class RealImapClient:
    def __init__(self, host: str, username: str, app_password: str) -> None:
        self._host = host
        self._username = username
        self._app_password = app_password
        self._connect()

    def _connect(self) -> None:
        self._conn = imaplib.IMAP4_SSL(self._host)
        status, _ = self._conn.login(self._username, self._app_password)
        if status != "OK":
            raise RuntimeError(f"IMAP login failed for {self._username}@{self._host}: {status}")

    def _with_reconnect(self, operation):
        try:
            return operation()
        except (imaplib.IMAP4.abort, OSError, ssl.SSLError):
            try:
                self._conn.shutdown()
            except Exception:
                pass
            self._connect()
            return operation()

    def select(self, mailbox: str) -> None:
        # imaplib passes mailbox arguments through verbatim. Quote a Gmail label
        # containing spaces so `Invoice Review Queue` is one IMAP mailbox name.
        mailbox_argument = mailbox
        if any(character.isspace() for character in mailbox) and not (mailbox.startswith('"') and mailbox.endswith('"')):
            mailbox_argument = f'"{mailbox.replace("\\", "\\\\").replace(chr(34), r"\"")}"'
        status, _ = self._with_reconnect(lambda: self._conn.select(mailbox_argument))
        if status != "OK":
            raise RuntimeError(f"IMAP select failed for mailbox {mailbox}: {status}")

    def search_unseen(self) -> list[bytes]:
        # Gmail's raw search narrows intake to unread supported documents.
        # Messages left in the triage label remain unread but are not re-run.
        query = 'is:unread has:attachment {filename:pdf filename:png filename:jpg filename:jpeg} -label:"Invoice Needs Review" -label:"Invoice Ignored"'
        status, data = self._with_reconnect(lambda: self._conn.search(None, "X-GM-RAW", _imap_quoted(query)))
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        return data[0].split()

    def search_all(self) -> list[bytes]:
        status, data = self._with_reconnect(lambda: self._conn.search(None, "ALL"))
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        return data[0].split()

    def search_pending_review(self) -> list[bytes]:
        query = 'is:unread label:"Invoice Needs Review"'
        status, data = self._with_reconnect(lambda: self._conn.search(None, "X-GM-RAW", _imap_quoted(query)))
        if status != "OK":
            raise RuntimeError(f"IMAP review-queue search failed: {status}")
        return data[0].split()

    def fetch_message(self, msg_id: bytes) -> bytes:
        status, data = self._with_reconnect(lambda: self._conn.fetch(msg_id, "(RFC822)"))
        if status != "OK":
            raise RuntimeError(f"IMAP fetch failed for message {msg_id!r}: {status}")
        return data[0][1]

    def mark_seen(self, msg_id: bytes) -> None:
        self._with_reconnect(lambda: self._conn.store(msg_id, "+FLAGS", "\\Seen"))

    def add_label(self, msg_id: bytes, label: str) -> None:
        """Apply a Gmail label without requiring the Gmail API."""
        # Gmail returns NO when STORE targets a label that has not been used
        # yet, so create it idempotently first.
        def apply_label():
            self._conn.create(_imap_quoted(label))
            return self._conn.store(msg_id, "+X-GM-LABELS", f'("{label}")')

        status, _ = self._with_reconnect(apply_label)
        if status != "OK":
            raise RuntimeError(f"IMAP label failed for message {msg_id!r}: {status}")


class ImapSource:
    def __init__(self, client: ImapClient, storage_dir: str, mailbox: str = "INBOX", repository=None) -> None:
        self._client = client
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._mailbox = mailbox
        self._repository = repository
        self._status: dict = {
            "enabled": True,
            "unclassified_unread_attachments": 0,
            "awaiting_human_triage": 0,
            "last_poll_at": None,
            "last_error": None,
        }

    def status(self) -> dict:
        return dict(self._status)

    def poll(self) -> list[IncomingDocument]:
        try:
            self._client.select(self._mailbox)
            # An employee applying a dedicated label is the intake event. Scan
            # every message in that label, regardless of its read state.
            if self._mailbox.upper() == "INBOX":
                msg_ids = self._client.search_unseen()
            else:
                search_all = getattr(self._client, "search_all", None)
                msg_ids = search_all() if search_all is not None else self._client.search_unseen()
            self._status["unclassified_unread_attachments"] = len(msg_ids)
            documents: list[IncomingDocument] = []
            for msg_id in msg_ids:
                raw_bytes = self._client.fetch_message(msg_id)
                message = email.message_from_bytes(raw_bytes)
                sender = message.get("From")
                message_key = message.get("Message-ID") or f"{self._mailbox}:{msg_id.decode(errors='replace')}"
                subject = message.get("Subject")
                body_parts: list[str] = []
                attachment_names: list[str] = []
                for part in message.walk():
                    if part.get_content_type() == "text/plain" and not part.get_filename():
                        payload = part.get_payload(decode=True)
                        body_parts.append(payload.decode(errors="replace") if payload else "")
                    if part.get_filename():
                        attachment_names.append(part.get_filename())
                triage = (
                    classify_email(subject=subject, body="\n".join(body_parts), sender=sender, attachments=attachment_names)
                    if self._mailbox.upper() == "INBOX"
                    else None
                )
                if triage is not None:
                    label = {
                        "process": "Invoice Review Queue",
                        "review": "Invoice Needs Review",
                        "ignore": "Invoice Ignored",
                    }[triage.decision]
                    add_label = getattr(self._client, "add_label", None)
                    if add_label is not None:
                        add_label(msg_id, label)
                for part in message.walk():
                    filename = part.get_filename()
                    if not filename or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    if triage is not None and triage.decision == "ignore":
                        continue
                    content_sha256 = hashlib.sha256(payload).hexdigest()
                    if self._repository is not None and not self._repository.claim_email_attachment(message_key, content_sha256):
                        continue
                    documents.append(self._save_attachment(
                        sender, filename, payload,
                        triage.score if triage else 100,
                        triage.decision if triage else "process",
                        triage.reasons if triage else ("explicit review queue label",),
                    ))
                # Uncertain messages stay unread and visible in Gmail, but the
                # triage label excludes them from automatic reprocessing.
                if triage is None or triage.decision != "review":
                    self._client.mark_seen(msg_id)
                self._status["unclassified_unread_attachments"] = max(
                    0, self._status["unclassified_unread_attachments"] - 1
                )
            pending_search = getattr(self._client, "search_pending_review", None)
            if pending_search is not None:
                self._status["awaiting_human_triage"] = len(pending_search())
            else:
                self._status["awaiting_human_triage"] = sum(
                    document.intake_decision == "review" for document in documents
                )
            self._status["last_poll_at"] = datetime.now(timezone.utc).isoformat()
            self._status["last_error"] = None
            return documents
        except Exception as exc:
            self._status["last_error"] = str(exc)
            raise

    def _save_attachment(self, sender: str | None, filename: str, payload: bytes, score: int = 100, decision: str = "process", reasons: tuple[str, ...] = ()) -> IncomingDocument:
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
            content_sha256=hashlib.sha256(payload).hexdigest(),
            intake_score=score,
            intake_decision=decision,
            intake_reasons=list(reasons),
        )
