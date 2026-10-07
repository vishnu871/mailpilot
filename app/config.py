"""
MailPilot application configuration.

Configuration is loaded from environment variables so that secrets and
runtime behavior are not hardcoded into application code.
"""

from __future__ import annotations

import os
from enum import Enum

from dotenv import load_dotenv


load_dotenv()


class RunMode(str, Enum):
    """MailPilot operating modes."""

    DRY_RUN = "dry_run"
    TEST = "test"
    LIVE = "live"


class Settings:
    """Central application configuration."""

    def __init__(self) -> None:
        self.app_name = "MailPilot"
        self.run_mode = self._load_run_mode()
        self.inbox_batch_size = self._load_positive_int(
            "MAILPILOT_INBOX_BATCH_SIZE",
            default=50,
        )
        self.log_level = os.getenv(
            "MAILPILOT_LOG_LEVEL",
            "INFO",
        ).strip().upper()

        # Microsoft authentication configuration.
        # These remain optional until the Graph provider is implemented.
        self.microsoft_client_id = os.getenv(
            "MICROSOFT_CLIENT_ID",
            "",
        ).strip()

        self.microsoft_authority = os.getenv(
            "MICROSOFT_AUTHORITY",
            "https://login.microsoftonline.com/common",
        ).strip()

        self.microsoft_scopes = self._load_scopes()

    @property
    def is_dry_run(self) -> bool:
        """Return whether mailbox mutations are disabled."""
        return self.run_mode == RunMode.DRY_RUN

    @property
    def is_test_mode(self) -> bool:
        """Return whether controlled test behavior is enabled."""
        return self.run_mode == RunMode.TEST

    @property
    def is_live_mode(self) -> bool:
        """Return whether real mailbox organization is enabled."""
        return self.run_mode == RunMode.LIVE

    def _load_run_mode(self) -> RunMode:
        """Read and validate the configured operating mode."""
        raw_mode = os.getenv(
            "MAILPILOT_RUN_MODE",
            RunMode.DRY_RUN.value,
        ).strip().lower()

        try:
            return RunMode(raw_mode)
        except ValueError as exc:
            valid_modes = ", ".join(mode.value for mode in RunMode)

            raise ValueError(
                f"Invalid MAILPILOT_RUN_MODE='{raw_mode}'. "
                f"Expected one of: {valid_modes}"
            ) from exc

    @staticmethod
    def _load_positive_int(
        environment_name: str,
        default: int,
    ) -> int:
        """Load a positive integer from an environment variable."""
        raw_value = os.getenv(
            environment_name,
            str(default),
        ).strip()

        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(
                f"{environment_name} must be an integer. "
                f"Received: '{raw_value}'"
            ) from exc

        if value <= 0:
            raise ValueError(
                f"{environment_name} must be greater than zero. "
                f"Received: {value}"
            )

        return value

    @staticmethod
    def _load_scopes() -> tuple[str, ...]:
        """
        Load Microsoft Graph scopes.

        Mail.ReadWrite is required for the eventual mailbox workflow:
        reading messages, creating folders, and moving messages.

        No send permission is requested because MailPilot does not send
        email.
        """
        raw_scopes = os.getenv(
            "MICROSOFT_SCOPES",
            "Mail.ReadWrite",
        )

        scopes = tuple(
            scope.strip()
            for scope in raw_scopes.split(",")
            if scope.strip()
        )

        return scopes


settings = Settings()