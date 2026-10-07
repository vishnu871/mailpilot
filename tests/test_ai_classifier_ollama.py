"""
Integration tests for the AI classification orchestrator
using the real Ollama AI provider.

These tests communicate with the local Ollama server.
"""

from __future__ import annotations

import pytest

from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.learning import LearningManager
from app.classifier.resolver import OrganizationResolver
from app.classifier.providers.ollama import OllamaAIProvider
from app.mail.models import EmailMessage
from database.manager import DatabaseManager


def make_message(
    *,
    message_id: str = "ollama-integration-test",
    sender_email: str = "no-reply@microsoft.com",
    sender_name: str = "Outlook Team",
    subject: str = "Welcome to your new Outlook.com account",
    body_preview: str = "Welcome to your new Outlook.com account.",
) -> EmailMessage:
    return EmailMessage(
        id=message_id,
        sender_email=sender_email,
        sender_name=sender_name,
        subject=subject,
        body_preview=body_preview,
    )


@pytest.fixture
def database(tmp_path) -> DatabaseManager:
    database = DatabaseManager(
        database_path=tmp_path / "mailpilot.db"
    )
    database.initialize()
    return database


def test_orchestrator_classifies_unknown_organization_with_ollama(
    database: DatabaseManager,
) -> None:
    resolver = OrganizationResolver(database)
    provider = OllamaAIProvider()

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        message=make_message(),
        existing_folders=(),
    )

    assert result.ai_used is True
    assert result.source == "ai_provider"

    assert result.classification.organization == "Microsoft"
    assert result.classification.domain == "microsoft.com"
    assert result.classification.folder_name == "Microsoft"
    assert result.classification.confidence.value == "high"


def test_orchestrator_uses_existing_mapping_without_ollama(
    database: DatabaseManager,
) -> None:
    database.save_mapping(
        domain="microsoft.com",
        organization="Microsoft",
        folder_name="Microsoft",
        confidence="HIGH",
        reason="Known organization mapping.",
    )

    resolver = OrganizationResolver(database)
    provider = OllamaAIProvider()

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        message=make_message(
            message_id="known-microsoft-message",
        ),
        existing_folders=("Microsoft",),
    )

    assert result.ai_used is False
    assert result.source == "exact_domain_mapping"

    assert result.classification.organization == "Microsoft"
    assert result.classification.domain == "microsoft.com"
    assert result.classification.folder_name == "Microsoft"
    assert result.classification.confidence.value == "high"


def test_orchestrator_can_learn_high_confidence_ollama_result(
    database: DatabaseManager,
) -> None:
    resolver = OrganizationResolver(database)
    provider = OllamaAIProvider()
    learning_manager = LearningManager(database)

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
        learning_manager=learning_manager,
    )

    result = orchestrator.classify(
        message=make_message(
            message_id="learning-test-message",
        ),
        existing_folders=(),
    )

    assert result.ai_used is True

    assert result.classification.organization == "Microsoft"
    assert result.classification.domain == "microsoft.com"
    assert result.classification.folder_name == "Microsoft"

    if result.classification.confidence.value == "high":
        assert result.learned is True

        mapping = database.get_mapping(
            "microsoft.com"
        )

        assert mapping is not None
        assert mapping.organization == "Microsoft"
        assert mapping.folder_name == "Microsoft"


@pytest.mark.parametrize(
    "sender_email,sender_name,subject,expected_organization,expected_domain",
    [
        (
            "notifications@google.com",
            "Google",
            "Your Google account notification",
            "Google",
            "google.com",
        ),
        (
            "no-reply@github.com",
            "GitHub",
            "Welcome to GitHub",
            "GitHub",
            "github.com",
        ),
    ],
)
def test_ollama_classifies_additional_company_domains(
    database: DatabaseManager,
    sender_email: str,
    sender_name: str,
    subject: str,
    expected_organization: str,
    expected_domain: str,
) -> None:
    resolver = OrganizationResolver(database)
    provider = OllamaAIProvider()

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        message=make_message(
            message_id=f"test-{sender_email}",
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            body_preview=subject,
        ),
        existing_folders=(),
    )

    assert result.ai_used is True

    assert result.classification.organization == (
        expected_organization
    )

    assert result.classification.domain == (
        expected_domain
    )

    assert result.classification.folder_name == (
        expected_organization
    )

    assert result.classification.confidence.value in {
        "high",
        "medium",
    }