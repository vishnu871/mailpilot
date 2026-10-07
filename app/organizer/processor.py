"""
Mailbox processing orchestration for MailPilot.

This module coordinates:
- Fetching mailbox messages
- Running the organizer engine
- Applying dry-run/live actions
- Recording processing history
- Preventing duplicate LIVE processing
- Scanning existing mail in paginated batches
- Reporting historical-scan progress
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from app.mail.models import EmailMessage
from app.mail.provider import MailProvider, MailProviderError
from app.organizer.engine import OrganizerEngine, OrganizerResult


class OrganizerProcessor:
    """
    Coordinates mailbox processing through OrganizerEngine.

    The processor is responsible for orchestration and persistence.
    The OrganizerEngine remains responsible for deciding what should
    happen to an individual message.
    """

    def __init__(
        self,
        provider: MailProvider,
        engine: OrganizerEngine,
        database=None,
    ) -> None:
        self.provider = provider
        self.engine = engine
        self.database = database

    # ------------------------------------------------------------------
    # Current inbox processing
    # ------------------------------------------------------------------

    def process(
        self,
        dry_run: bool = True,
        limit: int = 50,
    ) -> list[OrganizerResult]:
        """
        Process messages currently in the inbox.

        This method intentionally remains bounded. It is suitable for
        ongoing/new-mail processing.
        """
        if limit <= 0:
            return []

        messages = self.provider.list_inbox_messages(
            limit=limit,
        )

        existing_folders = list(
            self.provider.list_folders()
        )

        return self._process_messages(
            messages=messages,
            dry_run=dry_run,
            existing_folders=existing_folders,
        )

    # ------------------------------------------------------------------
    # Existing mailbox processing
    # ------------------------------------------------------------------

    def process_existing(
        self,
        dry_run: bool = True,
        limit: int = 50,
    ) -> list[OrganizerResult]:
        """
        Process one bounded batch of existing mailbox messages.
        """
        if limit <= 0:
            return []

        messages = self.provider.list_existing_messages(
            limit=limit,
        )

        existing_folders = list(
            self.provider.list_folders()
        )

        return self._process_messages(
            messages=messages,
            dry_run=dry_run,
            existing_folders=existing_folders,
        )

    # ------------------------------------------------------------------
    # Paginated existing mailbox processing
    # ------------------------------------------------------------------

    def process_existing_paginated(
        self,
        dry_run: bool = True,
        batch_size: int = 50,
        max_batches: Optional[int] = None,
        progress_callback: Optional[
            Callable[
                [int, int, Sequence[OrganizerResult]],
                None,
            ]
        ] = None,
    ) -> list[OrganizerResult]:
        """
        Process existing mailbox messages page by page.

        Args:
            dry_run:
                If True, no mailbox changes are made.

            batch_size:
                Maximum number of messages requested per provider page.

            max_batches:
                Optional safety limit for the number of pages to scan.
                None means continue until the provider reports that
                there are no more pages.

            progress_callback:
                Optional callback invoked after each page.

                Callback arguments:
                    batch_number
                    fetched_count
                    results

                This is intended for CLI/UI progress reporting.

        Returns:
            OrganizerResult objects for messages processed during the
            invocation.
        """
        if batch_size <= 0:
            return []

        if max_batches is not None and max_batches <= 0:
            return []

        list_page_method = getattr(
            self.provider,
            "list_existing_message_page",
            None,
        )

        if not callable(list_page_method):
            raise MailProviderError(
                "This mail provider does not support paginated "
                "existing-message scanning."
            )

        all_results: list[OrganizerResult] = []

        page_token: Optional[str] = None
        batches_processed = 0

        existing_folders = list(
            self.provider.list_folders()
        )

        while True:
            if (
                max_batches is not None
                and batches_processed >= max_batches
            ):
                break

            messages, next_page_token = list_page_method(
                limit=batch_size,
                page_token=page_token,
            )

            batches_processed += 1

            if not messages:
                break

            results = self._process_messages(
                messages=messages,
                dry_run=dry_run,
                existing_folders=existing_folders,
                skip_processed=True,
            )

            all_results.extend(results)

            self._update_existing_folders(
                results,
                existing_folders,
            )

            if progress_callback is not None:
                progress_callback(
                    batches_processed,
                    len(messages),
                    results,
                )

            if not next_page_token:
                break

            page_token = next_page_token

        return all_results

    # ------------------------------------------------------------------
    # Shared message processing
    # ------------------------------------------------------------------

    def _process_messages(
        self,
        messages: Sequence[EmailMessage],
        dry_run: bool,
        existing_folders: list[str],
        skip_processed: bool = False,
    ) -> list[OrganizerResult]:
        """
        Process a collection of messages.
        """
        results: list[OrganizerResult] = []

        for message in messages:
            if (
                skip_processed
                and self._was_successfully_moved(message.id)
            ):
                continue

            result = self.engine.organize_message(
                message=message,
                existing_folders=existing_folders,
                dry_run=dry_run,
            )

            results.append(result)

            self._record_processing_history(
                message=message,
                result=result,
            )

            self._update_existing_folders(
                [result],
                existing_folders,
            )

        return results

    # ------------------------------------------------------------------
    # LIVE completion detection
    # ------------------------------------------------------------------

    def _was_successfully_moved(
        self,
        message_id: str,
    ) -> bool:
        """
        Return True only when the latest processing history shows that
        the message was actually moved successfully.
        """
        if self.database is None:
            return False

        history = self.database.get_processing_history(
            message_id=message_id,
            limit=1,
        )

        if not history:
            return False

        latest = history[0]

        return (
            latest.action == "moved"
            and latest.status == "success"
        )

    # ------------------------------------------------------------------
    # Processing history
    # ------------------------------------------------------------------

    def _record_processing_history(
        self,
        message: EmailMessage,
        result: OrganizerResult,
    ) -> None:
        """
        Record processing history when a database is configured.
        """
        if self.database is None:
            return

        decision = result.decision

        self.database.record_processing(
            message_id=message.id,
            sender_email=message.sender_email,
            organization=decision.organization,
            domain=decision.domain,
            destination_folder=decision.destination_folder,
            confidence=self._history_confidence(
                decision.confidence,
            ),
            action=result.action,
            status=self._history_status(result),
            reason=decision.reason,
        )

    # ------------------------------------------------------------------
    # Folder tracking
    # ------------------------------------------------------------------

    @staticmethod
    def _update_existing_folders(
        results: Sequence[OrganizerResult],
        existing_folders: list[str],
    ) -> None:
        """
        Keep the in-memory folder list synchronized with results.
        """
        for result in results:
            destination = result.decision.destination_folder

            if not destination:
                continue

            normalized_destination = (
                " ".join(
                    destination.strip().split()
                ).casefold()
            )

            if not normalized_destination:
                continue

            already_exists = any(
                " ".join(
                    folder.strip().split()
                ).casefold()
                == normalized_destination
                for folder in existing_folders
            )

            if not already_exists:
                existing_folders.append(
                    destination
                )

    # ------------------------------------------------------------------
    # History status helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _history_status(
        result: OrganizerResult,
    ) -> str:
        """
        Convert an OrganizerResult into a processing-history status.
        """
        if result.action in {
            "folder_resolution_failed",
            "move_failed",
        }:
            return "failure"

        return "success"

    @staticmethod
    def _history_confidence(
        confidence,
    ) -> Optional[str]:
        """
        Convert ClassificationConfidence to its stored value.
        """
        if confidence is None:
            return None

        value = getattr(
            confidence,
            "value",
            None,
        )

        if value is not None:
            return value

        return str(confidence)