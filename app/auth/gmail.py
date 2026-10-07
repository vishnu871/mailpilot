"""
MailPilot Gmail OAuth authentication.

Handles local OAuth authentication for a user's Gmail account.
The authenticated credentials are stored locally so the user does
not need to authorize MailPilot every time it runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


class GmailAuthenticationError(Exception):
    """Raised when Gmail authentication fails."""


class GmailAuthenticator:
    """Authenticate MailPilot against a user's Gmail account."""

    DEFAULT_SCOPES = (
        "https://www.googleapis.com/auth/gmail.modify",
    )

    def __init__(
        self,
        credentials_path: str | Path = "credentials.json",
        token_path: str | Path = "token.json",
        scopes: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.scopes = scopes or self.DEFAULT_SCOPES

    def authenticate(self) -> Credentials:
        """
        Authenticate the user and return Gmail OAuth credentials.

        Existing valid credentials are reused. Expired credentials with
        a refresh token are refreshed automatically. Otherwise, a local
        browser-based OAuth flow is started.
        """

        credentials = self._load_existing_credentials()

        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                self._save_credentials(credentials)
                return credentials
            except Exception as exc:
                raise GmailAuthenticationError(
                    f"Failed to refresh Gmail credentials: {exc}"
                ) from exc

        if not self.credentials_path.exists():
            raise GmailAuthenticationError(
                "Gmail OAuth credentials file was not found: "
                f"{self.credentials_path}"
            )

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path),
                self.scopes,
            )

            credentials = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
            )

            self._save_credentials(credentials)

            return credentials

        except Exception as exc:
            raise GmailAuthenticationError(
                f"Gmail OAuth authentication failed: {exc}"
            ) from exc

    def _load_existing_credentials(self) -> Optional[Credentials]:
        """Load previously saved OAuth credentials if available."""

        if not self.token_path.exists():
            return None

        try:
            return Credentials.from_authorized_user_file(
                str(self.token_path),
                self.scopes,
            )
        except Exception as exc:
            raise GmailAuthenticationError(
                f"Failed to load Gmail token file: {exc}"
            ) from exc

    def _save_credentials(self, credentials: Credentials) -> None:
        """Persist OAuth credentials locally."""

        try:
            self.token_path.write_text(
                credentials.to_json(),
                encoding="utf-8",
            )
        except Exception as exc:
            raise GmailAuthenticationError(
                f"Failed to save Gmail token file: {exc}"
            ) from exc