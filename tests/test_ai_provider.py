from __future__ import annotations

import pytest

from app.classifier.providers.ai_provider import (
    AIClassificationRequest,
    AIClassificationResponse,
    AIProvider,
    AIProviderConfigurationError,
    AIProviderResponseError,
)
from app.mail.models import EmailMessage


def create_message() -> EmailMessage:
    """Create a representative email for provider tests."""
    return EmailMessage(
        id="message-123",
        sender_email="person@example.com",
        sender_name="Example User",
        subject="Project update",
        body_preview="Here is the latest project update.",
        is_read=False,
        has_attachments=False,
    )


def test_classification_request_defaults_are_empty():
    request = AIClassificationRequest(
        message=create_message()
    )

    assert request.message.id == "message-123"
    assert request.existing_folders == ()
    assert request.known_domain_mapping is None


def test_classification_request_accepts_existing_folders():
    request = AIClassificationRequest(
        message=create_message(),
        existing_folders=(
            "Example Corporation",
            "Finance",
            "Personal",
        ),
    )

    assert request.existing_folders == (
        "Example Corporation",
        "Finance",
        "Personal",
    )


def test_classification_request_accepts_known_mapping():
    request = AIClassificationRequest(
        message=create_message(),
        known_domain_mapping="Example Corporation",
    )

    assert request.known_domain_mapping == "Example Corporation"


def test_classification_response_can_represent_successful_classification():
    response = AIClassificationResponse(
        organization="Example Corporation",
        domain="example.com",
        folder_name="Example Corporation",
        confidence="HIGH",
        reason="Sender domain matches the organization.",
    )

    assert response.organization == "Example Corporation"
    assert response.domain == "example.com"
    assert response.folder_name == "Example Corporation"
    assert response.confidence == "HIGH"
    assert response.reason == (
        "Sender domain matches the organization."
    )


def test_classification_response_can_represent_uncertain_result():
    response = AIClassificationResponse(
        organization=None,
        domain="example.com",
        folder_name=None,
        confidence="LOW",
        reason="Insufficient evidence.",
    )

    assert response.organization is None
    assert response.domain == "example.com"
    assert response.folder_name is None
    assert response.confidence == "LOW"


def test_ai_provider_is_abstract():
    with pytest.raises(TypeError):
        AIProvider()


def test_ai_provider_configuration_error_is_provider_error():
    error = AIProviderConfigurationError(
        "Provider is not configured."
    )

    assert isinstance(error, Exception)
    assert str(error) == "Provider is not configured."


def test_ai_provider_response_error_is_provider_error():
    error = AIProviderResponseError(
        "Provider returned invalid data."
    )

    assert isinstance(error, Exception)
    assert str(error) == "Provider returned invalid data."


def test_custom_provider_can_implement_contract():
    class TestProvider(AIProvider):
        def classify(
            self,
            request: AIClassificationRequest,
        ) -> AIClassificationResponse:
            return AIClassificationResponse(
                organization="Example Corporation",
                domain="example.com",
                folder_name="Example Corporation",
                confidence="HIGH",
                reason="Test provider classification.",
            )

    provider = TestProvider()

    request = AIClassificationRequest(
        message=create_message()
    )

    response = provider.classify(request)

    assert response.organization == "Example Corporation"
    assert response.domain == "example.com"
    assert response.folder_name == "Example Corporation"
    assert response.confidence == "HIGH"