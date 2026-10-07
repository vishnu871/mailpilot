"""
MailPilot — Generic AI Provider Contract

Defines the interface that any AI backend must implement.

MailPilot deliberately does not depend on a specific AI vendor.
A provider can later be implemented for:
- OpenAI
- Anthropic
- Ollama
- Another compatible local/cloud model
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.mail.models import EmailMessage


class AIProviderError(Exception):
    """Base exception for AI provider failures."""


class AIProviderConfigurationError(AIProviderError):
    """Raised when an AI provider is incorrectly configured."""


class AIProviderResponseError(AIProviderError):
    """Raised when an AI provider returns an invalid response."""


@dataclass(frozen=True)
class AIClassificationRequest:
    """
    Structured information supplied to an AI provider.

    The provider receives only the information required to classify
    the email. It does not receive permission to perform mailbox
    operations.
    """

    message: EmailMessage
    existing_folders: tuple[str, ...] = ()
    known_domain_mapping: Optional[str] = None


@dataclass(frozen=True)
class AIClassificationResponse:
    """
    Structured classification returned by an AI provider.

    The provider proposes a classification. It does not execute
    folder creation or message movement.
    """

    organization: Optional[str]
    domain: Optional[str]
    folder_name: Optional[str]
    confidence: str
    reason: str


class AIProvider(ABC):
    """
    Abstract interface for AI classification providers.

    Implementations must return a structured classification response.
    """

    @abstractmethod
    def classify(
        self,
        request: AIClassificationRequest,
    ) -> AIClassificationResponse:
        """
        Classify an email using the configured AI backend.

        Args:
            request: Structured email classification input.

        Returns:
            A structured AI classification response.

        Raises:
            AIProviderError: If the provider cannot complete
                classification.
        """
        raise NotImplementedError