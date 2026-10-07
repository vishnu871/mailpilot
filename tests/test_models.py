"""
Tests for MailPilot domain models.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.mail.models import (
    ClassificationConfidence,
    EmailMessage,
    OrganizationClassification,
    OrganizationDecision,
    OrganizationMapping,
)


def test_email_message_normalizes_sender_email() -> None:
    message = EmailMessage(
        id="message-1",
        sender_email="  User@Example.COM  ",
        sender_name="Example User",
        subject="Test email",
    )

    assert message.sender_email == "user@example.com"


def test_email_message_accepts_received_at() -> None:
    received_at = datetime(
        2026,
        9,
        15,
        10,
        30,
        tzinfo=timezone.utc,
    )

    message = EmailMessage(
        id="message-2",
        sender_email="user@example.com",
        received_at=received_at,
    )

    assert message.received_at == received_at


def test_classification_normalizes_domain() -> None:
    classification = OrganizationClassification(
        organization="Example",
        domain="@Example.COM",
        folder_name="  Example   ",
        confidence=ClassificationConfidence.HIGH,
        reason="Known organization domain.",
    )

    assert classification.domain == "example.com"
    assert classification.folder_name == "Example"


def test_low_confidence_is_supported() -> None:
    classification = OrganizationClassification(
        confidence=ClassificationConfidence.LOW,
        reason="Insufficient evidence.",
    )

    assert classification.confidence == ClassificationConfidence.LOW
    assert classification.folder_name is None


def test_organization_mapping_normalizes_domain() -> None:
    mapping = OrganizationMapping(
        domain="@Example.COM",
        organization="Example",
        folder_name="Example",
    )

    assert mapping.domain == "example.com"


def test_organization_decision_can_require_folder_creation() -> None:
    decision = OrganizationDecision(
        message_id="message-3",
        organization="Example",
        domain="example.com",
        destination_folder="Example",
        confidence=ClassificationConfidence.HIGH,
        should_create_folder=True,
        should_move=True,
        reason="High-confidence organization match.",
    )

    assert decision.should_create_folder is True
    assert decision.should_move is True


def test_email_message_requires_sender_email() -> None:
    with pytest.raises(ValidationError):
        EmailMessage(
            id="message-4",
            sender_email="",
        )


def test_empty_folder_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        OrganizationClassification(
            folder_name="   ",
        )