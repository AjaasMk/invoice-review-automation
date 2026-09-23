"""Explainable invoice-intake classification for email messages.

The classifier is deliberately deterministic. It is a safety gate before OCR/LLM
extraction: uncertain messages are routed to review rather than silently lost.
"""
from dataclasses import dataclass
import re
from typing import Iterable

INVOICE_WORDS = ("invoice", "bill", "payment", "tax invoice", "remittance")
NOISE_WORDS = ("newsletter", "unsubscribe", "promotion", "sale", "advertisement")
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


@dataclass(frozen=True)
class TriageResult:
    score: int
    decision: str  # process, review, ignore
    reasons: tuple[str, ...]


def classify_email(
    *,
    subject: str | None,
    body: str | None,
    sender: str | None,
    attachments: Iterable[str],
    known_vendor: bool = False,
) -> TriageResult:
    """Score an email using transparent signals and return an action."""
    subject_text = (subject or "").lower()
    body_text = (body or "").lower()
    filenames = [str(name).lower() for name in attachments]
    score = 0
    reasons: list[str] = []

    supported = [name for name in filenames if any(name.endswith(ext) for ext in SUPPORTED_EXTENSIONS)]
    if supported:
        score += 35
        reasons.append("supported invoice attachment")
    else:
        score -= 40
        reasons.append("no supported attachment")

    if any(word in subject_text for word in INVOICE_WORDS):
        score += 25
        reasons.append("invoice keyword in subject")
    if any(word in body_text for word in INVOICE_WORDS):
        score += 15
        reasons.append("invoice language in message")
    if any(re.search(rf"(?:^|[\W_]){re.escape(word)}(?:$|[\W_])", name) for name in filenames for word in INVOICE_WORDS):
        score += 10
        reasons.append("invoice-like filename")
    if known_vendor:
        score += 15
        reasons.append("sender matches approved vendor")
    if any(word in subject_text or word in body_text for word in NOISE_WORDS):
        score -= 30
        reasons.append("promotional/noise language")

    score = max(0, min(100, score))
    if not supported or ("promotional/noise language" in reasons and score < 40):
        decision = "ignore"
    elif score >= 70:
        decision = "process"
    else:
        # A supported scan with weak email metadata is uncertain, not safe to
        # discard. Keep it visible for a person to classify.
        decision = "review"
    return TriageResult(score=score, decision=decision, reasons=tuple(reasons))
