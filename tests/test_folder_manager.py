"""
Tests for MailPilot folder management.
"""

from typing import Sequence

import pytest

from app.folders.manager import FolderManager, FolderManagerError
from app.mail.models import EmailMessage
from app.mail.provider import MailProvider, MailProviderError


class FakeMailProvider(MailProvider):
    """In-memory provider used for safe folder-manager tests."""

    def __init__(self, folders: Sequence[str] | None = None) -> None:
        self.folders = list(folders or [])
        self.created_folders: list[str] = []
        self.moved_messages: list[tuple[str, str]] = []

    def list_inbox_messages(self, limit: int = 50):
        return []

    def list_folders(self) -> Sequence[str]:
        return list(self.folders)

    def create_folder(self, folder_name: str) -> str:
        self.created_folders.append(folder_name)
        self.folders.append(folder_name)
        return folder_name

    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        self.moved_messages.append(
            (message_id, destination_folder)
        )


class FailingFolderListProvider(FakeMailProvider):
    """Provider that fails while listing folders."""

    def list_folders(self) -> Sequence[str]:
        raise MailProviderError("Simulated folder-list failure")


class FailingFolderCreateProvider(FakeMailProvider):
    """Provider that fails while creating a folder."""

    def create_folder(self, folder_name: str) -> str:
        raise MailProviderError("Simulated folder-create failure")


def test_existing_folder_is_reused() -> None:
    provider = FakeMailProvider(["Microsoft", "Google"])
    manager = FolderManager(provider)

    result = manager.resolve_folder("Microsoft")

    assert result.folder_name == "Microsoft"
    assert result.created is False
    assert provider.created_folders == []


def test_folder_matching_is_case_insensitive() -> None:
    provider = FakeMailProvider(["Microsoft"])
    manager = FolderManager(provider)

    result = manager.resolve_folder("mIcRoSoFt")

    assert result.folder_name == "Microsoft"
    assert result.created is False
    assert provider.created_folders == []


def test_missing_folder_is_created() -> None:
    provider = FakeMailProvider(["Microsoft"])
    manager = FolderManager(provider)

    result = manager.resolve_folder("Google")

    assert result.folder_name == "Google"
    assert result.created is True
    assert provider.created_folders == ["Google"]


def test_folder_name_whitespace_is_normalized() -> None:
    provider = FakeMailProvider(["Microsoft"])
    manager = FolderManager(provider)

    result = manager.resolve_folder("  Google   Workspace  ")

    assert result.folder_name == "Google Workspace"
    assert result.created is True
    assert provider.created_folders == ["Google Workspace"]


@pytest.mark.parametrize(
    "folder_name",
    [
        "",
        "   ",
        ".",
        "..",
    ],
)
def test_invalid_folder_names_are_rejected(folder_name: str) -> None:
    provider = FakeMailProvider()
    manager = FolderManager(provider)

    with pytest.raises(FolderManagerError):
        manager.resolve_folder(folder_name)

    assert provider.created_folders == []


def test_folder_listing_failure_is_safe() -> None:
    provider = FailingFolderListProvider()
    manager = FolderManager(provider)

    with pytest.raises(FolderManagerError):
        manager.resolve_folder("Microsoft")


def test_folder_creation_failure_is_safe() -> None:
    provider = FailingFolderCreateProvider()
    manager = FolderManager(provider)

    with pytest.raises(FolderManagerError):
        manager.resolve_folder("Microsoft")