"""
Tests for the Microsoft Outlook / Graph mail provider.

These tests use a fake Graph client, so no Microsoft account,
Azure/Entra application, or network connection is required.
"""

from __future__ import annotations

from datetime import timezone

import pytest

from app.mail.models import EmailMessage
from app.mail.outlook.provider import OutlookProvider
from app.mail.provider import MailProviderError


class FakeGraphClient:
    """Small in-memory Microsoft Graph client used by the tests."""

    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.responses: dict[str, dict] = {}

    def get(self, path: str, **kwargs):
        self.get_calls.append((path, kwargs))

        if path not in self.responses:
            raise RuntimeError(f"No fake GET response configured for: {path}")

        return self.responses[path]

    def post(self, path: str, **kwargs):
        self.post_calls.append((path, kwargs))

        if path not in self.responses:
            raise RuntimeError(f"No fake POST response configured for: {path}")

        return self.responses[path]


def graph_message(
    *,
    message_id: str = "message-1",
    sender_email: str = "notifications@example.com",
    sender_name: str = "Example Notifications",
    subject: str = "Example subject",
    body_preview: str = "Example body preview",
    received_date_time: str = "2026-09-30T05:30:00Z",
    is_read: bool = True,
    has_attachments: bool = False,
    web_link: str = "https://outlook.office.com/mail/inbox/id/message-1",
) -> dict:
    """Create a realistic Microsoft Graph message payload."""

    return {
        "id": message_id,
        "subject": subject,
        "bodyPreview": body_preview,
        "receivedDateTime": received_date_time,
        "isRead": is_read,
        "hasAttachments": has_attachments,
        "webLink": web_link,
        "from": {
            "emailAddress": {
                "name": sender_name,
                "address": sender_email,
            }
        },
    }


def graph_folder(
    *,
    folder_id: str,
    display_name: str,
) -> dict:
    """Create a Microsoft Graph mail-folder payload."""

    return {
        "id": folder_id,
        "displayName": display_name,
    }


# ----------------------------------------------------------------------
# Initialization
# ----------------------------------------------------------------------


def test_provider_requires_graph_client():
    with pytest.raises(ValueError, match="graph_client cannot be None"):
        OutlookProvider(None)


# ----------------------------------------------------------------------
# Inbox messages
# ----------------------------------------------------------------------


def test_list_inbox_messages_returns_email_messages():
    client = FakeGraphClient()

    client.responses["/me/mailFolders/inbox/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-1",
                sender_email="alerts@Example.COM",
                sender_name="Example Alerts",
                subject="Important alert",
                body_preview="Something happened.",
                is_read=False,
                has_attachments=True,
            )
        ]
    }

    provider = OutlookProvider(client)

    messages = provider.list_inbox_messages(limit=10)

    assert len(messages) == 1

    message = messages[0]

    assert isinstance(message, EmailMessage)
    assert message.id == "outlook-1"
    assert message.sender_email == "alerts@example.com"
    assert message.sender_name == "Example Alerts"
    assert message.subject == "Important alert"
    assert message.body_preview == "Something happened."
    assert message.is_read is False
    assert message.has_attachments is True
    assert message.web_link == (
        "https://outlook.office.com/mail/inbox/id/message-1"
    )

    path, kwargs = client.get_calls[0]

    assert path == "/me/mailFolders/inbox/messages"
    assert kwargs["params"]["$top"] == 10
    assert kwargs["params"]["$orderby"] == "receivedDateTime desc"


def test_list_inbox_messages_with_non_positive_limit_returns_empty():
    client = FakeGraphClient()
    provider = OutlookProvider(client)

    assert provider.list_inbox_messages(limit=0) == []
    assert provider.list_inbox_messages(limit=-1) == []

    assert client.get_calls == []


# ----------------------------------------------------------------------
# Existing mailbox messages / pagination
# ----------------------------------------------------------------------


def test_list_existing_messages_returns_first_page():
    client = FakeGraphClient()

    client.responses["/me/messages"] = {
        "value": [
            graph_message(message_id="existing-1"),
            graph_message(message_id="existing-2"),
        ],
        "@odata.nextLink": (
            "https://graph.microsoft.com/v1.0/me/messages"
            "?$skiptoken=next-page"
        ),
    }

    provider = OutlookProvider(client)

    messages = provider.list_existing_messages(limit=2)

    assert [message.id for message in messages] == [
        "existing-1",
        "existing-2",
    ]

    path, kwargs = client.get_calls[0]

    assert path == "/me/messages"
    assert kwargs["params"]["$top"] == 2
    assert kwargs["params"]["$orderby"] == "receivedDateTime desc"
    assert "$filter" in kwargs["params"]
    assert "isDraft eq false" in kwargs["params"]["$filter"]


def test_list_existing_message_page_returns_next_link():
    client = FakeGraphClient()

    next_link = (
        "https://graph.microsoft.com/v1.0/me/messages"
        "?$skiptoken=abc123"
    )

    client.responses["/me/messages"] = {
        "value": [
            graph_message(message_id="existing-1"),
        ],
        "@odata.nextLink": next_link,
    }

    provider = OutlookProvider(client)

    messages, returned_next_link = provider.list_existing_message_page(
        limit=1
    )

    assert [message.id for message in messages] == ["existing-1"]
    assert returned_next_link == next_link


def test_list_existing_message_page_uses_next_link_directly():
    client = FakeGraphClient()

    next_link = (
        "https://graph.microsoft.com/v1.0/me/messages"
        "?$skiptoken=abc123"
    )

    client.responses[next_link] = {
        "value": [
            graph_message(message_id="existing-2"),
        ],
    }

    provider = OutlookProvider(client)

    messages, returned_next_link = provider.list_existing_message_page(
        limit=1,
        next_link=next_link,
    )

    assert [message.id for message in messages] == ["existing-2"]
    assert returned_next_link is None

    assert client.get_calls == [(next_link, {})]


def test_list_existing_message_page_with_non_positive_limit_returns_empty():
    client = FakeGraphClient()
    provider = OutlookProvider(client)

    messages, next_link = provider.list_existing_message_page(limit=0)

    assert messages == []
    assert next_link is None
    assert client.get_calls == []


def test_list_existing_message_page_rejects_invalid_next_link():
    client = FakeGraphClient()
    provider = OutlookProvider(client)

    with pytest.raises(ValueError, match="next_link must be a string or None"):
        provider.list_existing_message_page(
            limit=10,
            next_link=123,
        )


# ----------------------------------------------------------------------
# Folder listing
# ----------------------------------------------------------------------


def test_list_folders_returns_display_names():
    client = FakeGraphClient()

    client.responses["/me/mailFolders"] = {
        "value": [
            graph_folder(
                folder_id="inbox-id",
                display_name="Inbox",
            ),
            graph_folder(
                folder_id="work-id",
                display_name="Work",
            ),
            graph_folder(
                folder_id="personal-id",
                display_name="Personal",
            ),
        ]
    }

    provider = OutlookProvider(client)

    folders = provider.list_folders()

    assert folders == [
        "Inbox",
        "Work",
        "Personal",
    ]


def test_list_folders_ignores_invalid_folder_entries():
    client = FakeGraphClient()

    client.responses["/me/mailFolders"] = {
        "value": [
            graph_folder(
                folder_id="work-id",
                display_name="Work",
            ),
            {},
            {
                "id": "missing-name",
            },
            {
                "id": "empty-name",
                "displayName": "   ",
            },
        ]
    }

    provider = OutlookProvider(client)

    assert provider.list_folders() == ["Work"]


# ----------------------------------------------------------------------
# Folder creation
# ----------------------------------------------------------------------


def test_create_folder_creates_new_folder():
    client = FakeGraphClient()

    client.responses["/me/mailFolders"] = {
        "value": [
            graph_folder(
                folder_id="inbox-id",
                display_name="Inbox",
            )
        ]
    }

    def fake_post(path: str, **kwargs):
        client.post_calls.append((path, kwargs))

        if path == "/me/mailFolders":
            return {
                "id": "company-id",
                "displayName": "Example Company",
            }

        raise RuntimeError(f"Unexpected POST path: {path}")

    client.post = fake_post

    provider = OutlookProvider(client)

    created = provider.create_folder("Example Company")

    assert created == "Example Company"

    assert client.get_calls[0][0] == "/me/mailFolders"

    post_path, post_kwargs = client.post_calls[0]

    assert post_path == "/me/mailFolders"
    assert post_kwargs["json"] == {
        "displayName": "Example Company",
    }


def test_create_folder_returns_existing_folder_case_insensitively():
    client = FakeGraphClient()

    client.responses["/me/mailFolders"] = {
        "value": [
            graph_folder(
                folder_id="company-id",
                display_name="Example Company",
            )
        ]
    }

    provider = OutlookProvider(client)

    result = provider.create_folder("  example   company ")

    assert result == "Example Company"
    assert client.post_calls == []


def test_create_folder_rejects_empty_name():
    client = FakeGraphClient()
    provider = OutlookProvider(client)

    with pytest.raises(ValueError, match="folder_name cannot be empty"):
        provider.create_folder("   ")


def test_create_folder_rejects_dot_folder_names():
    client = FakeGraphClient()
    provider = OutlookProvider(client)

    with pytest.raises(ValueError, match="folder_name cannot be"):
        provider.create_folder(".")


# ----------------------------------------------------------------------
# Message movement
# ----------------------------------------------------------------------


def test_move_message_moves_message_to_destination_folder():
    client = FakeGraphClient()

    client.responses["/me/mailFolders"] = {
        "value": [
            graph_folder(
                folder_id="company-folder-id",
                display_name="Example Company",
            )
        ]
    }

    def fake_post(path: str, **kwargs):
        client.post_calls.append((path, kwargs))

        if path.endswith("/move"):
            return {}

        raise RuntimeError(f"Unexpected POST path: {path}")

    client.post = fake_post

    provider = OutlookProvider(client)

    provider.move_message(
        message_id="message-123",
        destination_folder="Example Company",
    )

    assert len(client.post_calls) == 1

    path, kwargs = client.post_calls[0]

    assert path == "/me/messages/message-123/move"
    assert kwargs["json"] == {
        "destinationId": "company-folder-id",
    }


def test_move_message_fails_when_destination_folder_does_not_exist():
    client = FakeGraphClient()

    client.responses["/me/mailFolders"] = {
        "value": []
    }

    provider = OutlookProvider(client)

    with pytest.raises(
        MailProviderError,
        match="does not exist",
    ):
        provider.move_message(
            message_id="message-123",
            destination_folder="Missing Folder",
        )

    assert client.post_calls == []


def test_move_message_rejects_empty_message_id():
    client = FakeGraphClient()
    provider = OutlookProvider(client)

    with pytest.raises(ValueError, match="message_id cannot be empty"):
        provider.move_message(
            message_id="   ",
            destination_folder="Work",
        )


# ----------------------------------------------------------------------
# Graph parsing
# ----------------------------------------------------------------------


def test_message_without_optional_fields_uses_safe_defaults():
    client = FakeGraphClient()

    client.responses["/me/mailFolders/inbox/messages"] = {
        "value": [
            {
                "id": "minimal-message",
                "from": {
                    "emailAddress": {
                        "address": "person@example.com",
                    }
                },
            }
        ]
    }

    provider = OutlookProvider(client)

    messages = provider.list_inbox_messages()

    assert len(messages) == 1

    message = messages[0]

    assert message.id == "minimal-message"
    assert message.sender_email == "person@example.com"
    assert message.sender_name is None
    assert message.subject == ""
    assert message.body_preview == ""
    assert message.received_at is None
    assert message.is_read is False
    assert message.has_attachments is False
    assert message.web_link is None


def test_message_datetime_is_parsed_with_timezone():
    client = FakeGraphClient()

    client.responses["/me/mailFolders/inbox/messages"] = {
        "value": [
            graph_message(
                received_date_time="2026-09-30T05:30:00Z"
            )
        ]
    }

    provider = OutlookProvider(client)

    message = provider.list_inbox_messages()[0]

    assert message.received_at is not None
    assert message.received_at.tzinfo == timezone.utc


def test_invalid_datetime_becomes_none():
    client = FakeGraphClient()

    client.responses["/me/mailFolders/inbox/messages"] = {
        "value": [
            graph_message(
                received_date_time="not-a-date"
            )
        ]
    }

    provider = OutlookProvider(client)

    message = provider.list_inbox_messages()[0]

    assert message.received_at is None


def test_missing_sender_causes_provider_error():
    client = FakeGraphClient()

    client.responses["/me/mailFolders/inbox/messages"] = {
        "value": [
            {
                "id": "invalid-message",
                "subject": "Broken message",
            }
        ]
    }

    provider = OutlookProvider(client)

    with pytest.raises(
        MailProviderError,
        match="Microsoft Graph returned an invalid message",
    ):
        provider.list_inbox_messages()


# ----------------------------------------------------------------------
# Graph client failures
# ----------------------------------------------------------------------


def test_graph_get_failure_is_wrapped_as_provider_error():
    class FailingClient:
        def get(self, path: str, **kwargs):
            raise RuntimeError("network unavailable")

        def post(self, path: str, **kwargs):
            raise RuntimeError("network unavailable")

    provider = OutlookProvider(FailingClient())

    with pytest.raises(
        MailProviderError,
        match="Microsoft Graph GET failed",
    ):
        provider.list_folders()


def test_graph_post_failure_is_wrapped_as_provider_error():
    class FailingClient:
        def get(self, path: str, **kwargs):
            return {
                "value": [
                    graph_folder(
                        folder_id="work-id",
                        display_name="Work",
                    )
                ]
            }

        def post(self, path: str, **kwargs):
            raise RuntimeError("network unavailable")

    provider = OutlookProvider(FailingClient())

    with pytest.raises(
        MailProviderError,
        match="Microsoft Graph POST failed",
    ):
        provider.move_message(
            message_id="message-1",
            destination_folder="Work",
        )


# ----------------------------------------------------------------------
# Response normalization
# ----------------------------------------------------------------------


def test_graph_response_object_with_json_method_is_supported():
    class FakeResponse:
        def json(self):
            return {
                "value": [
                    graph_folder(
                        folder_id="work-id",
                        display_name="Work",
                    )
                ]
            }

    class FakeClient:
        def get(self, path: str, **kwargs):
            return FakeResponse()

        def post(self, path: str, **kwargs):
            return FakeResponse()

    provider = OutlookProvider(FakeClient())

    assert provider.list_folders() == ["Work"]


def test_unsupported_graph_response_type_raises_provider_error():
    class FakeClient:
        def get(self, path: str, **kwargs):
            return "not-a-response"

        def post(self, path: str, **kwargs):
            return "not-a-response"

    provider = OutlookProvider(FakeClient())

    with pytest.raises(
        MailProviderError,
        match="unsupported response type",
    ):
        provider.list_folders()