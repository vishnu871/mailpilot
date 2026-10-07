"""
MailPilot Outlook Web OAuth authentication.

Handles Microsoft identity platform OAuth for the MailPilot
web application.

The authorization state and PKCE code verifier are generated
during the authorization request and must be preserved until
the callback token exchange.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
from typing import Any
from urllib.parse import urlencode

import msal
from dotenv import load_dotenv


BASE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
    )
)

load_dotenv(
    os.path.join(
        BASE_DIR,
        ".env",
    )
)


class OutlookWebAuthenticationError(Exception):
    """Raised when Outlook web OAuth configuration or flow fails."""


class OutlookWebAuthenticator:
    """
    Microsoft identity platform OAuth helper.

    Uses Authorization Code + PKCE for the browser authorization
    request and performs the token exchange against Microsoft's
    OAuth token endpoint.

    The token exchange uses Windows curl.exe because the Python
    HTTPS transport on this Windows environment can complete normal
    Microsoft GET requests but times out during the Microsoft token
    POST TLS handshake.
    """

    DEFAULT_SCOPES = (
        "User.Read",
        "Mail.ReadWrite",
        "offline_access",
    )

    DEFAULT_AUTHORITY = (
        "https://login.microsoftonline.com/common"
    )

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
        authority: str | None = None,
        scopes: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self.client_id = (
            client_id
            or os.getenv("MICROSOFT_CLIENT_ID")
        )

        self.client_secret = (
            client_secret
            or os.getenv("MICROSOFT_CLIENT_SECRET")
        )

        self.redirect_uri = (
            redirect_uri
            or os.getenv("MICROSOFT_REDIRECT_URI")
        )

        self.authority = (
            authority
            or os.getenv(
                "MICROSOFT_AUTHORITY",
                self.DEFAULT_AUTHORITY,
            )
        )

        self.scopes = (
            tuple(scopes)
            if scopes is not None
            else self.DEFAULT_SCOPES
        )

        self._validate_configuration()

    def _validate_configuration(self) -> None:
        """Validate required Microsoft OAuth configuration."""

        missing: list[str] = []

        if not self.client_id:
            missing.append("MICROSOFT_CLIENT_ID")

        if not self.client_secret:
            missing.append("MICROSOFT_CLIENT_SECRET")

        if not self.redirect_uri:
            missing.append("MICROSOFT_REDIRECT_URI")

        if missing:
            raise OutlookWebAuthenticationError(
                "Missing Outlook web OAuth configuration: "
                + ", ".join(missing)
            )

    @staticmethod
    def _generate_code_verifier() -> str:
        """Generate a high-entropy PKCE code verifier."""
        return secrets.token_urlsafe(64)

    @staticmethod
    def _create_code_challenge(
        code_verifier: str,
    ) -> str:
        """Generate the S256 PKCE code challenge."""

        digest = hashlib.sha256(
            code_verifier.encode("ascii")
        ).digest()

        return base64.urlsafe_b64encode(
            digest
        ).rstrip(b"=").decode("ascii")

    def _build_msal_app(
        self,
    ) -> msal.ConfidentialClientApplication:
        """Build an MSAL confidential client."""

        return msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )

    def create_authorization_url(
        self,
        state: str | None = None,
    ) -> tuple[str, str]:
        """
        Create Microsoft's authorization URL and PKCE verifier.

        Returns:
            Tuple containing:
            - authorization URL
            - PKCE code verifier
        """

        if not state:
            state = secrets.token_urlsafe(32)

        code_verifier = self._generate_code_verifier()

        code_challenge = self._create_code_challenge(
            code_verifier
        )

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        authorization_url = (
            f"{self.authority.rstrip('/')}"
            f"/oauth2/v2.0/authorize?"
            f"{urlencode(params)}"
        )

        return (
            authorization_url,
            code_verifier,
        )

    def exchange_code_for_credentials(
        self,
        authorization_code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """
        Exchange the Microsoft authorization code for tokens.

        Uses Windows curl.exe for the token POST.

        The form body is passed to curl through stdin so sensitive
        OAuth values are not exposed as command-line arguments.
        """

        if not authorization_code:
            raise OutlookWebAuthenticationError(
                "Microsoft OAuth callback did not contain "
                "an authorization code."
            )

        if not code_verifier:
            raise OutlookWebAuthenticationError(
                "Microsoft OAuth PKCE code verifier is missing."
            )

        token_endpoint = (
            f"{self.authority.rstrip('/')}"
            f"/oauth2/v2.0/token"
        )

        token_data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "code_verifier": code_verifier,
        }

        encoded_body = urlencode(token_data).encode("utf-8")

        command = [
            "curl.exe",
            "-4",
            "-sS",
            "--fail-with-body",
            "--http1.1",
            "--max-time",
            "30",
            "-X",
            "POST",
            token_endpoint,
            "-H",
            "Accept: application/json",
            "-H",
            "Content-Type: application/x-www-form-urlencoded",
            "--data-binary",
            "@-",
        ]

        try:
            completed = subprocess.run(
                command,
                input=encoded_body,
                capture_output=True,
                timeout=40,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OutlookWebAuthenticationError(
                "Windows curl.exe was not found. "
                "MailPilot requires curl.exe for the "
                "Microsoft Outlook token exchange."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OutlookWebAuthenticationError(
                "Microsoft token endpoint request timed out."
            ) from exc
        except OSError as exc:
            raise OutlookWebAuthenticationError(
                "Failed to start Windows curl.exe for the "
                f"Microsoft token exchange: {exc}"
            ) from exc

        stdout = completed.stdout.decode(
            "utf-8",
            errors="replace",
        ).strip()

        stderr = completed.stderr.decode(
            "utf-8",
            errors="replace",
        ).strip()

        if not stdout:
            detail = stderr or "Microsoft returned an empty response."

            raise OutlookWebAuthenticationError(
                "Microsoft OAuth token exchange returned no response: "
                f"{detail}"
            )

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            detail = stdout[:500]

            raise OutlookWebAuthenticationError(
                "Microsoft OAuth returned an invalid token response: "
                f"{detail}"
            ) from exc

        if not isinstance(result, dict):
            raise OutlookWebAuthenticationError(
                "Microsoft OAuth returned an invalid token response."
            )

        if "error" in result:
            error = result.get(
                "error",
                "unknown_error",
            )

            description = result.get(
                "error_description",
                "No error description was provided.",
            )

            raise OutlookWebAuthenticationError(
                "Microsoft OAuth token exchange failed: "
                f"{error}: {description}"
            )

        if completed.returncode != 0:
            detail = result.get(
                "error_description",
                stderr or "Unknown Microsoft OAuth error.",
            )

            raise OutlookWebAuthenticationError(
                "Microsoft OAuth token exchange failed: "
                f"{detail}"
            )

        if not result.get("access_token"):
            raise OutlookWebAuthenticationError(
                "Microsoft OAuth did not return an access token."
            )

        return result
