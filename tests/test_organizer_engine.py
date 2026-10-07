"""
Tests for MailPilot organizer engine.
"""

from typing import Sequence

from app.classifier.base import EmailClassifier, ClassifierError
from app.mail.models import (
    ClassificationConfidence,
    EmailMessage,
    OrganizationClassification,
    OrganizationMapping,
)
from app.mail.provider import MailProvider, MailProviderError
from app.organizer.engine import OrganizerEngine


class FakeMailProvider(MailProvider):
    """In-memory mail provider for safe organizer tests."""

    def __init__(
        self,
        folders: Sequence[str] | None = None,
    ) -> None:
        self.folders = list(folders or [])
        self.created_folders: list[str] = []
        self.moved_messages: list[tuple[str, str]] = []

    def list_inbox_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        return []

    def list_folders(self) -> Sequence[str]:
        return list(self.folders)

    def create_folder(self, folder_name: str) -> str:
        self.created_folders.append(folder_name)
        self.folders.append(folder_name)
        return folder_name

    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        self.moved_messages.append(
            (message_id, destination_folder)
        )


class FailingMoveProvider(FakeMailProvider):
    """Provider that fails when moving a message."""

    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        raise MailProviderError("Simulated move failure")


class FakeClassifier(EmailClassifier):
    """Classifier returning a predetermined result."""

    def __init__(
        self,
        classification: OrganizationClassification,
    ) -> None:
        self.classification = classification

    def classify(
        self,
        message: EmailMessage,
        existing_mappings: Sequence[OrganizationMapping] | None = None,
        existing_folders: Sequence[str] | None = None,
    ) -> OrganizationClassification:
        return self.classification


class FailingClassifier(EmailClassifier):
    """Classifier that simulates an AI/classification failure."""

    def classify(
        self,
        message: EmailMessage,
        existing_mappings: Sequence[OrganizationMapping] | None = None,
        existing_folders: Sequence[str] | None = None,
    ) -> OrganizationClassification:
        raise ClassifierError("Simulated classifier failure")


def make_message() -> EmailMessage:
    """Create a standard test message."""
    return EmailMessage(
        id="message-001",
        sender_email="user@example.com",
        sender_name="Example User",
        subject="Test message",
        body_preview="Test email body.",
    )


def make_classification(
    confidence: ClassificationConfidence,
    folder_name: str | None = "Example",
) -> OrganizationClassification:
    """Create a standard classification result."""
    return OrganizationClassification(
        organization="Example",
        domain="example.com",
        folder_name=folder_name,
        confidence=confidence,
        reason="Test classification.",
    )


def test_low_confidence_is_left_untouched() -> None:
    provider = FakeMailProvider()
    classifier = FakeClassifier(
        make_classification(ClassificationConfidence.LOW)
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "left_untouched"
    assert result.decision.should_move is False
    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_medium_confidence_is_left_untouched() -> None:
    provider = FakeMailProvider()
    classifier = FakeClassifier(
        make_classification(ClassificationConfidence.MEDIUM)
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "left_untouched"
    assert result.decision.should_move is False
    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_high_confidence_with_existing_folder_moves_message() -> None:
    provider = FakeMailProvider(["Example"])
    classifier = FakeClassifier(
        make_classification(ClassificationConfidence.HIGH)
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "moved"
    assert result.decision.should_move is True
    assert result.decision.should_create_folder is False
    assert provider.created_folders == []
    assert provider.moved_messages == [
        ("message-001", "Example")
    ]


def test_high_confidence_creates_missing_folder_then_moves() -> None:
    provider = FakeMailProvider()
    classifier = FakeClassifier(
        make_classification(ClassificationConfidence.HIGH)
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "moved"
    assert result.decision.should_move is True
    assert result.decision.should_create_folder is True
    assert provider.created_folders == ["Example"]
    assert provider.moved_messages == [
        ("message-001", "Example")
    ]


def test_dry_run_never_creates_or_moves() -> None:
    provider = FakeMailProvider()
    classifier = FakeClassifier(
        make_classification(ClassificationConfidence.HIGH)
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=True,
    )

    assert result.action == "dry_run_would_move"
    assert result.decision.should_move is True
    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_high_confidence_without_folder_is_left_untouched() -> None:
    provider = FakeMailProvider()
    classifier = FakeClassifier(
        make_classification(
            ClassificationConfidence.HIGH,
            folder_name=None,
        )
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "left_untouched"
    assert result.decision.should_move is False
    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_classifier_failure_is_left_untouched() -> None:
    provider = FakeMailProvider()
    classifier = FailingClassifier()
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "left_untouched"
    assert result.decision.should_move is False
    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_move_failure_does_not_report_success() -> None:
    provider = FailingMoveProvider()
    classifier = FakeClassifier(
        make_classification(ClassificationConfidence.HIGH)
    )
    engine = OrganizerEngine(provider, classifier)

    result = engine.organize_message(
        make_message(),
        dry_run=False,
    )

    assert result.action == "move_failed"
    assert result.decision.should_move is False
    assert "Move failed" in result.decision.reason