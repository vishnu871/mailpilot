from __future__ import annotations

from datetime import datetime, timezone

from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.learning import LearningManager
from app.classifier.providers.mock import MockAIProvider
from app.classifier.providers.ai_provider import AIClassificationResponse
from app.classifier.resolver import OrganizationResolver
from app.mail.models import EmailMessage
from app.organizer.engine import OrganizerEngine
from app.organizer.processor import OrganizerProcessor
from database.manager import DatabaseManager


class MockMailProvider:
    def __init__(self, messages, folders=None):
        self.messages = list(messages)
        self.folders = list(folders or [])
        self.created_folders = []
        self.moved_messages = []

    def list_inbox_messages(self, limit=50):
        return self.messages[:limit]

    def list_folders(self):
        return list(self.folders)

    def create_folder(self, folder_name):
        self.created_folders.append(folder_name)
        self.folders.append(folder_name)
        return folder_name

    def move_message(self, message_id, destination_folder):
        self.moved_messages.append(
            (message_id, destination_folder)
        )


def create_message(
    message_id="message-123",
    sender_email="person@newcompany.com",
    subject="Test email",
):
    return EmailMessage(
        id=message_id,
        sender_email=sender_email,
        sender_name="Test Person",
        subject=subject,
        body_preview="Test body",
        received_at=datetime.now(timezone.utc),
        is_read=False,
        has_attachments=False,
    )


def create_system(tmp_path, messages, folders=None, ai_response=None):
    database = DatabaseManager(
        database_path=str(tmp_path / "mailpilot.db"),
    )
    database.initialize()

    resolver = OrganizationResolver(database)
    learning_manager = LearningManager(database)

    provider = MockMailProvider(
        messages=messages,
        folders=folders,
    )

    ai_provider = MockAIProvider(
        default_response=ai_response
    )

    classifier = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=ai_provider,
        learning_manager=learning_manager,
    )

    engine = OrganizerEngine(
        provider=provider,
        classifier=classifier,
    )

    processor = OrganizerProcessor(
        provider=provider,
        engine=engine,
    )

    return (
        database,
        provider,
        ai_provider,
        processor,
    )


def create_high_response():
    return AIClassificationResponse(
        organization="New Company",
        domain="newcompany.com",
        folder_name="New Company",
        confidence="HIGH",
        reason="AI identified the organization from the sender.",
    )


def create_medium_response():
    return AIClassificationResponse(
        organization="Maybe Company",
        domain="newcompany.com",
        folder_name="Maybe Company",
        confidence="MEDIUM",
        reason="AI has moderate confidence.",
    )


def test_dry_run_processes_inbox_batch_without_moving_messages(tmp_path):
    messages = [
        create_message(
            message_id="message-1"
        )
    ]

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=messages,
        ai_response=create_high_response(),
    )

    results = processor.process(
        dry_run=True
    )

    assert len(results) == 1

    result = results[0]

    assert result.action == "dry_run_would_move"
    assert result.decision.should_move is True

    assert provider.moved_messages == []
    assert provider.created_folders == []

    assert ai_provider.classification_count == 1


def test_dry_run_does_not_create_missing_folder(tmp_path):
    messages = [
        create_message()
    ]

    (
        database,
        provider,
        _,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=messages,
        folders=[],
        ai_response=create_high_response(),
    )

    results = processor.process(
        dry_run=True
    )

    assert len(results) == 1
    assert results[0].action == "dry_run_would_move"

    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_existing_folder_is_not_modified_in_dry_run(tmp_path):
    messages = [
        create_message()
    ]

    (
        database,
        provider,
        _,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=messages,
        folders=["New Company"],
        ai_response=create_high_response(),
    )

    results = processor.process(
        dry_run=True
    )

    assert len(results) == 1
    assert results[0].action == "dry_run_would_move"

    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_medium_confidence_is_left_untouched(tmp_path):
    messages = [
        create_message()
    ]

    (
        database,
        provider,
        _,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=messages,
        ai_response=create_medium_response(),
    )

    results = processor.process(
        dry_run=True
    )

    assert len(results) == 1

    result = results[0]

    assert result.action == "left_untouched"
    assert result.decision.should_move is False

    assert provider.created_folders == []
    assert provider.moved_messages == []


def test_multiple_messages_are_processed_as_batch(tmp_path):
    messages = [
        create_message(
            message_id="message-1",
            sender_email="person1@company-one.com",
        ),
        create_message(
            message_id="message-2",
            sender_email="person2@company-two.com",
        ),
        create_message(
            message_id="message-3",
            sender_email="person3@company-three.com",
        ),
    ]

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=messages,
        ai_response=AIClassificationResponse(
            organization="Detected Company",
            domain="company-one.com",
            folder_name="Detected Company",
            confidence="HIGH",
            reason="AI identified the organization.",
        ),
    )

    results = processor.process(
        dry_run=True,
        limit=2,
    )

    assert len(results) == 2
    assert ai_provider.classification_count == 2

    assert provider.moved_messages == []
    assert provider.created_folders == []


def test_processor_uses_provider_folder_state(tmp_path):
    messages = [
        create_message()
    ]

    (
        database,
        provider,
        _,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=messages,
        folders=["Existing Folder"],
        ai_response=create_high_response(),
    )

    results = processor.process(
        dry_run=True
    )

    assert len(results) == 1

    assert provider.folders == ["Existing Folder"]


def test_empty_inbox_returns_no_results(tmp_path):
    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        messages=[],
        ai_response=create_high_response(),
    )

    results = processor.process(
        dry_run=True
    )

    assert results == []
    assert ai_provider.classification_count == 0
    assert provider.moved_messages == []
    assert provider.created_folders == []