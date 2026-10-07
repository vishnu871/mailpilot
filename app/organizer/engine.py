"""
MailPilot — Organizer Engine

Converts classifier results into safe mailbox actions.

Responsibilities:
- Analyze an individual email.
- Convert classification into an organization decision.
- Enforce confidence-based movement rules.
- Support dry-run operation.
- Resolve folders only when an actual move is allowed.
- Never delete messages.

The engine accepts both:
- OrganizationClassification
- ClassificationResult returned by AIClassificationOrchestrator
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.classifier.base import EmailClassifier
from app.mail.models import (
    ClassificationConfidence,
    EmailMessage,
    OrganizationClassification,
    OrganizationDecision,
)
from app.mail.provider import MailProvider
from app.folders.manager import FolderManager


@dataclass(frozen=True)
class OrganizerResult:
    """Result of processing one email."""

    decision: OrganizationDecision
    action: str


class OrganizerEngine:
    """
    Safely converts classifier results into mailbox actions.

    Movement policy:

    HIGH + valid destination
        → eligible for movement

    MEDIUM
        → leave untouched

    LOW
        → leave untouched

    Dry run
        → report what would happen without mailbox changes
    """

    def __init__(
        self,
        provider: MailProvider,
        classifier: EmailClassifier,
        folder_manager: Optional[FolderManager] = None,
    ) -> None:
        self.provider = provider
        self.classifier = classifier
        self.folder_manager = folder_manager or FolderManager(
            provider
        )

    def analyze_message(
        self,
        message: EmailMessage,
        existing_folders: Optional[
            list[str] | tuple[str, ...]
        ] = None,
    ) -> OrganizationDecision:
        """
        Analyze one message and produce a movement decision.

        No mailbox changes are performed here.
        """
        try:
            classifier_result = self.classifier.classify(
                message=message,
                existing_mappings=None,
                existing_folders=existing_folders,
            )

            classification = self._extract_classification(
                classifier_result
            )

        except Exception as exc:
            return OrganizationDecision(
                message_id=message.id,
                organization=None,
                domain=self._safe_domain(message),
                destination_folder=None,
                confidence=ClassificationConfidence.LOW,
                should_create_folder=False,
                should_move=False,
                reason=(
                    f"Unexpected classification error: {exc}"
                ),
            )

        return self._build_decision(
            message=message,
            classification=classification,
            existing_folders=existing_folders,
        )

    def organize_message(
        self,
        message: EmailMessage,
        existing_folders: Optional[
            list[str] | tuple[str, ...]
        ] = None,
        dry_run: bool = True,
    ) -> OrganizerResult:
        """
        Analyze and optionally organize one message.

        Dry-run mode never creates folders or moves messages.
        """
        decision = self.analyze_message(
            message=message,
            existing_folders=existing_folders,
        )

        if not decision.should_move:
            return OrganizerResult(
                decision=decision,
                action="left_untouched",
            )

        if not decision.destination_folder:
            return OrganizerResult(
                decision=decision,
                action="left_untouched",
            )

        if dry_run:
            return OrganizerResult(
                decision=decision,
                action="dry_run_would_move",
            )

        try:
            folder_resolution = self.folder_manager.resolve_folder(
                decision.destination_folder
            )

        except Exception as exc:
            failed_decision = OrganizationDecision(
                message_id=decision.message_id,
                organization=decision.organization,
                domain=decision.domain,
                destination_folder=decision.destination_folder,
                confidence=decision.confidence,
                should_create_folder=False,
                should_move=False,
                reason=(
                    f"Folder resolution failed safely: {exc}"
                ),
            )

            return OrganizerResult(
                decision=failed_decision,
                action="folder_resolution_failed",
            )

        try:
            self.provider.move_message(
                message_id=message.id,
                destination_folder=folder_resolution.folder_name,
            )

        except Exception as exc:
            failed_decision = OrganizationDecision(
                message_id=decision.message_id,
                organization=decision.organization,
                domain=decision.domain,
                destination_folder=folder_resolution.folder_name,
                confidence=decision.confidence,
                should_create_folder=folder_resolution.created,
                should_move=False,
                reason=(
                    f"Move failed safely: {exc}"
                ),
            )

            return OrganizerResult(
                decision=failed_decision,
                action="move_failed",
            )

        final_decision = OrganizationDecision(
            message_id=decision.message_id,
            organization=decision.organization,
            domain=decision.domain,
            destination_folder=folder_resolution.folder_name,
            confidence=decision.confidence,
            should_create_folder=folder_resolution.created,
            should_move=True,
            reason=decision.reason,
        )

        return OrganizerResult(
            decision=final_decision,
            action="moved",
        )

    @staticmethod
    def _extract_classification(
        classifier_result,
    ) -> OrganizationClassification:
        """
        Extract OrganizationClassification from classifier output.

        Supports:
        - OrganizationClassification directly.
        - ClassificationResult returned by AIClassificationOrchestrator.
        """
        if isinstance(
            classifier_result,
            OrganizationClassification,
        ):
            return classifier_result

        classification = getattr(
            classifier_result,
            "classification",
            None,
        )

        if isinstance(
            classification,
            OrganizationClassification,
        ):
            return classification

        raise TypeError(
            "Classifier returned an unsupported result type."
        )

    @staticmethod
    def _build_decision(
        message: EmailMessage,
        classification: OrganizationClassification,
        existing_folders: Optional[
            list[str] | tuple[str, ...]
        ] = None,
    ) -> OrganizationDecision:
        """
        Convert classifier output into a deterministic safety decision.

        The organizer determines whether movement is permitted and
        whether a destination folder needs to be created.
        """

        should_move = (
            classification.confidence
            == ClassificationConfidence.HIGH
            and bool(classification.organization)
            and bool(classification.folder_name)
        )

        should_create_folder = False

        if should_move and classification.folder_name:
            normalized_destination = (
                classification.folder_name.strip().lower()
            )

            known_folders = {
                folder.strip().lower()
                for folder in (existing_folders or ())
                if folder and folder.strip()
            }

            should_create_folder = (
                normalized_destination not in known_folders
            )

        reason = classification.reason

        if classification.confidence == ClassificationConfidence.MEDIUM:
            reason = (
                f"{classification.reason} "
                "Medium confidence requires confirmation before movement."
            )

        elif classification.confidence == ClassificationConfidence.LOW:
            reason = (
                f"{classification.reason} "
                "Low confidence classification will remain untouched."
            )

        elif not should_move:
            reason = (
                f"{classification.reason} "
                "Required organization or folder information is missing."
            )

        return OrganizationDecision(
            message_id=message.id,
            organization=classification.organization,
            domain=classification.domain,
            destination_folder=classification.folder_name,
            confidence=classification.confidence,
            should_create_folder=should_create_folder,
            should_move=should_move,
            reason=reason,
        )

    @staticmethod
    def _safe_domain(
        message: EmailMessage,
    ) -> str:
        """Extract a safe sender domain for error reporting."""

        try:
            sender = message.sender_email.strip().lower()

            if "@" in sender:
                return sender.rsplit("@", 1)[1]

        except Exception:
            pass

        return "unknown"