"""
MailPilot classifier interface.

The classifier is responsible for determining which organization and
destination folder an email most likely belongs to.

The classifier does NOT:
- create folders
- move emails
- modify the mailbox
- contain hardcoded company-specific rules

Those responsibilities belong to the organizer and folder/provider layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from app.mail.models import (
    EmailMessage,
    OrganizationClassification,
    OrganizationMapping,
)


class ClassifierError(Exception):
    """Base exception for classifier failures."""


class EmailClassifier(ABC):
    """
    Abstract interface for email organization classification.

    Implementations may use:
    - deterministic sender/domain mappings
    - existing mailbox folder information
    - an LLM
    - a local model
    - a hybrid of deterministic rules and AI

    The rest of MailPilot only depends on this interface.
    """

    @abstractmethod
    def classify(
        self,
        message: EmailMessage,
        existing_mappings: Sequence[OrganizationMapping] | None = None,
        existing_folders: Sequence[str] | None = None,
    ) -> OrganizationClassification:
        """
        Classify an email into an organization/folder.

        Args:
            message: Normalized email message.
            existing_mappings: Known organization/domain-to-folder mappings.
            existing_folders: Existing mailbox folder names.

        Returns:
            Structured classification result.
        """
        raise NotImplementedError


class UnconfiguredClassifier(EmailClassifier):
    """
    Placeholder classifier used until an actual intelligence provider
    is configured.

    It intentionally returns a LOW-confidence result rather than making
    an unsafe guess.
    """

    def classify(
        self,
        message: EmailMessage,
        existing_mappings: Sequence[OrganizationMapping] | None = None,
        existing_folders: Sequence[str] | None = None,
    ) -> OrganizationClassification:
        return OrganizationClassification(
            organization=None,
            domain=None,
            folder_name=None,
            confidence="low",
            reason=(
                "No classifier implementation has been configured. "
                "The message was intentionally left unclassified."
            ),
        )