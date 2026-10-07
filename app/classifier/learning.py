"""
MailPilot — Organization Learning Manager

Persists trusted organization classifications into SQLite.

Learning policy:
- HIGH-confidence classifications may be learned automatically.
- MEDIUM-confidence classifications are never learned automatically.
- LOW-confidence classifications are never learned.
- Existing mappings are never overwritten.
- Generic consumer email providers are never learned as organizations.

This component only manages memory. It never moves, deletes, or modifies emails.
"""

from __future__ import annotations

from app.mail.models import (
    ClassificationConfidence,
    OrganizationClassification,
)
from database.manager import DatabaseManager


class LearningManager:
    """Persist trusted organization classifications."""

    GENERIC_EMAIL_PROVIDERS = {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.co.uk",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "mail.com",
        "gmx.com",
        "gmx.net",
    }

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    def learn(
        self,
        classification: OrganizationClassification,
    ) -> bool:
        """
        Learn a classification when it satisfies the learning policy.

        Returns:
            True if a new mapping was saved.
            False if the classification was not eligible for learning
            or an existing mapping already exists.
        """
        if not self._is_eligible(classification):
            return False

        domain = self._normalize_domain(
            classification.domain
        )

        if domain in self.GENERIC_EMAIL_PROVIDERS:
            return False

        existing_mapping = self.database.get_mapping(domain)

        if existing_mapping is not None:
            return False

        self.database.save_mapping(
            domain=domain,
            organization=classification.organization,
            folder_name=classification.folder_name,
            confidence=classification.confidence,
            reason=classification.reason,
        )

        return True

    @staticmethod
    def _is_eligible(
        classification: OrganizationClassification,
    ) -> bool:
        """Check whether a classification is eligible for automatic learning."""

        if classification.confidence != ClassificationConfidence.HIGH:
            return False

        if not classification.organization:
            return False

        if not classification.folder_name:
            return False

        if not classification.domain:
            return False

        return True

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        """Normalize a domain before persistence."""

        normalized = domain.strip().lower()

        if not normalized:
            raise ValueError(
                "Cannot learn an empty domain."
            )

        return normalized