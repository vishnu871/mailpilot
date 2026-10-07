"""
MailPilot email domain models.

These models provide provider-independent representations of Outlook
messages and organization/folder classification decisions.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClassificationConfidence(str, Enum):
    """Confidence level assigned to an organization classification."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EmailMessage(BaseModel):
    """Provider-independent representation of an email message."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    sender_email: str = Field(..., min_length=3)
    sender_name: str | None = None
    subject: str = ""
    body_preview: str = ""
    received_at: datetime | None = None
    is_read: bool = False
    has_attachments: bool = False
    web_link: str | None = None

    @field_validator("sender_email")
    @classmethod
    def normalize_sender_email(cls, value: str) -> str:
        """Normalize sender addresses for consistent classification."""
        normalized = value.strip().lower()

        if not normalized:
            raise ValueError("sender_email cannot be empty")

        return normalized


class OrganizationClassification(BaseModel):
    """AI/classifier result for an email."""

    model_config = ConfigDict(extra="ignore")

    organization: str | None = None
    domain: str | None = None
    folder_name: str | None = None
    confidence: ClassificationConfidence = ClassificationConfidence.LOW
    reason: str = ""

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str | None) -> str | None:
        """Normalize domains while preserving an unknown value as None."""
        if value is None:
            return None

        normalized = value.strip().lower()

        if normalized.startswith("@"):
            normalized = normalized[1:]

        if not normalized:
            raise ValueError("domain cannot be empty when provided")

        return normalized

    @field_validator("folder_name")
    @classmethod
    def normalize_folder_name(cls, value: str | None) -> str | None:
        """Normalize folder names and reject explicitly empty values."""
        if value is None:
            return None

        normalized = " ".join(value.strip().split())

        if not normalized:
            raise ValueError("folder_name cannot be empty when provided")

        return normalized


class OrganizationMapping(BaseModel):
    """Persistent mapping between an email domain and an Outlook folder."""

    model_config = ConfigDict(extra="ignore")

    domain: str = Field(..., min_length=1)
    organization: str = Field(..., min_length=1)
    folder_name: str = Field(..., min_length=1)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        """Normalize stored domains."""
        normalized = value.strip().lower()

        if normalized.startswith("@"):
            normalized = normalized[1:]

        if not normalized:
            raise ValueError("domain cannot be empty")

        return normalized


class OrganizationDecision(BaseModel):
    """Final validated decision made before a mailbox mutation."""

    model_config = ConfigDict(extra="ignore")

    message_id: str = Field(..., min_length=1)
    organization: str | None = None
    domain: str | None = None
    destination_folder: str | None = None
    confidence: ClassificationConfidence = ClassificationConfidence.LOW
    should_create_folder: bool = False
    should_move: bool = False
    reason: str = ""