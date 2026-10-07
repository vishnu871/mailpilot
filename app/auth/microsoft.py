"""
MailPilot Microsoft authentication.

Handles Microsoft identity authentication independently from the
Microsoft Graph mail provider.

Authentication uses MSAL device-code flow and a local token cache.
No passwords are collected or stored by MailPilot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msal

from app.config import settings


class MicrosoftAuthenticationError(Exception):
    """Raised when Microsoft authentication cannot be completed."""


class MicrosoftAuthenticator:
    """Authenticate MailPilot with a Microsoft account."""

    def __init__(
        self,
        client_id: str | None = None,
        authority: str | None = None,
        scopes: tuple[str, ...] | None = None,
        cache_path: str | Path | None = None,
    ) -> None:
        self._client_id = (
            client_id or settings.microsoft_client_id
        ).strip()

        self._authority = (
            authority or settings.microsoft_authority
        ).strip()

        self._scopes = scopes or settings.microsoft_scopes

        self._cache_path = Path(
            cache_path or "database/msal_token_cache.json"
        )

        if not self._client_id:
            raise MicrosoftAuthenticationError(
                "MICROSOFT_CLIENT_ID is not configured. "
                "Set it in the .env file before authenticating."
            )

        if not self._authority:
            raise MicrosoftAuthenticationError(
                "MICROSOFT_AUTHORITY cannot be empty."
            )

        if not self._scopes:
            raise MicrosoftAuthenticationError(
                "At least one Microsoft Graph scope is required."
            )

        self._cache = self._load_cache()

        self._app = msal.PublicClientApplication(
            client_id=self._client_id,
            authority=self._authority,
            token_cache=self._cache,
        )

    def acquire_access_token(self) -> str:
        """
        Return a valid Microsoft Graph access token.

        An existing cached account/token is used when possible.
        Otherwise, MSAL device-code authentication is started.
        """
        accounts = self._app.get_accounts()

        if accounts:
            result = self._app.acquire_token_silent(
                scopes=list(self._scopes),
                account=accounts[0],
            )

            if result and "access_token" in result:
                self._save_cache()
                return result["access_token"]

            if result and result.get("error"):
                # A silent failure can occur when interactive
                # authentication is required. Continue to device flow.
                pass

        try:
            flow = self._app.initiate_device_flow(
                scopes=list(self._scopes)
            )
        except Exception as exc:
            raise MicrosoftAuthenticationError(
                f"Unable to initialize Microsoft device authentication: "
                f"{exc}"
            ) from exc

        if "user_code" not in flow:
            error_description = flow.get(
                "error_description",
                "Unknown device-flow error.",
            )

            raise MicrosoftAuthenticationError(
                "Microsoft device authentication could not be initialized: "
                f"{error_description}"
            )

        message = flow.get(
            "message",
            "Follow the Microsoft device authentication instructions.",
        )

        print()
        print(message)
        print()

        try:
            result = self._app.acquire_token_by_device_flow(flow)
        except Exception as exc:
            raise MicrosoftAuthenticationError(
                f"Microsoft device authentication failed: {exc}"
            ) from exc

        access_token = result.get("access_token")

        if not access_token:
            error = result.get(
                "error_description",
                result.get("error", "Unknown authentication error."),
            )

            raise MicrosoftAuthenticationError(
                f"Microsoft authentication failed: {error}"
            )

        self._save_cache()

        return access_token

    def clear_cache(self) -> None:
        """Remove the local authentication cache."""
        self._cache = msal.SerializableTokenCache()

        if self._cache_path.exists():
            self._cache_path.unlink()

    def _load_cache(self) -> msal.SerializableTokenCache:
        """Load the token cache from disk if available."""
        cache = msal.SerializableTokenCache()

        if not self._cache_path.exists():
            return cache

        try:
            serialized_cache = self._cache_path.read_text(
                encoding="utf-8"
            )

            if serialized_cache.strip():
                cache.deserialize(serialized_cache)

        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise MicrosoftAuthenticationError(
                f"Unable to read Microsoft token cache: {exc}"
            ) from exc

        return cache

    def _save_cache(self) -> None:
        """Persist the token cache when MSAL reports changes."""
        if not self._cache.has_state_changed:
            return

        try:
            self._cache_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self._cache_path.write_text(
                self._cache.serialize(),
                encoding="utf-8",
            )

        except OSError as exc:
            raise MicrosoftAuthenticationError(
                f"Unable to save Microsoft token cache: {exc}"
            ) from exc


def get_access_token() -> str:
    """Convenience function for obtaining a Microsoft Graph token."""
    authenticator = MicrosoftAuthenticator()
    return authenticator.acquire_access_token()