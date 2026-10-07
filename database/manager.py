"""
MailPilot — SQLite Database Manager

Provides the application's persistence layer for:
- Organization/domain mappings
- Processing history
- Database metadata

This module is the only layer that should directly interact with
the MailPilot SQLite database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class DatabaseError(Exception):
    """Base exception for MailPilot database errors."""


class DatabaseInitializationError(DatabaseError):
    """Raised when the database cannot be initialized."""


class DatabaseOperationError(DatabaseError):
    """Raised when a database operation fails."""


@dataclass(frozen=True)
class OrganizationMappingRecord:
    """Stored organization/domain mapping."""

    domain: str
    organization: str
    folder_name: str
    confidence: str
    reason: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProcessingHistoryRecord:
    """Stored processing/audit record."""

    message_id: str
    sender_email: str
    organization: Optional[str]
    domain: Optional[str]
    destination_folder: Optional[str]
    confidence: Optional[str]
    action: str
    status: str
    reason: Optional[str]
    processed_at: str


class DatabaseManager:
    """
    Manages MailPilot's local SQLite database.

    The database file is created automatically when initialize()
    is called.
    """

    VALID_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}

    def __init__(
        self,
        database_path: str | Path = "database/mailpilot.db",
        schema_path: str | Path = "database/schema.sql",
    ) -> None:
        self.database_path = Path(database_path)
        self.schema_path = Path(schema_path)

    def initialize(self) -> None:
        """
        Create the database and apply the schema.

        Raises:
            DatabaseInitializationError:
                If the schema cannot be read or the database cannot
                be initialized.
        """
        try:
            if not self.schema_path.exists():
                raise DatabaseInitializationError(
                    f"Database schema not found: {self.schema_path}"
                )

            self.database_path.parent.mkdir(parents=True, exist_ok=True)

            schema = self.schema_path.read_text(encoding="utf-8")

            with sqlite3.connect(self.database_path) as connection:
                connection.executescript(schema)
                connection.commit()

        except DatabaseInitializationError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseInitializationError(
                f"Failed to initialize database: {exc}"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        """Open a database connection."""
        if not self.database_path.exists():
            raise DatabaseOperationError(
                "Database does not exist. Call initialize() first."
            )

        try:
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            return connection
        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to open database: {exc}"
            ) from exc

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        """Normalize an email domain for consistent storage."""
        normalized = domain.strip().lower()

        if not normalized:
            raise ValueError("Domain cannot be empty.")

        return normalized

    @staticmethod
    def _validate_confidence(confidence: str) -> str:
        """Validate and normalize confidence."""
        normalized = confidence.strip().upper()

        if normalized not in DatabaseManager.VALID_CONFIDENCE:
            raise ValueError(
                "Confidence must be one of: HIGH, MEDIUM, LOW."
            )

        return normalized

    @staticmethod
    def _utc_now() -> str:
        """Return the current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def get_mapping(
        self,
        domain: str,
    ) -> Optional[OrganizationMappingRecord]:
        """
        Retrieve an organization mapping by domain.

        Returns:
            The stored mapping, or None if no mapping exists.
        """
        normalized_domain = self._normalize_domain(domain)

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        domain,
                        organization,
                        folder_name,
                        confidence,
                        reason,
                        created_at,
                        updated_at
                    FROM organization_mappings
                    WHERE domain = ?
                    """,
                    (normalized_domain,),
                ).fetchone()

        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to retrieve mapping for '{normalized_domain}': {exc}"
            ) from exc

        if row is None:
            return None

        return OrganizationMappingRecord(
            domain=row["domain"],
            organization=row["organization"],
            folder_name=row["folder_name"],
            confidence=row["confidence"],
            reason=row["reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_mapping(
        self,
        domain: str,
        organization: str,
        folder_name: str,
        confidence: str,
        reason: Optional[str] = None,
    ) -> OrganizationMappingRecord:
        """
        Create or update an organization/domain mapping.

        Existing mappings for the same domain are updated rather than
        duplicated.
        """
        normalized_domain = self._normalize_domain(domain)
        normalized_organization = organization.strip()
        normalized_folder = folder_name.strip()
        normalized_confidence = self._validate_confidence(confidence)

        if not normalized_organization:
            raise ValueError("Organization cannot be empty.")

        if not normalized_folder:
            raise ValueError("Folder name cannot be empty.")

        now = self._utc_now()

        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT created_at
                    FROM organization_mappings
                    WHERE domain = ?
                    """,
                    (normalized_domain,),
                ).fetchone()

                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO organization_mappings (
                            domain,
                            organization,
                            folder_name,
                            confidence,
                            reason,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized_domain,
                            normalized_organization,
                            normalized_folder,
                            normalized_confidence,
                            reason,
                            now,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE organization_mappings
                        SET
                            organization = ?,
                            folder_name = ?,
                            confidence = ?,
                            reason = ?,
                            updated_at = ?
                        WHERE domain = ?
                        """,
                        (
                            normalized_organization,
                            normalized_folder,
                            normalized_confidence,
                            reason,
                            now,
                            normalized_domain,
                        ),
                    )

                connection.commit()

        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to save mapping for '{normalized_domain}': {exc}"
            ) from exc

        mapping = self.get_mapping(normalized_domain)

        if mapping is None:
            raise DatabaseOperationError(
                f"Mapping was not available after saving '{normalized_domain}'."
            )

        return mapping

    def list_mappings(self) -> list[OrganizationMappingRecord]:
        """Return all stored organization mappings."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        domain,
                        organization,
                        folder_name,
                        confidence,
                        reason,
                        created_at,
                        updated_at
                    FROM organization_mappings
                    ORDER BY organization COLLATE NOCASE, domain COLLATE NOCASE
                    """
                ).fetchall()

        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to list organization mappings: {exc}"
            ) from exc

        return [
            OrganizationMappingRecord(
                domain=row["domain"],
                organization=row["organization"],
                folder_name=row["folder_name"],
                confidence=row["confidence"],
                reason=row["reason"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def record_processing(
        self,
        message_id: str,
        sender_email: str,
        organization: Optional[str],
        domain: Optional[str],
        destination_folder: Optional[str],
        confidence: Optional[str],
        action: str,
        status: str,
        reason: Optional[str] = None,
    ) -> ProcessingHistoryRecord:
        """Record one processing decision/action in the audit history."""
        normalized_message_id = message_id.strip()
        normalized_sender = sender_email.strip().lower()
        normalized_action = action.strip()
        normalized_status = status.strip()

        if not normalized_message_id:
            raise ValueError("Message ID cannot be empty.")

        if not normalized_sender:
            raise ValueError("Sender email cannot be empty.")

        if not normalized_action:
            raise ValueError("Action cannot be empty.")

        if not normalized_status:
            raise ValueError("Status cannot be empty.")

        normalized_domain = (
            self._normalize_domain(domain) if domain is not None else None
        )

        normalized_confidence = (
            self._validate_confidence(confidence)
            if confidence is not None
            else None
        )

        processed_at = self._utc_now()

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO processing_history (
                        message_id,
                        sender_email,
                        organization,
                        domain,
                        destination_folder,
                        confidence,
                        action,
                        status,
                        reason,
                        processed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_message_id,
                        normalized_sender,
                        organization.strip()
                        if organization is not None
                        else None,
                        normalized_domain,
                        destination_folder.strip()
                        if destination_folder is not None
                        else None,
                        normalized_confidence,
                        normalized_action,
                        normalized_status,
                        reason,
                        processed_at,
                    ),
                )
                connection.commit()

        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to record processing for "
                f"'{normalized_message_id}': {exc}"
            ) from exc

        return ProcessingHistoryRecord(
            message_id=normalized_message_id,
            sender_email=normalized_sender,
            organization=(
                organization.strip()
                if organization is not None
                else None
            ),
            domain=normalized_domain,
            destination_folder=(
                destination_folder.strip()
                if destination_folder is not None
                else None
            ),
            confidence=normalized_confidence,
            action=normalized_action,
            status=normalized_status,
            reason=reason,
            processed_at=processed_at,
        )

    def was_message_processed(self, message_id: str) -> bool:
        """
        Check whether a message has previously been recorded.

        This does not prevent reprocessing by itself; the organizer
        will decide how previous history should affect processing.
        """
        normalized_message_id = message_id.strip()

        if not normalized_message_id:
            raise ValueError("Message ID cannot be empty.")

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1
                    FROM processing_history
                    WHERE message_id = ?
                    LIMIT 1
                    """,
                    (normalized_message_id,),
                ).fetchone()

        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to check processing history for "
                f"'{normalized_message_id}': {exc}"
            ) from exc

        return row is not None

    def get_processing_history(
        self,
        message_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[ProcessingHistoryRecord]:
        """
        Retrieve processing history.

        If message_id is supplied, only records for that message
        are returned.
        """
        if limit < 1:
            raise ValueError("Limit must be at least 1.")

        try:
            with self._connect() as connection:
                if message_id is not None:
                    normalized_message_id = message_id.strip()

                    if not normalized_message_id:
                        raise ValueError("Message ID cannot be empty.")

                    rows = connection.execute(
                        """
                        SELECT
                            message_id,
                            sender_email,
                            organization,
                            domain,
                            destination_folder,
                            confidence,
                            action,
                            status,
                            reason,
                            processed_at
                        FROM processing_history
                        WHERE message_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (normalized_message_id, limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT
                            message_id,
                            sender_email,
                            organization,
                            domain,
                            destination_folder,
                            confidence,
                            action,
                            status,
                            reason,
                            processed_at
                        FROM processing_history
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()

        except ValueError:
            raise
        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to retrieve processing history: {exc}"
            ) from exc

        return [
            ProcessingHistoryRecord(
                message_id=row["message_id"],
                sender_email=row["sender_email"],
                organization=row["organization"],
                domain=row["domain"],
                destination_folder=row["destination_folder"],
                confidence=row["confidence"],
                action=row["action"],
                status=row["status"],
                reason=row["reason"],
                processed_at=row["processed_at"],
            )
            for row in rows
        ]

    def get_schema_version(self) -> Optional[str]:
        """Return the current database schema version."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT value
                    FROM database_metadata
                    WHERE key = 'schema_version'
                    """
                ).fetchone()

        except sqlite3.Error as exc:
            raise DatabaseOperationError(
                f"Failed to retrieve schema version: {exc}"
            ) from exc

        return row["value"] if row is not None else None