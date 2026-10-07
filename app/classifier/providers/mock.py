"""
MailPilot — Mock AI Provider

Deterministic AI-provider implementation used for development
and testing before connecting a real AI service.

This provider does not access the internet, Outlook, or any
external model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

from app.classifier.providers.ai_provider import (
    AIClassificationRequest,
    AIClassificationResponse,
    AIProvider,
)


class MockAIProvider(AIProvider):
    """
    Deterministic AI provider for local development and testing.

    Classifications are supplied explicitly by the caller, allowing
    the rest of the MailPilot classification pipeline to be tested
    without an external AI service.
    """

    def __init__(
        self,
        default_response: Optional[AIClassificationResponse] = None,
        responses_by_message_id: Optional[
            Mapping[str, AIClassificationResponse]
        ] = None,
    ) -> None:
        self.default_response = default_response
        self.responses_by_message_id = dict(
            responses_by_message_id or {}
        )

        self.classification_count = 0

    def classify(
        self,
        request: AIClassificationRequest,
    ) -> AIClassificationResponse:
        """
        Return the configured response for the message.

        Message-specific responses take priority over the default
        response.

        Raises:
            ValueError: If no response has been configured.
        """
        self.classification_count += 1

        message_id = request.message.id

        if message_id in self.responses_by_message_id:
            return self.responses_by_message_id[message_id]

        if self.default_response is not None:
            return self.default_response

        raise ValueError(
            "MockAIProvider has no classification configured for "
            f"message '{message_id}'."
        )