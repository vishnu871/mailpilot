from __future__ import annotations

from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.learning import LearningManager
from app.classifier.providers.ai_provider import AIClassificationResponse
from app.classifier.providers.mock import MockAIProvider
from app.classifier.resolver import OrganizationResolver
from app.mail.models import ClassificationConfidence, EmailMessage
from database.manager import DatabaseManager


def create_message(
    message_id: str = "message-123",
    sender_email: str = "person@newcompany.com",
) -> EmailMessage:
    return EmailMessage(
        id=message_id,
        sender_email=sender_email,
        sender_name="Test Person",
        subject="Test email",
        body_preview="Test body",
        is_read=False,
        has_attachments=False,
    )


def create_system(tmp_path):
    database = DatabaseManager(
        database_path=str(tmp_path / "mailpilot.db"),
    )
    database.initialize()

    resolver = OrganizationResolver(database)
    learning_manager = LearningManager(database)

    return database, resolver, learning_manager


def create_response(
    organization: str | None = "New Company",
    domain: str | None = "newcompany.com",
    folder_name: str | None = "New Company",
    confidence: str = "HIGH",
    reason: str = "AI identified the organization from the sender information.",
) -> AIClassificationResponse:
    return AIClassificationResponse(
        organization=organization,
        domain=domain,
        folder_name=folder_name,
        confidence=confidence,
        reason=reason,
    )


def test_unknown_domain_is_automatically_learned(tmp_path):
    database, resolver, learning_manager = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response()
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is True
    assert result.classification.confidence == ClassificationConfidence.HIGH

    mapping = database.get_mapping(
        "newcompany.com"
    )

    assert mapping is not None
    assert mapping.organization == "New Company"
    assert mapping.folder_name == "New Company"
    assert mapping.confidence == "HIGH"


def test_learned_domain_bypasses_ai_on_next_email(tmp_path):
    database, resolver, learning_manager = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response()
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    first_result = orchestrator.classify(
        create_message()
    )

    assert first_result.ai_used is True
    assert provider.classification_count == 1

    second_result = orchestrator.classify(
        create_message(
            message_id="message-456",
            sender_email="anotherperson@newcompany.com",
        )
    )

    assert second_result.ai_used is False
    assert second_result.source == "exact_domain_mapping"
    assert second_result.classification.organization == "New Company"
    assert second_result.classification.folder_name == "New Company"

    assert provider.classification_count == 1


def test_learned_domain_mapping_applies_to_subdomain(tmp_path):
    database, resolver, learning_manager = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response()
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    first_result = orchestrator.classify(
        create_message()
    )

    assert first_result.ai_used is True

    second_result = orchestrator.classify(
        create_message(
            message_id="message-456",
            sender_email="alerts@mail.newcompany.com",
        )
    )

    assert second_result.ai_used is False
    assert second_result.source == "parent_domain_mapping"
    assert second_result.classification.organization == "New Company"
    assert second_result.classification.folder_name == "New Company"

    assert provider.classification_count == 1


def test_medium_confidence_is_not_automatically_learned(tmp_path):
    database, resolver, learning_manager = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response(
            organization="Maybe Company",
            folder_name="Maybe Company",
            confidence="MEDIUM",
            reason="AI has moderate confidence.",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is True
    assert result.classification.confidence == ClassificationConfidence.MEDIUM

    assert database.get_mapping(
        "newcompany.com"
    ) is None


def test_low_confidence_is_not_automatically_learned(tmp_path):
    database, resolver, learning_manager = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response(
            organization=None,
            domain="newcompany.com",
            folder_name=None,
            confidence="LOW",
            reason="Insufficient evidence.",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is True
    assert result.classification.confidence == ClassificationConfidence.LOW

    assert database.get_mapping(
        "newcompany.com"
    ) is None


def test_existing_mapping_is_preserved(tmp_path):
    database, resolver, learning_manager = create_system(tmp_path)

    database.save_mapping(
        domain="newcompany.com",
        organization="Original Company",
        folder_name="Original Company",
        confidence="HIGH",
        reason="Existing trusted mapping.",
    )

    provider = MockAIProvider(
        default_response=create_response(
            organization="Different Company",
            folder_name="Different Company",
            confidence="HIGH",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is False
    assert result.source == "exact_domain_mapping"
    assert result.classification.organization == "Original Company"

    mapping = database.get_mapping(
        "newcompany.com"
    )

    assert mapping is not None
    assert mapping.organization == "Original Company"
    assert mapping.folder_name == "Original Company"

    assert provider.classification_count == 0


def test_learning_is_optional_for_backward_compatibility(tmp_path):
    _, resolver, _ = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response()
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is True
    assert result.classification.confidence == ClassificationConfidence.HIGH