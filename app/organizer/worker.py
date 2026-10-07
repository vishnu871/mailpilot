"""
MailPilot automatic background organizer worker.

This worker reuses MailPilot's existing:

- UserStore
- GmailProvider / OutlookProvider
- OrganizerProcessor
- OrganizerEngine
- classifier
- encrypted OAuth credentials

It never starts a new interactive OAuth flow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
import ssl

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.auth.outlook_web import OutlookWebAuthenticator
from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.providers.ollama import OllamaAIProvider
from app.classifier.resolver import OrganizationResolver
from app.mail.gmail.provider import GmailProvider
from app.mail.outlook.provider import OutlookProvider
from app.organizer.engine import OrganizerEngine
from app.organizer.processor import OrganizerProcessor
from database.manager import DatabaseManager
from database.user_store import UserStore


logger = logging.getLogger("mailpilot.organizer.worker")


class GmailReauthenticationRequired(RuntimeError):
    """
    Raised when the stored Gmail refresh token is no longer valid.

    This is not a worker-fatal error. The affected Gmail account needs
    to reconnect through the existing MailPilot Gmail OAuth flow.
    """


class TLS12HTTPAdapter(HTTPAdapter):
    """Use TLS 1.2 for Microsoft HTTPS calls on this Windows runtime."""

    def init_poolmanager(
        self,
        connections,
        maxsize,
        block=False,
        **pool_kwargs,
    ):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2

        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=context,
            **pool_kwargs,
        )

    def proxy_manager_for(
        self,
        proxy,
        **proxy_kwargs,
    ):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2

        proxy_kwargs["ssl_context"] = context

        return super().proxy_manager_for(
            proxy,
            **proxy_kwargs,
        )


BASE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
    )
)


DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_BATCH_SIZE = 50

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
)


def _database() -> DatabaseManager:
    """Create and initialize the existing organizer database."""

    database = DatabaseManager(
        database_path=os.path.join(
            BASE_DIR,
            "database",
            "mailpilot.db",
        ),
        schema_path=os.path.join(
            BASE_DIR,
            "database",
            "schema.sql",
        ),
    )

    database.initialize()

    return database


def _build_processor(
    provider: Any,
    database: DatabaseManager,
) -> OrganizerProcessor:
    """Build the existing MailPilot organization pipeline."""

    resolver = OrganizationResolver(
        database=database,
    )

    ai_provider = OllamaAIProvider()

    classifier = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=ai_provider,
        learning_manager=None,
    )

    engine = OrganizerEngine(
        provider=provider,
        classifier=classifier,
    )

    return OrganizerProcessor(
        provider=provider,
        engine=engine,
        database=database,
    )


def _load_gmail_credentials(
    user_store: UserStore,
    user_id: str,
) -> Credentials:
    """
    Load Gmail credentials from encrypted UserStore storage.

    Existing refresh tokens are used when the access token has expired.

    No interactive OAuth flow is started.

    If Google reports that the refresh token has expired or been revoked,
    a controlled GmailReauthenticationRequired exception is raised instead
    of producing a full traceback.
    """

    credentials = user_store.get_credentials(
        user_id=user_id,
        scopes=GMAIL_SCOPES,
    )

    if credentials is None:
        raise GmailReauthenticationRequired(
            "No stored Gmail OAuth credentials found."
        )

    if credentials.valid:
        return credentials

    if not credentials.expired:
        raise GmailReauthenticationRequired(
            "Stored Gmail credentials are invalid."
        )

    if not credentials.refresh_token:
        raise GmailReauthenticationRequired(
            "Stored Gmail credentials have no refresh token."
        )

    try:
        credentials.refresh(
            GoogleRequest(),
        )

    except RefreshError as exc:
        error_text = str(exc).lower()

        if (
            "invalid_grant" in error_text
            or "expired or revoked" in error_text
        ):
            raise GmailReauthenticationRequired(
                "The stored Gmail refresh token has expired or "
                "been revoked. Reconnect Gmail through MailPilot."
            ) from exc

        raise GmailReauthenticationRequired(
            "Gmail OAuth refresh failed. Reconnect Gmail through "
            "MailPilot."
        ) from exc

    user_store.save_credentials(
        user_id=user_id,
        provider="gmail",
        credentials=credentials,
    )

    return credentials


def _run_gmail(
    user_store: UserStore,
    user: dict[str, Any],
    limit: int,
) -> int:
    """Run the existing live organizer against one Gmail account."""

    user_id = str(user["id"])

    credentials = _load_gmail_credentials(
        user_store=user_store,
        user_id=user_id,
    )

    service = build(
        "gmail",
        "v1",
        credentials=credentials,
        cache_discovery=False,
    )

    provider = GmailProvider(
        service=service,
    )

    database = _database()

    processor = _build_processor(
        provider=provider,
        database=database,
    )

    results = processor.process(
        dry_run=False,
        limit=limit,
    )

    return len(results)


def _outlook_token_is_expired(
    credentials: dict[str, Any],
) -> bool:
    """
    Determine whether an Outlook access token should be refreshed.

    Microsoft token responses normally contain expires_in. If the stored
    response also has a timestamp, use it. Otherwise an existing access
    token with expires_in but no timestamp is refreshed on the first
    worker cycle.
    """

    access_token = str(
        credentials.get("access_token") or ""
    ).strip()

    if not access_token:
        return True

    expires_at = credentials.get("expires_at")

    if expires_at is not None:
        try:
            return float(expires_at) <= time.time() + 60
        except (TypeError, ValueError):
            pass

    issued_at = credentials.get("issued_at")
    expires_in = credentials.get("expires_in")

    if issued_at is not None and expires_in is not None:
        try:
            return (
                float(issued_at)
                + float(expires_in)
                <= time.time() + 60
            )
        except (TypeError, ValueError):
            pass

    if expires_in is not None:
        return True

    return False


def _refresh_outlook_credentials(
    user_store: UserStore,
    user_id: str,
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """
    Refresh an Outlook token using the existing refresh token.

    The existing refresh token is preserved if Microsoft does not return
    a replacement refresh token.
    """

    if not _outlook_token_is_expired(credentials):
        return credentials

    refresh_token = str(
        credentials.get("refresh_token") or ""
    ).strip()

    if not refresh_token:
        raise RuntimeError(
            "Stored Outlook credentials have expired and contain "
            "no refresh token."
        )

    authenticator = OutlookWebAuthenticator()

    token_endpoint = (
        f"{authenticator.authority.rstrip('/')}"
        "/oauth2/v2.0/token"
    )

    session = requests.Session()
    session.mount(
        "https://",
        TLS12HTTPAdapter(),
    )

    response = session.post(
        token_endpoint,
        data={
            "client_id": authenticator.client_id,
            "client_secret": authenticator.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(authenticator.scopes),
        },
        headers={
            "Accept": "application/json",
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
        },
        timeout=30,
    )

    try:
        token_result = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Microsoft returned an invalid token refresh response."
        ) from exc

    if not isinstance(token_result, dict):
        raise RuntimeError(
            "Microsoft returned an invalid token refresh response."
        )

    if "error" in token_result:
        error = token_result.get(
            "error",
            "unknown_error",
        )

        description = token_result.get(
            "error_description",
            "No error description was provided.",
        )

        raise RuntimeError(
            "Microsoft Outlook token refresh failed: "
            f"{error}: {description}"
        )

    if not response.ok:
        raise RuntimeError(
            "Microsoft Outlook token refresh failed with "
            f"HTTP {response.status_code}."
        )

    new_access_token = str(
        token_result.get("access_token") or ""
    ).strip()

    if not new_access_token:
        raise RuntimeError(
            "Microsoft did not return a refreshed access token."
        )

    merged = dict(credentials)
    merged.update(token_result)

    if not merged.get("refresh_token"):
        merged["refresh_token"] = refresh_token

    merged["issued_at"] = time.time()

    user_store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=merged,
    )

    return merged


def _run_outlook(
    user_store: UserStore,
    user: dict[str, Any],
    limit: int,
) -> int:
    """Run the existing live organizer against one Outlook account."""

    user_id = str(user["id"])

    credentials = user_store.get_provider_credentials(
        user_id=user_id,
        provider="outlook",
    )

    if not credentials:
        raise RuntimeError(
            "No stored Outlook OAuth credentials found."
        )

    credentials = _refresh_outlook_credentials(
        user_store=user_store,
        user_id=user_id,
        credentials=credentials,
    )

    access_token = str(
        credentials.get("access_token") or ""
    ).strip()

    if not access_token:
        raise RuntimeError(
            "Stored Outlook credentials have no access token."
        )

    database = _database()

    # Imported here intentionally to avoid a circular import during
    # application startup.
    from app.web.app import MicrosoftGraphClient

    graph_client = MicrosoftGraphClient(
        access_token,
    )

    provider = OutlookProvider(
        graph_client=graph_client,
    )

    processor = _build_processor(
        provider=provider,
        database=database,
    )

    results = processor.process(
        dry_run=False,
        limit=limit,
    )

    return len(results)


class OrganizerWorker:
    """Continuously organize all connected MailPilot mailboxes."""

    def __init__(
        self,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.interval_seconds = max(
            60,
            int(interval_seconds),
        )

        self.batch_size = max(
            1,
            int(batch_size),
        )

        self.user_store = UserStore()
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Request a graceful worker shutdown."""

        self._stop_event.set()

    def run_once(self) -> None:
        """Process one cycle for every connected provider account."""

        users = self.user_store.list_connected_users()

        for user in users:
            provider = str(
                user.get("provider") or ""
            ).strip().lower()

            email = str(
                user.get("email") or ""
            ).strip()

            if provider not in {
                "gmail",
                "outlook",
            }:
                continue

            try:
                logger.warning(
                    "Starting automatic organization: "
                    "%s / %s",
                    provider,
                    email,
                )

                if provider == "gmail":
                    processed = _run_gmail(
                        user_store=self.user_store,
                        user=user,
                        limit=self.batch_size,
                    )

                else:
                    processed = _run_outlook(
                        user_store=self.user_store,
                        user=user,
                        limit=self.batch_size,
                    )

                logger.warning(
                    "Automatic organization completed: "
                    "%s / %s / %d messages processed",
                    provider,
                    email,
                    processed,
                )

            except GmailReauthenticationRequired as exc:
                logger.warning(
                    "Automatic organization skipped: "
                    "%s / %s — %s",
                    provider,
                    email,
                    exc,
                )

            except Exception as exc:
                # One broken mailbox must never stop other connected
                # accounts from being processed.
                logger.error(
                    "Automatic organization failed: "
                    "%s / %s — %s",
                    provider,
                    email,
                    exc,
                    exc_info=False,
                )

    async def run_forever(self) -> None:
        """Run organization repeatedly until stop() is called."""

        logger.warning(
            "MailPilot automatic organizer started "
            "(interval=%ss, batch=%s)",
            self.interval_seconds,
            self.batch_size,
        )

        while not self._stop_event.is_set():
            await asyncio.to_thread(
                self.run_once,
            )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )

            except asyncio.TimeoutError:
                continue

        logger.warning(
            "MailPilot automatic organizer stopped."
        )