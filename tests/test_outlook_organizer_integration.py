"""
Integration tests for OutlookProvider + OrganizerProcessor.

These tests use a fake Microsoft Graph client, so they do not require
Microsoft/Azure credentials or network access.
"""

from __future__ import annotations

from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.learning import LearningManager
from app.classifier.providers.ai_provider import AIClassificationResponse
from app.classifier.providers.mock import MockAIProvider
from app.classifier.resolver import OrganizationResolver
from app.mail.outlook.provider import OutlookProvider
from app.organizer.engine import OrganizerEngine
from app.organizer.processor import OrganizerProcessor
from database.manager import DatabaseManager


class FakeGraphClient:
    """Minimal in-memory Microsoft Graph client."""

    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

        self.page_responses: dict[str, dict] = {}
        self.folder_response: dict = {
            "value": []
        }

    def get(self, path: str, **kwargs):
        self.get_calls.append((path, kwargs))

        if path == "/me/mailFolders":
            return self.folder_response

        if path in self.page_responses:
            return self.page_responses[path]

        raise RuntimeError(
            f"No fake GET response configured for: {path}"
        )

    def post(self, path: str, **kwargs):
        self.post_calls.append((path, kwargs))

        if path == "/me/mailFolders":
            folder_name = kwargs["json"]["displayName"]

            folder = {
                "id": f"folder-{len(self.post_calls)}",
                "displayName": folder_name,
            }

            existing = self.folder_response.setdefault(
                "value",
                [],
            )

            existing.append(folder)

            return folder

        if path.endswith("/move"):
            return {}

        raise RuntimeError(
            f"No fake POST response configured for: {path}"
        )


def graph_message(
    *,
    message_id: str,
    sender_email: str,
    sender_name: str,
    subject: str,
) -> dict:
    """Build a Microsoft Graph message payload."""

    return {
        "id": message_id,
        "subject": subject,
        "bodyPreview": "Test Outlook message.",
        "receivedDateTime": "2026-09-30T05:30:00Z",
        "isRead": False,
        "hasAttachments": False,
        "webLink": (
            "https://outlook.office.com/mail/inbox/"
            f"id/{message_id}"
        ),
        "from": {
            "emailAddress": {
                "name": sender_name,
                "address": sender_email,
            }
        },
    }


def create_system(
    tmp_path,
    graph_client: FakeGraphClient,
    ai_response: AIClassificationResponse,
):
    """Create the real OutlookProvider + organizer pipeline."""

    database = DatabaseManager(
        database_path=str(
            tmp_path / "mailpilot.db"
        ),
    )

    database.initialize()

    resolver = OrganizationResolver(
        database
    )

    learning_manager = LearningManager(
        database
    )

    provider = OutlookProvider(
        graph_client
    )

    ai_provider = MockAIProvider(
        default_response=ai_response
    )

    classifier = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=ai_provider,
        learning_manager=learning_manager,
    )

    engine = OrganizerEngine(
        provider=provider,
        classifier=classifier,
    )

    processor = OrganizerProcessor(
        provider=provider,
        engine=engine,
        database=database,
    )

    return (
        database,
        provider,
        ai_provider,
        processor,
    )


def test_outlook_provider_and_processor_work_together(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "inbox-folder",
                "displayName": "Inbox",
            }
        ]
    }

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="person@example.com",
                sender_name="Example Person",
                subject="Example email",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Example Company",
        domain="example.com",
        folder_name="Example Company",
        confidence="HIGH",
        reason="AI identified the organization.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    results = processor.process_existing_paginated(
        dry_run=False,
        batch_size=10,
    )

    assert len(results) == 1

    result = results[0]

    assert result.action == "moved"
    assert result.decision.should_move is True
    assert result.decision.organization == "Example Company"
    assert result.decision.destination_folder == "Example Company"

    assert ai_provider.classification_count == 1

    assert len(client.post_calls) == 2

    create_folder_call = client.post_calls[0]

    assert create_folder_call[0] == "/me/mailFolders"
    assert create_folder_call[1]["json"] == {
        "displayName": "Example Company"
    }

    move_call = client.post_calls[1]

    assert move_call[0] == (
        "/me/messages/outlook-message-1/move"
    )

    assert move_call[1]["json"] == {
        "destinationId": "folder-1"
    }

    history = database.get_processing_history(
        message_id="outlook-message-1",
        limit=10,
    )

    assert len(history) == 1

    assert history[0].action == "moved"
    assert history[0].status == "success"
    assert history[0].destination_folder == "Example Company"


def test_outlook_paginated_processing_uses_multiple_pages(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "inbox-folder",
                "displayName": "Inbox",
            }
        ]
    }

    first_next_link = (
        "https://graph.microsoft.com/v1.0/me/messages"
        "?$skiptoken=page-2"
    )

    second_next_link = (
        "https://graph.microsoft.com/v1.0/me/messages"
        "?$skiptoken=page-3"
    )

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="one@example.com",
                sender_name="One",
                subject="First email",
            )
        ],
        "@odata.nextLink": first_next_link,
    }

    client.page_responses[first_next_link] = {
        "value": [
            graph_message(
                message_id="outlook-message-2",
                sender_email="two@example.com",
                sender_name="Two",
                subject="Second email",
            )
        ],
        "@odata.nextLink": second_next_link,
    }

    client.page_responses[second_next_link] = {
        "value": [
            graph_message(
                message_id="outlook-message-3",
                sender_email="three@example.com",
                sender_name="Three",
                subject="Third email",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Example Company",
        domain="example.com",
        folder_name="Example Company",
        confidence="HIGH",
        reason="AI identified the organization.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    progress_calls = []

    def progress_callback(
        batch_number,
        fetched_count,
        results,
    ):
        progress_calls.append(
            (
                batch_number,
                fetched_count,
                len(results),
            )
        )

    results = processor.process_existing_paginated(
        dry_run=True,
        batch_size=1,
        progress_callback=progress_callback,
    )

    assert len(results) == 3

    assert [
        result.decision.message_id
        for result in results
    ] == [
        "outlook-message-1",
        "outlook-message-2",
        "outlook-message-3",
    ]

    assert all(
        result.action == "dry_run_would_move"
        for result in results
    )

    assert progress_calls == [
        (1, 1, 1),
        (2, 1, 1),
        (3, 1, 1),
    ]

    message_get_calls = [
        call
        for call in client.get_calls
        if call[0] != "/me/mailFolders"
    ]

    assert [
        call[0]
        for call in message_get_calls
    ] == [
        "/me/messages",
        first_next_link,
        second_next_link,
    ]

    assert client.post_calls == []


def test_outlook_existing_folder_is_reused(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "existing-company-folder",
                "displayName": "Example Company",
            }
        ]
    }

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="person@example.com",
                sender_name="Example Person",
                subject="Existing folder test",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Example Company",
        domain="example.com",
        folder_name="Example Company",
        confidence="HIGH",
        reason="AI identified the organization.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    results = processor.process_existing_paginated(
        dry_run=False,
        batch_size=10,
    )

    assert len(results) == 1
    assert results[0].action == "moved"

    assert len(client.post_calls) == 1

    move_call = client.post_calls[0]

    assert move_call[0] == (
        "/me/messages/outlook-message-1/move"
    )

    assert move_call[1]["json"] == {
        "destinationId": "existing-company-folder"
    }

    assert ai_provider.classification_count == 1


def test_outlook_dry_run_does_not_create_or_move(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "inbox-folder",
                "displayName": "Inbox",
            }
        ]
    }

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="person@example.com",
                sender_name="Example Person",
                subject="Dry run test",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Example Company",
        domain="example.com",
        folder_name="Example Company",
        confidence="HIGH",
        reason="AI identified the organization.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    results = processor.process_existing_paginated(
        dry_run=True,
        batch_size=10,
    )

    assert len(results) == 1
    assert results[0].action == "dry_run_would_move"

    assert client.post_calls == []
    assert ai_provider.classification_count == 1


def test_outlook_medium_confidence_is_left_untouched(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "inbox-folder",
                "displayName": "Inbox",
            }
        ]
    }

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="person@example.com",
                sender_name="Example Person",
                subject="Medium confidence test",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Maybe Company",
        domain="example.com",
        folder_name="Maybe Company",
        confidence="MEDIUM",
        reason="AI has moderate confidence.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    results = processor.process_existing_paginated(
        dry_run=False,
        batch_size=10,
    )

    assert len(results) == 1

    result = results[0]

    assert result.action == "left_untouched"
    assert result.decision.should_move is False

    assert client.post_calls == []
    assert ai_provider.classification_count == 1


def test_outlook_successful_processing_is_not_repeated(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "inbox-folder",
                "displayName": "Inbox",
            },
            {
                "id": "company-folder",
                "displayName": "Example Company",
            },
        ]
    }

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="person@example.com",
                sender_name="Example Person",
                subject="Duplicate processing test",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Example Company",
        domain="example.com",
        folder_name="Example Company",
        confidence="HIGH",
        reason="AI identified the organization.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    first_results = processor.process_existing_paginated(
        dry_run=False,
        batch_size=10,
    )

    assert len(first_results) == 1
    assert first_results[0].action == "moved"

    first_post_count = len(client.post_calls)
    first_classification_count = ai_provider.classification_count

    second_results = processor.process_existing_paginated(
        dry_run=False,
        batch_size=10,
    )

    assert second_results == []

    assert len(client.post_calls) == first_post_count
    assert ai_provider.classification_count == (
        first_classification_count
    )


def test_outlook_processor_records_history_for_dry_run(
    tmp_path,
):
    client = FakeGraphClient()

    client.folder_response = {
        "value": [
            {
                "id": "inbox-folder",
                "displayName": "Inbox",
            }
        ]
    }

    client.page_responses["/me/messages"] = {
        "value": [
            graph_message(
                message_id="outlook-message-1",
                sender_email="person@example.com",
                sender_name="Example Person",
                subject="History test",
            )
        ]
    }

    ai_response = AIClassificationResponse(
        organization="Example Company",
        domain="example.com",
        folder_name="Example Company",
        confidence="HIGH",
        reason="AI identified the organization.",
    )

    (
        database,
        provider,
        ai_provider,
        processor,
    ) = create_system(
        tmp_path=tmp_path,
        graph_client=client,
        ai_response=ai_response,
    )

    results = processor.process_existing_paginated(
        dry_run=True,
        batch_size=10,
    )

    assert len(results) == 1

    history = database.get_processing_history(
        message_id="outlook-message-1",
        limit=10,
    )

    assert len(history) == 1

    assert history[0].action == "dry_run_would_move"
    assert history[0].status == "success"
    assert history[0].sender_email == "person@example.com"
    assert history[0].organization == "Example Company"
    assert history[0].domain == "example.com"
    assert history[0].destination_folder == "Example Company"