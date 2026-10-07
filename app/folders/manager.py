"""
MailPilot folder management.

This module contains the deterministic logic for resolving destination
folders. It does not perform classification and does not decide whether
an email should be moved.

The flow is:

    requested folder
          ↓
    existing folder?
       /       \
     yes       no
      ↓         ↓
    reuse     create
       \       /
        folder ID/name
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.mail.provider import MailProvider, MailProviderError


class FolderManagerError(Exception):
    """Base exception for folder management failures."""


@dataclass(frozen=True)
class FolderResolution:
    """Result of resolving a destination folder."""

    folder_name: str
    created: bool


class FolderManager:
    """
    Resolves and creates destination folders through a MailProvider.

    Folder creation is deterministic. The manager never decides which
    organization a message belongs to; that decision comes from the
    classifier/organizer layers.
    """

    def __init__(self, provider: MailProvider) -> None:
        self._provider = provider

    def resolve_folder(self, folder_name: str) -> FolderResolution:
        """
        Find an existing folder or create it when necessary.

        Args:
            folder_name: Desired destination folder name.

        Returns:
            FolderResolution describing the final folder.

        Raises:
            FolderManagerError: If the folder name is invalid or the
                provider cannot list/create folders.
        """
        normalized_name = self._normalize_folder_name(folder_name)

        try:
            existing_folders = self._provider.list_folders()
        except MailProviderError as exc:
            raise FolderManagerError(
                f"Unable to list mailbox folders: {exc}"
            ) from exc

        existing_folder = self._find_matching_folder(
            normalized_name,
            existing_folders,
        )

        if existing_folder is not None:
            return FolderResolution(
                folder_name=existing_folder,
                created=False,
            )

        try:
            created_folder = self._provider.create_folder(normalized_name)
        except MailProviderError as exc:
            raise FolderManagerError(
                f"Unable to create folder '{normalized_name}': {exc}"
            ) from exc

        final_folder = created_folder.strip() if created_folder else ""

        if not final_folder:
            raise FolderManagerError(
                f"Provider reported success but returned no folder "
                f"identifier/name for '{normalized_name}'."
            )

        return FolderResolution(
            folder_name=final_folder,
            created=True,
        )

    @staticmethod
    def _normalize_folder_name(folder_name: str) -> str:
        """
        Normalize a folder name while preserving its human-readable form.
        """
        if not isinstance(folder_name, str):
            raise FolderManagerError("Folder name must be a string.")

        normalized = " ".join(folder_name.strip().split())

        if not normalized:
            raise FolderManagerError("Folder name cannot be empty.")

        if normalized in {".", ".."}:
            raise FolderManagerError(
                f"Invalid folder name: '{normalized}'."
            )

        return normalized

    @staticmethod
    def _find_matching_folder(
        requested_name: str,
        existing_folders: Sequence[str],
    ) -> str | None:
        """
        Find an existing folder using case-insensitive matching.

        This prevents duplicate folders such as:

            Microsoft
            microsoft
            MICROSOFT
        """
        requested_key = requested_name.casefold()

        for folder in existing_folders:
            if not isinstance(folder, str):
                continue

            normalized_existing = " ".join(folder.strip().split())

            if normalized_existing.casefold() == requested_key:
                return normalized_existing

        return None