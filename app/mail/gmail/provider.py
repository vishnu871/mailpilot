"""
Gmail mail provider implementation for MailPilot.
"""

from __future__ import annotations

import base64
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Optional, Sequence

from bs4 import BeautifulSoup

from app.mail.models import EmailMessage
from app.mail.provider import MailProvider, MailProviderError


class GmailProvider(MailProvider):
    """Gmail implementation of the MailProvider interface."""

    BODY_PREVIEW_LIMIT = 2000

    def __init__(
        self,
        authenticator: Any = None,
        service: Any = None,
    ) -> None:
        self.authenticator = authenticator
        self._service = service

    def _build_service(self) -> Any:
        """Build and cache the Gmail API service."""
        if self._service is not None:
            return self._service

        if self.authenticator is None:
            raise MailProviderError(
                "Gmail authentication is not configured."
            )

        try:
            from googleapiclient.discovery import build

            credentials = self.authenticator.authenticate()

            self._service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )

            return self._service

        except Exception as exc:
            raise MailProviderError(
                f"Failed to initialize Gmail service: {exc}"
            ) from exc

    @property
    def service(self) -> Any:
        """Return the Gmail API service."""
        return self._build_service()

    def list_inbox_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        """Return messages currently in the Gmail inbox."""
        if limit <= 0:
            return []

        service = self.service

        try:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=["INBOX"],
                    maxResults=limit,
                )
                .execute()
            )

            message_refs = response.get("messages", [])

            return self._fetch_messages(
                message_refs,
                service=service,
            )

        except MailProviderError:
            raise

        except Exception as exc:
            raise MailProviderError(
                f"Failed to list Gmail inbox messages: {exc}"
            ) from exc

    def list_existing_messages(
        self,
        limit: int = 50,
    ) -> Sequence[EmailMessage]:
        """
        Return the first batch of existing eligible Gmail messages.

        For complete historical scanning, use
        list_existing_message_page().
        """
        messages, _ = self.list_existing_message_page(
            limit=limit,
            page_token=None,
        )

        return messages

    def list_existing_message_page(
        self,
        limit: int = 50,
        page_token: Optional[str] = None,
    ) -> tuple[Sequence[EmailMessage], Optional[str]]:
        """
        Return one page of existing eligible Gmail messages.

        Eligible mail excludes:
        - Spam
        - Trash
        - Sent
        - Drafts

        Archived mail remains eligible.
        """
        if limit <= 0:
            return [], None

        if page_token is not None and not isinstance(
            page_token,
            str,
        ):
            raise MailProviderError(
                "Gmail page token must be a string or None."
            )

        service = self.service

        try:
            request_kwargs: dict[str, Any] = {
                "userId": "me",
                "q": "-in:spam -in:trash -in:sent -in:drafts",
                "maxResults": limit,
            }

            if page_token:
                request_kwargs["pageToken"] = page_token

            response = (
                service.users()
                .messages()
                .list(
                    **request_kwargs,
                )
                .execute()
            )

            message_refs = response.get(
                "messages",
                [],
            )

            messages = self._fetch_messages(
                message_refs,
                service=service,
            )

            return (
                messages,
                response.get("nextPageToken"),
            )

        except MailProviderError:
            raise

        except Exception as exc:
            raise MailProviderError(
                f"Failed to list existing Gmail message page: {exc}"
            ) from exc

    def _fetch_messages(
        self,
        message_refs: Sequence[dict[str, Any]],
        service: Any,
    ) -> list[EmailMessage]:
        """Fetch complete Gmail messages from message references."""
        messages: list[EmailMessage] = []

        for message_ref in message_refs:
            message_id = message_ref.get("id")

            if not message_id:
                continue

            try:
                response = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=message_id,
                        format="full",
                    )
                    .execute()
                )

                message = self._parse_message(response)

                if message is not None:
                    messages.append(message)

            except Exception as exc:
                raise MailProviderError(
                    f"Failed to fetch Gmail message "
                    f"{message_id}: {exc}"
                ) from exc

        return messages

    def _parse_message(
        self,
        message: dict[str, Any],
    ) -> Optional[EmailMessage]:
        """Convert a Gmail API message into EmailMessage."""
        message_id = message.get("id")

        if not message_id:
            return None

        payload = message.get(
            "payload",
            {},
        )

        headers = payload.get(
            "headers",
            [],
        )

        header_map: dict[str, str] = {}

        for header in headers:
            name = header.get(
                "name",
                "",
            )

            value = header.get(
                "value",
                "",
            )

            if name:
                header_map[name.lower()] = value

        sender_email, sender_name = self._parse_sender(
            header_map.get(
                "from",
                "",
            )
        )

        subject = header_map.get(
            "subject",
            "",
        )

        body_preview = self._extract_body_preview(
            payload,
        )

        label_ids = set(
            message.get(
                "labelIds",
                [],
            )
        )

        received_at = self._parse_received_at(
            header_map.get("date")
        )

        is_read = "UNREAD" not in label_ids

        has_attachments = self._has_attachments(
            payload,
        )

        web_link = self._build_web_link(
            message_id,
        )

        return EmailMessage(
            id=message_id,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            body_preview=body_preview,
            received_at=received_at,
            is_read=is_read,
            has_attachments=has_attachments,
            web_link=web_link,
        )

    @staticmethod
    def _parse_sender(
        value: str,
    ) -> tuple[str, str]:
        """Extract sender email address and display name."""
        value = value.strip()

        if not value:
            return "", ""

        try:
            name, email_address = parseaddr(value)

            return (
                email_address.strip(),
                name.strip(),
            )

        except Exception:
            return value, ""

    @staticmethod
    def _parse_received_at(
        value: Optional[str],
    ) -> Any:
        """Convert the RFC email Date header into a datetime."""
        if not value:
            return None

        try:
            return parsedate_to_datetime(value)

        except Exception:
            return None

    def _extract_body_preview(
        self,
        payload: dict[str, Any],
    ) -> str:
        """Extract a bounded plain-text or HTML body preview."""
        plain_text = self._find_body_part(
            payload,
            "text/plain",
        )

        if plain_text:
            return self._clean_plain_text(
                plain_text,
            )

        html_text = self._find_body_part(
            payload,
            "text/html",
        )

        if html_text:
            return self._clean_html_text(
                html_text,
            )

        return ""

    def _find_body_part(
        self,
        part: dict[str, Any],
        mime_type: str,
    ) -> str:
        """Recursively find a body part of the requested MIME type."""
        current_mime = part.get(
            "mimeType",
            "",
        )

        if current_mime == mime_type:
            body = part.get(
                "body",
                {},
            )

            data = body.get("data")

            if data:
                return self._decode_body(data)

        for child in part.get(
            "parts",
            [],
        ) or []:
            result = self._find_body_part(
                child,
                mime_type,
            )

            if result:
                return result

        return ""

    @staticmethod
    def _decode_body(
        data: str,
    ) -> str:
        """Decode Gmail's URL-safe base64 message body."""
        try:
            padding = "=" * (-len(data) % 4)

            decoded = base64.urlsafe_b64decode(
                data + padding,
            )

            return decoded.decode(
                "utf-8",
                errors="replace",
            )

        except Exception:
            return ""

    def _clean_plain_text(
        self,
        text: str,
    ) -> str:
        """Normalize plain-text content."""
        text = (
            text.replace("\r", " ")
            .replace("\n", " ")
        )

        text = " ".join(
            text.split()
        )

        return text[
            : self.BODY_PREVIEW_LIMIT
        ]

    def _clean_html_text(
        self,
        html: str,
    ) -> str:
        """Convert HTML email content to normalized plain text."""
        try:
            soup = BeautifulSoup(
                html,
                "html.parser",
            )

            text = soup.get_text(
                separator=" ",
            )

            text = " ".join(
                text.split()
            )

            return text[
                : self.BODY_PREVIEW_LIMIT
            ]

        except Exception:
            return self._clean_plain_text(
                html,
            )

    def _has_attachments(
        self,
        payload: dict[str, Any],
    ) -> bool:
        """Detect whether a Gmail message contains an attachment."""
        filename = payload.get("filename")

        if filename:
            return True

        for part in payload.get(
            "parts",
            [],
        ) or []:
            if self._has_attachments(part):
                return True

        return False

    @staticmethod
    def _build_web_link(
        message_id: str,
    ) -> str:
        """Build a Gmail web URL for the message."""
        return (
            "https://mail.google.com/mail/u/0/"
            f"#all/{message_id}"
        )

    def list_folders(
        self,
    ) -> Sequence[str]:
        """Return Gmail labels."""
        service = self.service

        try:
            response = (
                service.users()
                .labels()
                .list(
                    userId="me",
                )
                .execute()
            )

            labels = response.get(
                "labels",
                [],
            )

            names = [
                label.get(
                    "name",
                    "",
                ).strip()
                for label in labels
                if label.get(
                    "name",
                    "",
                ).strip()
            ]

            return sorted(
                names,
                key=str.casefold,
            )

        except Exception as exc:
            raise MailProviderError(
                f"Failed to list Gmail labels: {exc}"
            ) from exc

    def create_folder(
        self,
        folder_name: str,
    ) -> str:
        """Create a Gmail label if it does not already exist."""
        if not isinstance(
            folder_name,
            str,
        ):
            raise MailProviderError(
                "Gmail label name must be a string."
            )

        normalized_name = " ".join(
            folder_name.strip().split()
        )

        if not normalized_name:
            raise MailProviderError(
                "Gmail label name cannot be empty."
            )

        if normalized_name in {
            ".",
            "..",
        }:
            raise MailProviderError(
                "Invalid Gmail label name."
            )

        try:
            existing = self._find_label_by_name(
                normalized_name,
            )

            if existing is not None:
                return existing["name"]

            response = (
                self.service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": normalized_name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            )

            return response.get(
                "name",
                normalized_name,
            )

        except MailProviderError:
            raise

        except Exception as exc:
            raise MailProviderError(
                f"Failed to create Gmail label "
                f"'{normalized_name}': {exc}"
            ) from exc

    def move_message(
        self,
        message_id: str,
        destination_folder: str,
    ) -> None:
        """
        Move a Gmail message to a destination label.

        The message is never deleted.
        """
        if (
            not isinstance(
                message_id,
                str,
            )
            or not message_id.strip()
        ):
            raise MailProviderError(
                "Gmail message ID cannot be empty."
            )

        if (
            not isinstance(
                destination_folder,
                str,
            )
            or not destination_folder.strip()
        ):
            raise MailProviderError(
                "Gmail destination label cannot be empty."
            )

        try:
            label = self._find_label_by_name(
                destination_folder,
            )

            if label is None:
                raise MailProviderError(
                    f"Gmail label '{destination_folder}' "
                    "does not exist."
                )

            label_id = label.get("id")

            if not label_id:
                raise MailProviderError(
                    f"Gmail label '{destination_folder}' "
                    "does not have a valid ID."
                )

            (
                self.service.users()
                .messages()
                .modify(
                    userId="me",
                    id=message_id,
                    body={
                        "addLabelIds": [
                            label_id,
                        ],
                        "removeLabelIds": [
                            "INBOX",
                        ],
                    },
                )
                .execute()
            )

        except MailProviderError:
            raise

        except Exception as exc:
            raise MailProviderError(
                f"Failed to move Gmail message "
                f"'{message_id}' to "
                f"'{destination_folder}': {exc}"
            ) from exc

    def _find_label_by_name(
        self,
        label_name: str,
    ) -> Optional[dict[str, Any]]:
        """Find a Gmail label using normalized case-insensitive matching."""
        response = (
            self.service.users()
            .labels()
            .list(
                userId="me",
            )
            .execute()
        )

        labels = response.get(
            "labels",
            [],
        )

        normalized_target = " ".join(
            label_name.strip().split()
        ).casefold()

        for label in labels:
            current_name = label.get(
                "name",
                "",
            )

            normalized_current = " ".join(
                current_name.strip().split()
            ).casefold()

            if normalized_current == normalized_target:
                return label

        return None
