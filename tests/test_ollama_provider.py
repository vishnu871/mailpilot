"""
Tests for the MailPilot Ollama AI provider.
"""

from __future__ import annotations

import pytest
import requests

from app.classifier.providers.ai_provider import (
    AIClassificationRequest,
    AIProviderConfigurationError,
    AIProviderResponseError,
)
from app.classifier.providers.ollama import OllamaAIProvider
from app.mail.models import EmailMessage


def make_request(
    *,
    sender_email: str = "no-reply@microsoft.com",
    sender_name: str = "Outlook Team",
    subject: str = "Welcome to your new Outlook.com account",
    body_preview: str = "Welcome to your new Outlook.com account.",
) -> AIClassificationRequest:
    message = EmailMessage(
        id="test-message-1",
        sender_email=sender_email,
        sender_name=sender_name,
        subject=subject,
        body_preview=body_preview,
    )

    return AIClassificationRequest(
        message=message,
        existing_folders=("Microsoft", "Google"),
    )


def test_provider_has_expected_defaults() -> None:
    provider = OllamaAIProvider()

    assert provider.model == "qwen2.5vl:3b"
    assert provider.base_url == "http://localhost:11434"
    assert provider.timeout == 120


def test_provider_rejects_empty_model() -> None:
    with pytest.raises(AIProviderConfigurationError):
        OllamaAIProvider(model="")


def test_provider_rejects_empty_base_url() -> None:
    with pytest.raises(AIProviderConfigurationError):
        OllamaAIProvider(base_url="")


def test_provider_rejects_invalid_timeout() -> None:
    with pytest.raises(AIProviderConfigurationError):
        OllamaAIProvider(timeout=0)


def test_prompt_requires_organization_folder() -> None:
    request = make_request()

    prompt = OllamaAIProvider._build_prompt(request)

    assert "folder_name MUST be the organization name" in prompt
    assert "The destination folder represents the ORGANIZATION" in prompt
    assert "Do NOT create topic folders" in prompt


def test_parse_valid_high_confidence_response() -> None:
    raw_response = """
    {
        "organization": "Microsoft",
        "domain": "microsoft.com",
        "folder_name": "Microsoft",
        "confidence": "HIGH",
        "reason": "The sender domain identifies Microsoft."
    }
    """

    result = OllamaAIProvider._parse_response(raw_response)

    assert result.organization == "Microsoft"
    assert result.domain == "microsoft.com"
    assert result.folder_name == "Microsoft"
    assert result.confidence == "HIGH"
    assert result.reason == "The sender domain identifies Microsoft."


def test_parse_response_with_extra_model_text() -> None:
    raw_response = """
    I will classify the email now.

    {
        "organization": "Microsoft",
        "domain": "MICROSOFT.COM",
        "folder_name": "Microsoft",
        "confidence": "high",
        "reason": "The sender domain identifies Microsoft."
    }

    Classification complete.
    """

    result = OllamaAIProvider._parse_response(raw_response)

    assert result.organization == "Microsoft"
    assert result.domain == "microsoft.com"
    assert result.folder_name == "Microsoft"
    assert result.confidence == "HIGH"


def test_parse_medium_confidence_response() -> None:
    raw_response = """
    {
        "organization": "Example Corporation",
        "domain": "example.com",
        "folder_name": "Example Corporation",
        "confidence": "MEDIUM",
        "reason": "The sender information suggests the organization."
    }
    """

    result = OllamaAIProvider._parse_response(raw_response)

    assert result.organization == "Example Corporation"
    assert result.domain == "example.com"
    assert result.folder_name == "Example Corporation"
    assert result.confidence == "MEDIUM"


def test_parse_low_confidence_response() -> None:
    raw_response = """
    {
        "organization": null,
        "domain": null,
        "folder_name": null,
        "confidence": "LOW",
        "reason": "The organization cannot be determined reliably."
    }
    """

    result = OllamaAIProvider._parse_response(raw_response)

    assert result.organization is None
    assert result.domain is None
    assert result.folder_name is None
    assert result.confidence == "LOW"


def test_invalid_confidence_is_rejected() -> None:
    raw_response = """
    {
        "organization": "Microsoft",
        "domain": "microsoft.com",
        "folder_name": "Microsoft",
        "confidence": "VERY_HIGH",
        "reason": "The sender domain identifies Microsoft."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_high_confidence_requires_organization() -> None:
    raw_response = """
    {
        "organization": null,
        "domain": "microsoft.com",
        "folder_name": "Microsoft",
        "confidence": "HIGH",
        "reason": "The sender domain identifies Microsoft."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_high_confidence_requires_domain() -> None:
    raw_response = """
    {
        "organization": "Microsoft",
        "domain": null,
        "folder_name": "Microsoft",
        "confidence": "HIGH",
        "reason": "The sender appears to be Microsoft."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_high_confidence_requires_folder_name() -> None:
    raw_response = """
    {
        "organization": "Microsoft",
        "domain": "microsoft.com",
        "folder_name": null,
        "confidence": "HIGH",
        "reason": "The sender domain identifies Microsoft."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_medium_confidence_requires_organization() -> None:
    raw_response = """
    {
        "organization": null,
        "domain": "example.com",
        "folder_name": "Example Corporation",
        "confidence": "MEDIUM",
        "reason": "The sender information is suggestive."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_empty_model_response_is_rejected() -> None:
    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response("")


def test_invalid_json_response_is_rejected() -> None:
    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(
            "This is not a JSON classification."
        )


def test_missing_reason_is_rejected() -> None:
    raw_response = """
    {
        "organization": "Microsoft",
        "domain": "microsoft.com",
        "folder_name": "Microsoft",
        "confidence": "HIGH"
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_non_string_confidence_is_rejected() -> None:
    raw_response = """
    {
        "organization": "Microsoft",
        "domain": "microsoft.com",
        "folder_name": "Microsoft",
        "confidence": 1,
        "reason": "The sender domain identifies Microsoft."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_non_string_optional_field_is_rejected() -> None:
    raw_response = """
    {
        "organization": 123,
        "domain": "microsoft.com",
        "folder_name": "Microsoft",
        "confidence": "HIGH",
        "reason": "The sender domain identifies Microsoft."
    }
    """

    with pytest.raises(AIProviderResponseError):
        OllamaAIProvider._parse_response(raw_response)


def test_classify_sends_thinking_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "response": (
                    '{"organization":"Microsoft",'
                    '"domain":"microsoft.com",'
                    '"folder_name":"Microsoft",'
                    '"confidence":"HIGH",'
                    '"reason":"Sender domain identifies Microsoft."}'
                )
            }

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        requests,
        "post",
        fake_post,
    )

    provider = OllamaAIProvider()
    result = provider.classify(make_request())

    payload = captured["json"]

    assert captured["url"] == (
        "http://localhost:11434/api/generate"
    )
    assert captured["timeout"] == 120
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen2.5vl:3b"
    assert payload["stream"] is False
    assert payload["think"] is False

    assert result.organization == "Microsoft"
    assert result.domain == "microsoft.com"
    assert result.folder_name == "Microsoft"
    assert result.confidence == "HIGH"


def test_classify_converts_ollama_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: int,
    ) -> None:
        raise requests.ConnectionError(
            "Ollama server unavailable"
        )

    monkeypatch.setattr(
        requests,
        "post",
        fake_post,
    )

    provider = OllamaAIProvider()

    with pytest.raises(AIProviderResponseError) as exc_info:
        provider.classify(make_request())

    assert "Failed to communicate with Ollama" in str(
        exc_info.value
    )


def test_classify_rejects_invalid_ollama_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "response": "not valid classification JSON"
            }

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(
        requests,
        "post",
        fake_post,
    )

    provider = OllamaAIProvider()

    with pytest.raises(AIProviderResponseError):
        provider.classify(make_request())


def test_classify_rejects_missing_response_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"model": "qwen2.5vl:3b"}

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: int,
    ) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(
        requests,
        "post",
        fake_post,
    )

    provider = OllamaAIProvider()

    with pytest.raises(AIProviderResponseError):
        provider.classify(make_request())
