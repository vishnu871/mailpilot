"""
MailPilot Gmail Web OAuth authentication.

Handles Google OAuth for the MailPilot web application.

The OAuth state and PKCE code verifier are generated during the
authorization request and the code verifier is explicitly restored
during the callback token exchange.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow


BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / ".env")


class GmailWebAuthenticationError(Exception):
    """Raised when Gmail web OAuth configuration or authentication fails."""


class GmailWebAuthenticator:
    """Handle Gmail OAuth for the MailPilot web application."""

    DEFAULT_SCOPES = (
        "https://www.googleapis.com/auth/gmail.modify",
    )

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        scopes: tuple[str, ...] | None = None,
    ) -> None:
        self.client_id = client_id or os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = client_secret or os.getenv(
            "GOOGLE_CLIENT_SECRET"
        )
        self.redirect_uri = redirect_uri or os.getenv(
            "GOOGLE_REDIRECT_URI"
        )
        self.scopes = scopes or self.DEFAULT_SCOPES

        self._validate_configuration()
        self._configure_local_oauth_transport()

    def _validate_configuration(self) -> None:
        """Validate the Google web OAuth configuration."""

        missing: list[str] = []

        if not self.client_id:
            missing.append("GOOGLE_CLIENT_ID")

        if not self.client_secret:
            missing.append("GOOGLE_CLIENT_SECRET")

        if not self.redirect_uri:
            missing.append("GOOGLE_REDIRECT_URI")

        if missing:
            raise GmailWebAuthenticationError(
                "Missing Gmail web OAuth configuration: "
                + ", ".join(missing)
            )

    def _configure_local_oauth_transport(self) -> None:
        """
        Allow HTTP only for localhost development.

        Production deployment must use HTTPS and must not rely on this
        local development exception.
        """

        if self.redirect_uri and self.redirect_uri.startswith(
            "http://localhost:"
        ):
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    def _client_config(self) -> dict[str, Any]:
        """Build the Google OAuth client configuration."""

        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self.redirect_uri],
            }
        }

    def create_authorization_url(
        self,
        state: str,
    ) -> tuple[str, str]:
        """
        Create the Google OAuth authorization URL.

        Returns:
            A tuple containing:
                - authorization URL
                - PKCE code verifier

        The code verifier must be preserved until the callback.
        """

        try:
            flow = Flow.from_client_config(
                self._client_config(),
                scopes=list(self.scopes),
                state=state,
            )

            flow.redirect_uri = self.redirect_uri

            authorization_url, _ = flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )

            code_verifier = flow.code_verifier

            if not code_verifier:
                raise GmailWebAuthenticationError(
                    "Google OAuth did not generate a PKCE code verifier."
                )

            return authorization_url, code_verifier

        except GmailWebAuthenticationError:
            raise

        except Exception as exc:
            raise GmailWebAuthenticationError(
                f"Failed to create Gmail authorization URL: {exc}"
            ) from exc

    def exchange_code_for_credentials(
        self,
        authorization_response: str,
        state: str,
        code_verifier: str,
    ) -> Any:
        """
        Exchange Google's authorization response for credentials.

        The original PKCE code verifier generated during the
        authorization request is restored before exchanging the
        authorization code.
        """

        try:
            flow = Flow.from_client_config(
                self._client_config(),
                scopes=list(self.scopes),
                state=state,
            )

            flow.redirect_uri = self.redirect_uri

            flow.code_verifier = code_verifier

            flow.fetch_token(
                authorization_response=authorization_response
            )

            return flow.credentials

        except Exception as exc:
            raise GmailWebAuthenticationError(
                f"Failed to exchange Gmail authorization code: {exc}"
            ) from exc