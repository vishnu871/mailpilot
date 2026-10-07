from __future__ import annotations

from app.classifier.ai import AIClassificationOrchestrator
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

    return database, resolver


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


def test_high_confidence_ai_result_is_saved(tmp_path):
    database, resolver = create_system(tmp_path)

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

    database.save_mapping(
        domain=result.classification.domain,
        organization=result.classification.organization,
        folder_name=result.classification.folder_name,
        confidence=result.classification.confidence,
        reason=result.classification.reason,
    )

    mapping = database.get_mapping("newcompany.com")

    assert mapping is not None
    assert mapping.organization == "New Company"
    assert mapping.folder_name == "New Company"
    assert mapping.confidence == "HIGH"


def test_saved_high_confidence_mapping_bypasses_ai(tmp_path):
    database, resolver = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response()
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    first_result = orchestrator.classify(
        create_message()
    )

    assert first_result.ai_used is True
    assert provider.classification_count == 1

    database.save_mapping(
        domain=first_result.classification.domain,
        organization=first_result.classification.organization,
        folder_name=first_result.classification.folder_name,
        confidence=first_result.classification.confidence,
        reason=first_result.classification.reason,
    )

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


def test_high_confidence_mapping_can_be_reused_for_subdomain(tmp_path):
    database, resolver = create_system(tmp_path)

    database.save_mapping(
        domain="newcompany.com",
        organization="New Company",
        folder_name="New Company",
        confidence="HIGH",
        reason="Previously confirmed organization.",
    )

    provider = MockAIProvider(
        default_response=create_response(
            organization="Wrong AI Organization",
            domain="mail.newcompany.com",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        create_message(
            sender_email="alerts@mail.newcompany.com"
        )
    )

    assert result.ai_used is False
    assert result.source == "parent_domain_mapping"
    assert result.classification.organization == "New Company"
    assert result.classification.folder_name == "New Company"
    assert provider.classification_count == 0


def test_medium_confidence_is_not_automatically_learned(tmp_path):
    database, resolver = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response(
            organization="Maybe Company",
            folder_name="Maybe Company",
            confidence="MEDIUM",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is True
    assert result.classification.confidence == ClassificationConfidence.MEDIUM

    mapping = database.get_mapping("newcompany.com")

    assert mapping is None


def test_low_confidence_is_not_automatically_learned(tmp_path):
    database, resolver = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response(
            organization=None,
            domain="newcompany.com",
            folder_name=None,
            confidence="LOW",
            reason="Insufficient evidence to identify the organization.",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is True
    assert result.classification.confidence == ClassificationConfidence.LOW

    mapping = database.get_mapping("newcompany.com")

    assert mapping is None


def test_existing_mapping_is_not_overwritten_by_ai(tmp_path):
    database, resolver = create_system(tmp_path)

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
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        create_message()
    )

    assert result.ai_used is False
    assert result.classification.organization == "Original Company"
    assert result.classification.folder_name == "Original Company"

    mapping = database.get_mapping("newcompany.com")

    assert mapping is not None
    assert mapping.organization == "Original Company"
    assert mapping.folder_name == "Original Company"

    assert provider.classification_count == 0