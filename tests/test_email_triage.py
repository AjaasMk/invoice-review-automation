from pipeline.intake.email_triage import classify_email


def test_high_confidence_invoice_is_processed() -> None:
    result = classify_email(
        subject="Invoice INV-2048",
        body="Please find the invoice attached for payment.",
        sender="billing@acme.example",
        attachments=["INV-2048.pdf"],
        known_vendor=True,
    )
    assert result.decision == "process"
    assert result.score >= 70
    assert "sender matches approved vendor" in result.reasons


def test_ambiguous_attachment_is_reviewed() -> None:
    result = classify_email(
        subject="Documents attached",
        body="Please review this invoice document.",
        sender="unknown@example",
        attachments=["scan.jpg"],
    )
    assert result.decision == "review"
    assert 40 <= result.score < 70


def test_scanned_invoice_with_generic_filename_is_not_dropped() -> None:
    result = classify_email(
        subject="Documents attached",
        body="Please review this document.",
        sender="unknown@example",
        attachments=["scan.jpg"],
    )
    assert result.decision == "review"


def test_noise_without_attachment_is_ignored() -> None:
    result = classify_email(
        subject="Weekly promotion",
        body="Unsubscribe from our newsletter and see our sale.",
        sender="marketing@example",
        attachments=[],
    )
    assert result.decision == "ignore"
    assert result.score < 40


def test_promotional_attachment_with_clear_noise_is_ignored() -> None:
    result = classify_email(
        subject="Weekly sale promotion",
        body="Unsubscribe from our newsletter and see our sale.",
        sender="marketing@example",
        attachments=["catalog.pdf"],
    )
    assert result.decision == "ignore"
