"""
Abstract mail provider interface for MailPilot.

This module defines the common interface that Gmail and Outlook
providers must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from app.mail.models import EmailMessage


class MailProviderError(Exception):
    """Base exception for mail-provider failures."""


class MailProvider(ABC):
    """
    Abstract interface for mailbox providers.

    Provider implementations such as GmailProvider and OutlookProvider
    translate their provider-specific APIs into these common operations.
    """

    @abstractmethod
    def list_inbox_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        """
        Return messages currently in the inbox.

        Args:
            limit: Maximum number of messages to return.

        Returns:
            A sequence of EmailMessage objects.
        """
        raise NotImplementedError

    @abstractmethod
    def list_folders(self) -> Sequence[str]:
        """
        Return the available mailbox folders/labels.
        """
        raise NotImplementedError

    @abstractmethod
    def create_folder(
        self,
        folder_name: str,
    ) -> str:
        """
        Create a destination folder/label.

        Args:
            folder_name: Name of the folder/label.

        Returns:
            The actual created or existing folder name.
        """
        raise NotImplementedError

    @abstractmethod
    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        """
        Move or label a message.

        Implementations must never delete the message.
        """
        raise NotImplementedError

    def list_existing_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        """
        Return a bounded batch of existing mailbox messages.

        This optional operation exists for the initial historical
        mailbox scan.

        Providers that support historical scanning should override it.

        It is intentionally NOT abstract so existing test/fake providers
        that only implement the original interface remain compatible.
        """
        raise MailProviderError(
            "This mail provider does not support existing-message scanning."
        )


class UnsupportedMailProvider(MailProvider):
    """
    Provider used when a requested mail service is not implemented.
    """

    def list_inbox_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        raise MailProviderError(
            "This mail provider does not support inbox message listing."
        )

    def list_folders(self) -> Sequence[str]:
        raise MailProviderError(
            "This mail provider does not support folder listing."
        )

    def create_folder(
        self,
        folder_name: str,
    ) -> str:
        raise MailProviderError(
            "This mail provider does not support folder creation."
        )

    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        raise MailProviderError(
            "This mail provider does not support moving messages."
        )