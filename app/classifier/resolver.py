"""
MailPilot — Organization and Domain Resolver

Provides deterministic organization intelligence before the AI
classifier is invoked.

The resolver is intentionally conservative:
- Strong existing mappings are reused.
- Generic email-provider domains are not treated as organizations.
- Parent-domain relationships can reuse known organization mappings.
- Unknown or ambiguous domains return None.
- No mailbox changes are performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from database.manager import (
    DatabaseManager,
    OrganizationMappingRecord,
)


class OrganizationResolverError(Exception):
    """Base exception for organization resolver errors."""


@dataclass(frozen=True)
class OrganizationResolution:
    """Result of a deterministic organization resolution."""

    domain: str
    organization: str
    folder_name: str
    source: str
    confidence: str
    reason: str


class OrganizationResolver:
    """
    Resolves an email domain to a known organization.

    Resolution is deterministic and database-backed.

    The resolver does not use an AI model. AI is intentionally
    handled later when deterministic evidence is insufficient.
    """

    # These are generic mailbox providers, not organizations.
    #
    # This list is intentionally limited to common consumer
    # providers. It is not used to identify companies.
    GENERIC_EMAIL_PROVIDERS = frozenset(
        {
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
    )

    def __init__(self, database: DatabaseManager) -> None:
        self.database = database

    @staticmethod
    def normalize_email(email: str) -> str:
        """
        Normalize an email address.

        Raises:
            ValueError: If the email address is invalid or empty.
        """
        normalized = email.strip().lower()

        if not normalized:
            raise ValueError("Email address cannot be empty.")

        if normalized.count("@") != 1:
            raise ValueError("Email address must contain exactly one '@'.")

        local_part, domain = normalized.split("@", 1)

        if not local_part:
            raise ValueError("Email address must contain a local part.")

        if not domain:
            raise ValueError("Email address must contain a domain.")

        return f"{local_part}@{domain}"

    @classmethod
    def extract_domain(cls, email: str) -> str:
        """Extract and normalize the domain from an email address."""
        normalized = cls.normalize_email(email)
        return normalized.split("@", 1)[1]

    @classmethod
    def is_generic_email_provider(cls, domain: str) -> bool:
        """Return True when the domain is a generic consumer mailbox provider."""
        normalized = domain.strip().lower()
        return normalized in cls.GENERIC_EMAIL_PROVIDERS

    @staticmethod
    def _parent_domains(domain: str) -> list[str]:
        """
        Return possible parent domains.

        Example:

            alerts.mail.example.com

        produces:

            mail.example.com
            example.com

        The resolver deliberately stops before single-label domains.
        """
        parts = domain.split(".")

        if len(parts) < 3:
            return []

        return [
            ".".join(parts[index:])
            for index in range(1, len(parts) - 1)
        ]

    @staticmethod
    def _resolution_from_mapping(
        domain: str,
        mapping: OrganizationMappingRecord,
        source: str,
        reason: str,
    ) -> OrganizationResolution:
        """Build a resolution from a stored mapping."""
        return OrganizationResolution(
            domain=domain,
            organization=mapping.organization,
            folder_name=mapping.folder_name,
            source=source,
            confidence=mapping.confidence,
            reason=reason,
        )

    def resolve(
        self,
        email: str,
    ) -> Optional[OrganizationResolution]:
        """
        Resolve an email address to a known organization.

        Resolution order:

        1. Reject generic consumer mailbox providers.
        2. Look for an exact domain mapping.
        3. Look for a known parent-domain mapping.
        4. Return None when no deterministic mapping exists.
        """
        domain = self.extract_domain(email)

        if self.is_generic_email_provider(domain):
            return None

        exact_mapping = self.database.get_mapping(domain)

        if exact_mapping is not None:
            return self._resolution_from_mapping(
                domain=domain,
                mapping=exact_mapping,
                source="exact_domain_mapping",
                reason=(
                    f"Domain '{domain}' matches an existing "
                    "organization mapping."
                ),
            )

        for parent_domain in self._parent_domains(domain):
            if self.is_generic_email_provider(parent_domain):
                continue

            parent_mapping = self.database.get_mapping(parent_domain)

            if parent_mapping is not None:
                return self._resolution_from_mapping(
                    domain=domain,
                    mapping=parent_mapping,
                    source="parent_domain_mapping",
                    reason=(
                        f"Domain '{domain}' is a subdomain of the "
                        f"known organization domain '{parent_domain}'."
                    ),
                )

        return None