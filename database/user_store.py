"""
MailPilot per-user authentication storage.

This module provides the local development storage layer for:

- MailPilot users
- encrypted provider credentials
- server-side sessions
- pending OAuth states

The storage implementation currently uses SQLite so the complete
multi-user authentication architecture can be tested locally.

Before production deployment to Vercel or Netlify, this storage
interface should be backed by a persistent PostgreSQL-compatible
database rather than the local filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from google.oauth2.credentials import Credentials


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

DEFAULT_DATABASE_PATH = (
    BASE_DIR / "database" / "mailpilot_users.sqlite3"
)

SESSION_DURATION_HOURS = 24
OAUTH_STATE_DURATION_MINUTES = 10


class UserStoreError(Exception):
    """Raised when user storage operations fail."""


class UserStore:
    """Manage MailPilot users, credentials, sessions, and OAuth state."""

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DATABASE_PATH,
        encryption_key: str | None = None,
    ) -> None:
        self.database_path = Path(database_path)

        key = encryption_key or os.getenv(
            "MAILPILOT_TOKEN_ENCRYPTION_KEY"
        )

        if not key:
            raise UserStoreError(
                "MAILPILOT_TOKEN_ENCRYPTION_KEY is not configured."
            )

        try:
            self.fernet = Fernet(key.encode("utf-8"))
        except Exception as exc:
            raise UserStoreError(
                "MAILPILOT_TOKEN_ENCRYPTION_KEY is invalid."
            ) from exc

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection."""

        connection = sqlite3.connect(
            str(self.database_path),
            timeout=30,
        )

        connection.row_factory = sqlite3.Row

        return connection

    def _initialize_database(self) -> None:
        """Create or migrate the authentication tables."""

        try:
            with self._connect() as connection:
                connection.execute(
                    "PRAGMA foreign_keys = ON"
                )

                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        email TEXT NOT NULL,
                        display_name TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        account_id TEXT,
                        UNIQUE(provider, email)
                    );

                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        FOREIGN KEY(user_id)
                            REFERENCES users(id)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                    ON sessions(user_id);

                    CREATE INDEX IF NOT EXISTS idx_sessions_expires_at
                    ON sessions(expires_at);

                    CREATE TABLE IF NOT EXISTS oauth_states (
                        state_hash TEXT PRIMARY KEY,
                        code_verifier_encrypted TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        account_id TEXT
                    );
                    """
                )

                self._migrate_users_account_id(
                    connection
                )

                self._migrate_oauth_credentials(
                    connection
                )

                self._migrate_oauth_states_account_id(
                    connection
                )

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to initialize user database: {exc}"
            ) from exc

    def _migrate_users_account_id(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Ensure provider users can be bound to a MailPilot account."""

        columns = connection.execute(
            "PRAGMA table_info(users)"
        ).fetchall()

        if not any(row["name"] == "account_id" for row in columns):
            connection.execute(
                "ALTER TABLE users ADD COLUMN account_id TEXT"
            )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_account_id
            ON users(account_id)
            """
        )

    def _migrate_oauth_states_account_id(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Ensure OAuth states can be bound to a MailPilot account."""

        columns = connection.execute(
            "PRAGMA table_info(oauth_states)"
        ).fetchall()

        if not any(row["name"] == "account_id" for row in columns):
            connection.execute(
                "ALTER TABLE oauth_states ADD COLUMN account_id TEXT"
            )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oauth_states_account_id
            ON oauth_states(account_id)
            """
        )

    def _migrate_oauth_credentials(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """
        Ensure oauth_credentials supports multiple providers per user.

        Older MailPilot databases used user_id as the primary key.
        The new schema uses a composite primary key:

            PRIMARY KEY(user_id, provider)

        Existing credential rows are preserved during migration.
        """

        table_row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'oauth_credentials'
            """
        ).fetchone()

        if not table_row:
            connection.execute(
                """
                CREATE TABLE oauth_credentials (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    credentials_encrypted TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, provider),
                    FOREIGN KEY(user_id)
                        REFERENCES users(id)
                        ON DELETE CASCADE
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_oauth_credentials_provider
                ON oauth_credentials(provider)
                """
            )

            return

        columns = connection.execute(
            """
            PRAGMA table_info(oauth_credentials)
            """
        ).fetchall()

        primary_key_columns = [
            row["name"]
            for row in columns
            if row["pk"] > 0
        ]

        if primary_key_columns == [
            "user_id",
            "provider",
        ]:
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_oauth_credentials_provider
                ON oauth_credentials(provider)
                """
            )

            return

        connection.execute(
            """
            ALTER TABLE oauth_credentials
            RENAME TO oauth_credentials_legacy
            """
        )

        connection.execute(
            """
            CREATE TABLE oauth_credentials (
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                credentials_encrypted TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, provider),
                FOREIGN KEY(user_id)
                    REFERENCES users(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            INSERT INTO oauth_credentials (
                user_id,
                provider,
                credentials_encrypted,
                created_at,
                updated_at
            )
            SELECT
                user_id,
                provider,
                credentials_encrypted,
                created_at,
                updated_at
            FROM oauth_credentials_legacy
            """
        )

        connection.execute(
            """
            DROP TABLE oauth_credentials_legacy
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_oauth_credentials_provider
            ON oauth_credentials(provider)
            """
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        """Return the current UTC time."""

        return datetime.now(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        """Convert a datetime to an ISO timestamp."""

        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        """Parse an ISO timestamp."""

        return datetime.fromisoformat(value)

    @staticmethod
    def _hash_state(state: str) -> str:
        """Hash an OAuth state before storing it."""

        return hashlib.sha256(
            state.encode("utf-8")
        ).hexdigest()

    def _encrypt(self, value: str) -> str:
        """Encrypt sensitive storage data."""

        try:
            return self.fernet.encrypt(
                value.encode("utf-8")
            ).decode("utf-8")
        except Exception as exc:
            raise UserStoreError(
                f"Failed to encrypt sensitive data: {exc}"
            ) from exc

    def _decrypt(self, value: str) -> str:
        """Decrypt sensitive storage data."""

        try:
            return self.fernet.decrypt(
                value.encode("utf-8")
            ).decode("utf-8")
        except InvalidToken as exc:
            raise UserStoreError(
                "Failed to decrypt stored credentials."
            ) from exc
        except Exception as exc:
            raise UserStoreError(
                f"Failed to decrypt sensitive data: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def get_or_create_user(
        self,
        provider: str,
        email: str,
        display_name: str | None = None,
        account_id: str | None = None,
    ) -> str:
        """
        Get an existing provider user or create a new one.

        Provider + email uniquely identify an external account. When
        account_id is supplied, the provider user is explicitly bound
        to that MailPilot account. An existing binding cannot be changed
        implicitly to a different account.
        """

        provider = provider.strip().lower()
        email = email.strip().lower()
        account_id = account_id.strip() if account_id else None

        if not provider:
            raise UserStoreError("Provider is required.")

        if not email:
            raise UserStoreError("Email address is required.")

        if account_id is not None and not account_id:
            raise UserStoreError("Account ID is required when provided.")

        now = self._timestamp(self._now())

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, account_id
                    FROM users
                    WHERE provider = ?
                      AND email = ?
                    """,
                    (
                        provider,
                        email,
                    ),
                ).fetchone()

                if row:
                    existing_account_id = row["account_id"]

                    if (
                        account_id is not None
                        and existing_account_id is not None
                        and existing_account_id != account_id
                    ):
                        raise UserStoreError(
                            "Provider account is already bound to a different "
                            "MailPilot account."
                        )

                    connection.execute(
                        """
                        UPDATE users
                        SET display_name = ?,
                            account_id = COALESCE(account_id, ?),
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            display_name,
                            account_id,
                            now,
                            row["id"],
                        ),
                    )

                    return str(row["id"])

                user_id = secrets.token_urlsafe(24)

                connection.execute(
                    """
                    INSERT INTO users (
                        id,
                        provider,
                        email,
                        display_name,
                        created_at,
                        updated_at,
                        account_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        provider,
                        email,
                        display_name,
                        now,
                        now,
                        account_id,
                    ),
                )

                return user_id

        except UserStoreError:
            raise

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to create or retrieve user: {exc}"
            ) from exc

    def list_connected_users(self) -> list[dict[str, str]]:
        """Return all provider-connected users without exposing credentials."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        id,
                        provider,
                        email,
                        display_name,
                        created_at,
                        updated_at,
                        account_id
                    FROM users
                    ORDER BY provider, email
                    """
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to list connected users: {exc}"
            ) from exc

    def get_user(
        self,
        user_id: str,
    ) -> Optional[dict[str, str]]:
        """Retrieve a user by MailPilot user ID."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        provider,
                        email,
                        display_name,
                        created_at,
                        updated_at,
                        account_id
                    FROM users
                    WHERE id = ?
                    """,
                    (user_id,),
                ).fetchone()

                if not row:
                    return None

                return dict(row)

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to retrieve user: {exc}"
            ) from exc

    def bind_user_to_account(
        self,
        user_id: str,
        account_id: str,
    ) -> None:
        """Bind an existing provider user to a MailPilot account safely."""

        user_id = user_id.strip()
        account_id = account_id.strip()

        if not user_id:
            raise UserStoreError("User ID is required.")

        if not account_id:
            raise UserStoreError("Account ID is required.")

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT account_id
                    FROM users
                    WHERE id = ?
                    """,
                    (user_id,),
                ).fetchone()

                if not row:
                    raise UserStoreError("User not found.")

                existing_account_id = row["account_id"]

                if (
                    existing_account_id is not None
                    and existing_account_id != account_id
                ):
                    raise UserStoreError(
                        "Provider account is already bound to a different "
                        "MailPilot account."
                    )

                connection.execute(
                    """
                    UPDATE users
                    SET account_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        account_id,
                        self._timestamp(self._now()),
                        user_id,
                    ),
                )

        except UserStoreError:
            raise

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to bind user to MailPilot account: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Provider credentials
    # ------------------------------------------------------------------

    def save_credentials(
        self,
        user_id: str,
        provider: str,
        credentials: Credentials,
    ) -> None:
        """
        Encrypt and save Gmail OAuth credentials for one user.

        This method is retained for backward compatibility with the
        existing Gmail integration.
        """

        credentials_json = credentials.to_json()
        encrypted = self._encrypt(
            credentials_json
        )

        now = self._timestamp(self._now())

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO oauth_credentials (
                        user_id,
                        provider,
                        credentials_encrypted,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, provider)
                    DO UPDATE SET
                        credentials_encrypted =
                            excluded.credentials_encrypted,
                        updated_at = excluded.updated_at
                    """,
                    (
                        user_id,
                        provider.lower(),
                        encrypted,
                        now,
                        now,
                    ),
                )

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to save OAuth credentials: {exc}"
            ) from exc

    def get_credentials(
        self,
        user_id: str,
        scopes: tuple[str, ...] | None = None,
    ) -> Optional[Credentials]:
        """
        Load and decrypt Gmail OAuth credentials for one user.

        This method is retained for backward compatibility with the
        existing Gmail integration.
        """

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT credentials_encrypted
                    FROM oauth_credentials
                    WHERE user_id = ?
                      AND provider = 'gmail'
                    """,
                    (user_id,),
                ).fetchone()

                if not row:
                    return None

                credentials_json = self._decrypt(
                    row["credentials_encrypted"]
                )

                credentials = (
                    Credentials.from_authorized_user_info(
                        json.loads(credentials_json),
                        scopes=scopes,
                    )
                )

                return credentials

        except UserStoreError:
            raise

        except Exception as exc:
            raise UserStoreError(
                f"Failed to load OAuth credentials: {exc}"
            ) from exc

    def save_provider_credentials(
        self,
        user_id: str,
        provider: str,
        credentials: dict[str, Any],
    ) -> None:
        """
        Encrypt and save provider-neutral OAuth credentials.

        This method is used by providers such as Microsoft Outlook
        whose OAuth token representation is not compatible with the
        Google Credentials class used by Gmail.
        """

        provider = provider.strip().lower()

        if not provider:
            raise UserStoreError(
                "Provider is required."
            )

        if not isinstance(credentials, dict):
            raise UserStoreError(
                "Provider credentials must be a dictionary."
            )

        try:
            credentials_json = json.dumps(
                credentials,
                separators=(",", ":"),
                sort_keys=True,
            )

        except (TypeError, ValueError) as exc:
            raise UserStoreError(
                "Provider credentials must be JSON serializable."
            ) from exc

        encrypted = self._encrypt(
            credentials_json
        )

        now = self._timestamp(self._now())

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO oauth_credentials (
                        user_id,
                        provider,
                        credentials_encrypted,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, provider)
                    DO UPDATE SET
                        credentials_encrypted =
                            excluded.credentials_encrypted,
                        updated_at = excluded.updated_at
                    """,
                    (
                        user_id,
                        provider,
                        encrypted,
                        now,
                        now,
                    ),
                )

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to save provider credentials: {exc}"
            ) from exc

    def get_provider_credentials(
        self,
        user_id: str,
        provider: str,
    ) -> Optional[dict[str, Any]]:
        """
        Load and decrypt provider-neutral OAuth credentials.

        Credentials are returned only when they belong to the
        requested user and provider.
        """

        provider = provider.strip().lower()

        if not provider:
            raise UserStoreError(
                "Provider is required."
            )

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT credentials_encrypted
                    FROM oauth_credentials
                    WHERE user_id = ?
                      AND provider = ?
                    """,
                    (
                        user_id,
                        provider,
                    ),
                ).fetchone()

                if not row:
                    return None

                credentials_json = self._decrypt(
                    row["credentials_encrypted"]
                )

                credentials = json.loads(
                    credentials_json
                )

                if not isinstance(
                    credentials,
                    dict,
                ):
                    raise UserStoreError(
                        "Stored provider credentials are invalid."
                    )

                return credentials

        except UserStoreError:
            raise

        except (json.JSONDecodeError, TypeError) as exc:
            raise UserStoreError(
                "Stored provider credentials are invalid."
            ) from exc

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to load provider credentials: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        user_id: str,
    ) -> str:
        """Create a new server-side session."""

        session_id = secrets.token_urlsafe(48)

        created_at = self._now()
        expires_at = created_at + timedelta(
            hours=SESSION_DURATION_HOURS
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id,
                        user_id,
                        created_at,
                        expires_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        user_id,
                        self._timestamp(created_at),
                        self._timestamp(expires_at),
                    ),
                )

                return session_id

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to create session: {exc}"
            ) from exc

    def get_user_by_session(
        self,
        session_id: str,
    ) -> Optional[dict[str, str]]:
        """Resolve a valid session to its user."""

        if not session_id:
            return None

        now = self._now()

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        u.id,
                        u.provider,
                        u.email,
                        u.display_name,
                        u.created_at,
                        u.updated_at,
                        u.account_id,
                        s.expires_at
                    FROM sessions s
                    JOIN users u
                      ON u.id = s.user_id
                    WHERE s.session_id = ?
                    """,
                    (session_id,),
                ).fetchone()

                if not row:
                    return None

                expires_at = self._parse_timestamp(
                    row["expires_at"]
                )

                if expires_at <= now:
                    connection.execute(
                        """
                        DELETE FROM sessions
                        WHERE session_id = ?
                        """,
                        (session_id,),
                    )

                    return None

                return dict(row)

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to resolve session: {exc}"
            ) from exc

    def delete_session(
        self,
        session_id: str,
    ) -> None:
        """Delete a session."""

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    DELETE FROM sessions
                    WHERE session_id = ?
                    """,
                    (session_id,),
                )

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to delete session: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # OAuth state / PKCE
    # ------------------------------------------------------------------

    def save_oauth_state(
        self,
        state: str,
        code_verifier: str,
        account_id: str | None = None,
    ) -> None:
        """Store OAuth state, PKCE verifier, and optional MailPilot account binding."""

        account_id = account_id.strip() if account_id else None

        if account_id is not None and not account_id:
            raise UserStoreError("Account ID is required when provided.")

        now = self._now()
        expires_at = now + timedelta(
            minutes=OAUTH_STATE_DURATION_MINUTES
        )

        state_hash = self._hash_state(state)
        encrypted_verifier = self._encrypt(
            code_verifier
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO oauth_states (
                        state_hash,
                        code_verifier_encrypted,
                        created_at,
                        expires_at,
                        account_id
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state_hash,
                        encrypted_verifier,
                        self._timestamp(now),
                        self._timestamp(expires_at),
                        account_id,
                    ),
                )

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to save OAuth state: {exc}"
            ) from exc

    def consume_oauth_state(
        self,
        state: str,
    ) -> Optional[str]:
        """
        Validate and consume an OAuth state.

        This compatibility method returns only the PKCE verifier.
        Use ``consume_oauth_state_with_account`` when the MailPilot
        account binding is required.
        """

        result = self.consume_oauth_state_with_account(state)

        if result is None:
            return None

        return result["code_verifier"]

    def consume_oauth_state_with_account(
        self,
        state: str,
    ) -> Optional[dict[str, str | None]]:
        """Validate and consume OAuth state with its MailPilot account binding."""

        state_hash = self._hash_state(state)
        now = self._now()

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        code_verifier_encrypted,
                        expires_at,
                        account_id
                    FROM oauth_states
                    WHERE state_hash = ?
                    """,
                    (state_hash,),
                ).fetchone()

                if not row:
                    return None

                connection.execute(
                    """
                    DELETE FROM oauth_states
                    WHERE state_hash = ?
                    """,
                    (state_hash,),
                )

                expires_at = self._parse_timestamp(
                    row["expires_at"]
                )

                if expires_at <= now:
                    return None

                return {
                    "code_verifier": self._decrypt(
                        row["code_verifier_encrypted"]
                    ),
                    "account_id": row["account_id"],
                }

        except UserStoreError:
            raise

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to consume OAuth state: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> None:
        """Remove expired sessions and OAuth states."""

        now = self._timestamp(self._now())

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    DELETE FROM sessions
                    WHERE expires_at <= ?
                    """,
                    (now,),
                )

                connection.execute(
                    """
                    DELETE FROM oauth_states
                    WHERE expires_at <= ?
                    """,
                    (now,),
                )

        except sqlite3.Error as exc:
            raise UserStoreError(
                f"Failed to clean up expired authentication data: {exc}"
            ) from exc