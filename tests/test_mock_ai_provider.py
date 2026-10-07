from __future__ import annotations

import pytest

from app.classifier.providers.ai_provider import (
    AIClassificationRequest,
    AIClassificationResponse,
)
from app.classifier.providers.mock import MockAIProvider
from app.mail.models import EmailMessage


def create_message(message_id: str = "message-123") -> EmailMessage:
    """Create a representative email for testing."""
    return EmailMessage(
        id=message_id,
        sender_email="person@example.com",
        sender_name="Example User",
        subject="Project update",
        body_preview="Here is the latest project update.",
        is_read=False,
        has_attachments=False,
    )


def create_request(
    message_id: str = "message-123",
) -> AIClassificationRequest:
    """Create a classification request."""
    return AIClassificationRequest(
        message=create_message(message_id)
    )


def create_response(
    organization: str = "Example Corporation",
    domain: str = "example.com",
    folder_name: str = "Example Corporation",
    confidence: str = "HIGH",
    reason: str = "Test classification.",
) -> AIClassificationResponse:
    """Create a classification response."""
    return AIClassificationResponse(
        organization=organization,
        domain=domain,
        folder_name=folder_name,
        confidence=confidence,
        reason=reason,
    )


def test_mock_provider_returns_default_response():
    response = create_response()

    provider = MockAIProvider(
        default_response=response
    )

    result = provider.classify(
        create_request()
    )

    assert result == response


def test_mock_provider_returns_message_specific_response():
    default_response = create_response(
        organization="Default Organization"
    )

    specific_response = create_response(
        organization="Specific Organization"
    )

    provider = MockAIProvider(
        default_response=default_response,
        responses_by_message_id={
            "message-123": specific_response,
        },
    )

    result = provider.classify(
        create_request("message-123")
    )

    assert result == specific_response


def test_message_specific_response_takes_priority():
    default_response = create_response(
        organization="Default Organization"
    )

    specific_response = create_response(
        organization="Specific Organization"
    )

    provider = MockAIProvider(
        default_response=default_response,
        responses_by_message_id={
            "message-123": specific_response,
        },
    )

    result = provider.classify(
        create_request("message-123")
    )

    assert result.organization == "Specific Organization"


def test_default_response_is_used_for_unknown_message():
    default_response = create_response(
        organization="Default Organization"
    )

    provider = MockAIProvider(
        default_response=default_response,
        responses_by_message_id={
            "other-message": create_response(
                organization="Other Organization"
            ),
        },
    )

    result = provider.classify(
        create_request("message-123")
    )

    assert result == default_response


def test_provider_raises_when_no_response_is_configured():
    provider = MockAIProvider()

    with pytest.raises(
        ValueError,
        match="no classification configured",
    ):
        provider.classify(
            create_request("message-123")
        )


def test_classification_count_starts_at_zero():
    provider = MockAIProvider(
        default_response=create_response()
    )

    assert provider.classification_count == 0


def test_classification_count_increments_for_each_request():
    provider = MockAIProvider(
        default_response=create_response()
    )

    provider.classify(create_request("message-1"))
    provider.classify(create_request("message-2"))
    provider.classify(create_request("message-3"))

    assert provider.classification_count == 3


def test_high_confidence_response_is_returned():
    response = create_response(
        confidence="HIGH"
    )

    provider = MockAIProvider(
        default_response=response
    )

    result = provider.classify(
        create_request()
    )

    assert result.confidence == "HIGH"


def test_medium_confidence_response_is_returned():
    response = create_response(
        confidence="MEDIUM"
    )

    provider = MockAIProvider(
        default_response=response
    )

    result = provider.classify(
        create_request()
    )

    assert result.confidence == "MEDIUM"


def test_low_confidence_response_is_returned():
    response = create_response(
        confidence="LOW"
    )

    provider = MockAIProvider(
        default_response=response
    )

    result = provider.classify(
        create_request()
    )

    assert result.confidence == "LOW"


def test_mock_provider_preserves_request_information():
    captured_request = None

    class CapturingMockProvider(MockAIProvider):
        def classify(self, request):
            nonlocal captured_request
            captured_request = request

            return super().classify(request)

    response = create_response()

    provider = CapturingMockProvider(
        default_response=response
    )

    request = AIClassificationRequest(
        message=create_message("message-456"),
        existing_folders=(
            "Example Corporation",
            "Finance",
        ),
        known_domain_mapping="Example Corporation",
    )

    provider.classify(request)

    assert captured_request is not None
    assert captured_request.message.id == "message-456"
    assert captured_request.existing_folders == (
        "Example Corporation",
        "Finance",
    )
    assert (
        captured_request.known_domain_mapping
        == "Example Corporation"
    )