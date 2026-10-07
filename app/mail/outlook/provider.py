"""
Microsoft Outlook / Graph mail provider for MailPilot.

This module translates Microsoft Graph mail operations into the
provider-independent MailProvider interface used by MailPilot.

The provider accepts an injectable Graph client so it can be tested
without requiring a live Microsoft account or Azure application.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from app.mail.models import EmailMessage
from app.mail.provider import MailProvider, MailProviderError


class OutlookProvider(MailProvider):
    """
    Microsoft Graph implementation of the MailProvider interface.

    The graph_client object is intentionally injected rather than created
    inside this class. This keeps Microsoft authentication separate from
    mailbox operations and makes the provider easy to unit test.
    """

    GRAPH_MESSAGES_PATH = "/me/messages"
    GRAPH_INBOX_MESSAGES_PATH = "/me/mailFolders/inbox/messages"
    GRAPH_MAIL_FOLDERS_PATH = "/me/mailFolders"

    def __init__(
        self,
        graph_client: Any,
    ) -> None:
        """
        Initialize the Outlook provider.

        Args:
            graph_client:
                Object responsible for Microsoft Graph HTTP operations.

                The client is expected to provide:
                    get(path, **kwargs)
                    post(path, **kwargs)
                    patch(path, **kwargs)

                The exact authentication mechanism remains outside this
                provider.
        """
        if graph_client is None:
            raise ValueError("graph_client cannot be None")

        self.graph_client = graph_client

    # ------------------------------------------------------------------
    # Public MailProvider interface
    # ------------------------------------------------------------------

    def list_inbox_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        """
        Return messages currently in the Outlook inbox.
        """
        if limit <= 0:
            return []

        response = self._get(
            self.GRAPH_INBOX_MESSAGES_PATH,
            params={
                "$top": limit,
                "$orderby": "receivedDateTime desc",
            },
        )

        return self._parse_message_collection(response)

    def list_existing_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        """
        Return a bounded batch of existing mailbox messages.

        This method intentionally excludes deleted, sent, and draft
        messages from the historical scan.
        """
        messages, _next_link = self.list_existing_message_page(
            limit=limit,
            page_token=None,
        )
        return messages

    def list_existing_message_page(
        self,
        limit: int = 50,
        page_token: str | None = None,
        next_link: str | None = None,
    ) -> tuple[Sequence[EmailMessage], str | None]:
        """
        Return one page of existing mailbox messages.

        Microsoft Graph uses an @odata.nextLink URL for pagination.
        MailPilot's OrganizerProcessor uses the provider-neutral
        page_token name, so page_token is treated as the Graph nextLink.

        next_link is retained as a backwards-compatible alias for
        provider-level callers and existing tests.

        Args:
            limit:
                Maximum number of messages requested for the first page.

            page_token:
                Microsoft Graph @odata.nextLink returned by a previous
                request.

            next_link:
                Backwards-compatible alias for page_token.

        Returns:
            A tuple containing:
                - parsed EmailMessage objects
                - the next Graph pagination URL, or None
        """
        if limit <= 0:
            return [], None

        if (
            page_token is not None
            and not isinstance(page_token, str)
        ):
            raise ValueError(
                "page_token must be a string or None"
            )

        if (
            next_link is not None
            and not isinstance(next_link, str)
        ):
            raise ValueError(
                "next_link must be a string or None"
            )

        if page_token and next_link:
            if page_token != next_link:
                raise ValueError(
                    "page_token and next_link cannot contain "
                    "different pagination links"
                )

        pagination_link = page_token or next_link

        if pagination_link:
            response = self._get(
                pagination_link,
            )
        else:
            response = self._get(
                self.GRAPH_MESSAGES_PATH,
                params={
                    "$top": limit,
                    "$orderby": "receivedDateTime desc",
                    "$filter": (
                        "isDraft eq false"
                    ),
                },
            )

        messages = self._parse_message_collection(response)
        next_page = self._get_next_link(response)

        return messages, next_page

    def list_folders(self) -> Sequence[str]:
        """
        Return Outlook mail-folder display names.
        """
        response = self._get(
            self.GRAPH_MAIL_FOLDERS_PATH,
            params={
                "$top": 100,
            },
        )

        folders = response.get("value", [])

        if not isinstance(folders, list):
            raise MailProviderError(
                "Microsoft Graph returned an invalid mail-folder response."
            )

        names: list[str] = []

        for folder in folders:
            if not isinstance(folder, Mapping):
                continue

            display_name = folder.get("displayName")

            if isinstance(display_name, str) and display_name.strip():
                names.append(display_name.strip())

        return names

    def create_folder(
        self,
        folder_name: str,
    ) -> str:
        """
        Create an Outlook mail folder.

        If a folder with the same normalized name already exists,
        its existing display name is returned instead of creating a
        duplicate.
        """
        normalized_name = self._normalize_folder_name(folder_name)

        existing_folder = self._find_folder(normalized_name)

        if existing_folder is not None:
            return existing_folder

        response = self._post(
            self.GRAPH_MAIL_FOLDERS_PATH,
            json={
                "displayName": normalized_name,
            },
        )

        if not isinstance(response, Mapping):
            raise MailProviderError(
                "Microsoft Graph returned an invalid folder creation response."
            )

        created_name = response.get("displayName")

        if not isinstance(created_name, str) or not created_name.strip():
            raise MailProviderError(
                "Microsoft Graph did not return the created folder name."
            )

        return created_name.strip()

    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        """
        Move a message into the specified Outlook folder.

        The destination folder is resolved to its Graph folder ID before
        the message move is executed.
        """
        normalized_message_id = self._normalize_required_string(
            message_id,
            "message_id",
        )
        normalized_folder = self._normalize_folder_name(
            destination_folder,
        )

        folder = self._find_folder_details(normalized_folder)

        if folder is None:
            raise MailProviderError(
                f"Outlook folder '{normalized_folder}' does not exist."
            )

        folder_id = folder.get("id")

        if not isinstance(folder_id, str) or not folder_id.strip():
            raise MailProviderError(
                f"Outlook folder '{normalized_folder}' has no valid ID."
            )

        path = (
            f"{self.GRAPH_MESSAGES_PATH}/"
            f"{normalized_message_id}/move"
        )

        self._post(
            path,
            json={
                "destinationId": folder_id,
            },
        )

    # ------------------------------------------------------------------
    # Graph response parsing
    # ------------------------------------------------------------------

    def _parse_message_collection(
        self,
        response: Mapping[str, Any],
    ) -> list[EmailMessage]:
        """
        Convert a Graph message collection into EmailMessage objects.
        """
        values = response.get("value", [])

        if not isinstance(values, list):
            raise MailProviderError(
                "Microsoft Graph returned an invalid message collection."
            )

        messages: list[EmailMessage] = []

        for raw_message in values:
            if not isinstance(raw_message, Mapping):
                continue

            try:
                messages.append(self._parse_message(raw_message))
            except (KeyError, TypeError, ValueError) as exc:
                raise MailProviderError(
                    "Microsoft Graph returned an invalid message."
                ) from exc

        return messages

    def _parse_message(
        self,
        raw_message: Mapping[str, Any],
    ) -> EmailMessage:
        """
        Convert one Microsoft Graph message into EmailMessage.
        """
        message_id = raw_message.get("id")

        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("Graph message is missing id")

        sender_email, sender_name = self._parse_sender(raw_message)

        subject = raw_message.get("subject")
        if not isinstance(subject, str):
            subject = ""

        body_preview = raw_message.get("bodyPreview")
        if not isinstance(body_preview, str):
            body_preview = ""

        received_at = self._parse_datetime(
            raw_message.get("receivedDateTime")
        )

        is_read = raw_message.get("isRead", False)
        if not isinstance(is_read, bool):
            is_read = False

        has_attachments = raw_message.get("hasAttachments", False)
        if not isinstance(has_attachments, bool):
            has_attachments = False

        web_link = raw_message.get("webLink")
        if not isinstance(web_link, str) or not web_link.strip():
            web_link = None

        return EmailMessage(
            id=message_id.strip(),
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            body_preview=body_preview,
            received_at=received_at,
            is_read=is_read,
            has_attachments=has_attachments,
            web_link=web_link,
        )

    def _parse_sender(
        self,
        raw_message: Mapping[str, Any],
    ) -> tuple[str, str | None]:
        """
        Extract sender email and display name from a Graph message.
        """
        sender = raw_message.get("from")

        if not isinstance(sender, Mapping):
            raise ValueError("Graph message is missing sender")

        email_address = sender.get("emailAddress")

        if not isinstance(email_address, Mapping):
            raise ValueError("Graph message is missing sender emailAddress")

        address = email_address.get("address")

        if not isinstance(address, str) or not address.strip():
            raise ValueError("Graph message is missing sender address")

        name = email_address.get("name")

        if not isinstance(name, str) or not name.strip():
            name = None
        else:
            name = name.strip()

        return address.strip().lower(), name

    @staticmethod
    def _parse_datetime(
        value: Any,
    ) -> datetime | None:
        """
        Parse a Microsoft Graph ISO-8601 datetime value.
        """
        if value is None:
            return None

        if not isinstance(value, str) or not value.strip():
            return None

        normalized = value.strip()

        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"

        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _get_next_link(
        response: Mapping[str, Any],
    ) -> str | None:
        """
        Extract Microsoft Graph's pagination link.
        """
        next_link = response.get("@odata.nextLink")

        if isinstance(next_link, str) and next_link.strip():
            return next_link.strip()

        return None

    # ------------------------------------------------------------------
    # Folder helpers
    # ------------------------------------------------------------------

    def _find_folder(
        self,
        folder_name: str,
    ) -> str | None:
        """
        Find an existing folder using case-insensitive matching.
        """
        folder = self._find_folder_details(folder_name)

        if folder is None:
            return None

        display_name = folder.get("displayName")

        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()

        return None

    def _find_folder_details(
        self,
        folder_name: str,
    ) -> Mapping[str, Any] | None:
        """
        Find complete Graph folder metadata by display name.
        """
        normalized_target = self._normalize_folder_name(folder_name)

        response = self._get(
            self.GRAPH_MAIL_FOLDERS_PATH,
            params={
                "$top": 100,
            },
        )

        folders = response.get("value", [])

        if not isinstance(folders, list):
            raise MailProviderError(
                "Microsoft Graph returned an invalid mail-folder response."
            )

        for folder in folders:
            if not isinstance(folder, Mapping):
                continue

            display_name = folder.get("displayName")

            if not isinstance(display_name, str):
                continue

            if (
                self._normalize_folder_name(display_name).casefold()
                == normalized_target.casefold()
            ):
                return folder

        return None

    # ------------------------------------------------------------------
    # Graph client wrappers
    # ------------------------------------------------------------------

    def _get(
        self,
        path: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        """
        Execute a Graph GET request and normalize provider errors.
        """
        try:
            response = self.graph_client.get(path, **kwargs)
        except Exception as exc:
            raise MailProviderError(
                f"Microsoft Graph GET failed: {exc}"
            ) from exc

        return self._normalize_response(response)

    def _post(
        self,
        path: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        """
        Execute a Graph POST request and normalize provider errors.
        """
        try:
            response = self.graph_client.post(path, **kwargs)
        except Exception as exc:
            raise MailProviderError(
                f"Microsoft Graph POST failed: {exc}"
            ) from exc

        return self._normalize_response(response)

    @staticmethod
    def _normalize_response(
        response: Any,
    ) -> Mapping[str, Any]:
        """
        Normalize common Graph-client response styles.

        Supported responses:
            - Mapping objects
            - Objects exposing a JSON-like `json()` method
        """
        if isinstance(response, Mapping):
            return response

        json_method = getattr(response, "json", None)

        if callable(json_method):
            try:
                payload = json_method()
            except Exception as exc:
                raise MailProviderError(
                    "Microsoft Graph returned an unreadable response."
                ) from exc

            if isinstance(payload, Mapping):
                return payload

        raise MailProviderError(
            "Microsoft Graph returned an unsupported response type."
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_required_string(
        value: str,
        field_name: str,
    ) -> str:
        """
        Normalize and validate a required string value.
        """
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")

        normalized = " ".join(value.strip().split())

        if not normalized:
            raise ValueError(f"{field_name} cannot be empty")

        return normalized

    @classmethod
    def _normalize_folder_name(
        cls,
        folder_name: str,
    ) -> str:
        """
        Normalize and validate an Outlook folder name.
        """
        normalized = cls._normalize_required_string(
            folder_name,
            "folder_name",
        )

        if normalized in {".", ".."}:
            raise ValueError(
                "folder_name cannot be '.' or '..'"
            )

        return normalized