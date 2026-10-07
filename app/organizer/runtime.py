"""
MailPilot — Organizer Runtime Composition

Builds the existing Outlook + classifier + organizer pipeline.

This module contains runtime wiring only. It does not implement
classification or mailbox organization logic itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.learning import LearningManager
from app.classifier.providers.ollama import OllamaAIProvider
from app.classifier.resolver import OrganizationResolver
from app.organizer.engine import OrganizerEngine, OrganizerResult
from app.organizer.processor import OrganizerProcessor
from app.mail.outlook.provider import OutlookProvider
from database.manager import DatabaseManager


BASE_DIR = Path(__file__).resolve().parents[2]


def run_outlook_organization(
    *,
    access_token: str,
    dry_run: bool = True,
    limit: int = 50,
    enable_learning: bool = False,
) -> list[OrganizerResult]:
    """
    Run MailPilot's existing organizer pipeline against Outlook.

    Args:
        access_token:
            Valid Microsoft Graph access token for the connected
            Outlook account.

        dry_run:
            When True, no Outlook folders are created and no messages
            are moved.

        limit:
            Maximum number of inbox messages to process.

        enable_learning:
            When True, HIGH-confidence classifications may be learned
            into the organization mapping database.

            This defaults to False because a DRY RUN should not mutate
            organization mappings.

    Returns:
        OrganizerResult objects produced by OrganizerProcessor.
    """

    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError(
            "Outlook access token cannot be empty."
        )

    if limit <= 0:
        return []

    database = DatabaseManager(
        database_path=BASE_DIR / "database" / "mailpilot.db",
        schema_path=BASE_DIR / "database" / "schema.sql",
    )

    database.initialize()

    # MicrosoftGraphClient is intentionally imported here to avoid
    # creating a second authentication implementation and to avoid
    # circular imports with app.web.app.
    from app.web.app import MicrosoftGraphClient

    graph_client = MicrosoftGraphClient(
        access_token.strip()
    )

    provider = OutlookProvider(
        graph_client=graph_client
    )

    resolver = OrganizationResolver(
        database=database
    )

    ai_provider = OllamaAIProvider()

    learning_manager = None

    if enable_learning:
        learning_manager = LearningManager(
            database=database
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

    return processor.process(
        dry_run=dry_run,
        limit=limit,
    )