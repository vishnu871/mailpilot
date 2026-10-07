"""
MailPilot account and activation-key storage.

This module manages MailPilot application accounts independently from
provider-specific OAuth storage.

Responsibilities:
- MailPilot account registration
- Password hashing and verification
- Unique activation keys
- One-time activation
- Account status
- Account lookup

Provider OAuth credentials remain in UserStore for now and will be
bound to MailPilot accounts in the next integration phase.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


BASE_DIR = Path(__file__).resolve().parents[1]

DEFAULT_DATABASE_PATH = (
    BASE_DIR / "database" / "mailpilot_accounts.sqlite3"
)

PASSWORD_ITERATIONS = 310_000
PASSWORD_SALT_BYTES = 16
ACTIVATION_KEY_BYTES = 24


class AccountStoreError(Exception):
    """Raised when MailPilot account storage fails."""


class AccountStore:
    """
    Persistent storage for MailPilot application accounts.

    This is intentionally separate from provider OAuth storage so that
    a MailPilot account remains independent from Gmail or Outlook.
    """

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DATABASE_PATH,
    ) -> None:
        self.database_path = Path(database_path)

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._initialize_database()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open the account database."""

        connection = sqlite3.connect(
            str(self.database_path),
            timeout=30,
        )

        connection.row_factory = sqlite3.Row

        return connection

    def _initialize_database(self) -> None:
        """Create account and activation-key tables."""

        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA foreign_keys = ON;

                    CREATE TABLE IF NOT EXISTS accounts (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        password_salt TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS activation_keys (
                        id TEXT PRIMARY KEY,
                        key_hash TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        account_id TEXT,
                        activated_at TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(account_id)
                            REFERENCES accounts(id)
                            ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS
                        idx_activation_keys_account_id
                    ON activation_keys(account_id);

                    CREATE INDEX IF NOT EXISTS
                        idx_activation_keys_status
                    ON activation_keys(status);
                    """
                )

                connection.commit()

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to initialize account database: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        """Return current UTC time."""

        return datetime.now(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        """Convert datetime to ISO-8601 UTC timestamp."""

        return value.astimezone(
            timezone.utc
        ).isoformat()

    # ------------------------------------------------------------------
    # Password hashing
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_password(
        password: str,
        salt: bytes,
    ) -> str:
        """
        Hash a password using PBKDF2-HMAC-SHA256.
        """

        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PASSWORD_ITERATIONS,
        )

        return derived_key.hex()

    @classmethod
    def _create_password_credentials(
        cls,
        password: str,
    ) -> tuple[str, str]:
        """Create password salt and hash."""

        if not password:
            raise AccountStoreError(
                "Password is required."
            )

        if len(password) < 8:
            raise AccountStoreError(
                "Password must contain at least 8 characters."
            )

        salt = secrets.token_bytes(
            PASSWORD_SALT_BYTES
        )

        password_hash = cls._hash_password(
            password,
            salt,
        )

        return (
            salt.hex(),
            password_hash,
        )

    @classmethod
    def _verify_password(
        cls,
        password: str,
        salt_hex: str,
        expected_hash: str,
    ) -> bool:
        """Verify a password against stored credentials."""

        try:
            salt = bytes.fromhex(
                salt_hex
            )
        except ValueError:
            return False

        actual_hash = cls._hash_password(
            password,
            salt,
        )

        return hmac.compare_digest(
            actual_hash,
            expected_hash,
        )

    # ------------------------------------------------------------------
    # Activation keys
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_activation_key(
        activation_key: str,
    ) -> str:
        """Hash an activation key for database storage."""

        normalized = activation_key.strip().upper()

        return hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def generate_activation_key() -> str:
        """
        Generate a new unique customer activation key.

        The returned raw key is intended to be shown to the
        administrator/customer once. Only its hash is stored.
        """

        token = secrets.token_urlsafe(
            ACTIVATION_KEY_BYTES
        ).replace(
            "-",
            "",
        ).replace(
            "_",
            "",
        ).upper()

        return (
            f"MP-{token[:8]}-"
            f"{token[8:16]}-"
            f"{token[16:24]}-"
            f"{token[24:32]}"
        )

    def create_activation_key(self) -> str:
        """
        Create and persist a new unused activation key.

        Returns:
            The raw activation key.

        The raw key is NOT stored in the database.
        """

        for _ in range(10):
            activation_key = (
                self.generate_activation_key()
            )

            key_hash = self._hash_activation_key(
                activation_key
            )

            key_id = secrets.token_urlsafe(
                24
            )

            now = self._timestamp(
                self._now()
            )

            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO activation_keys (
                            id,
                            key_hash,
                            status,
                            account_id,
                            activated_at,
                            created_at
                        )
                        VALUES (?, ?, ?, NULL, NULL, ?)
                        """,
                        (
                            key_id,
                            key_hash,
                            "UNUSED",
                            now,
                        ),
                    )

                    connection.commit()

                    return activation_key

            except sqlite3.IntegrityError:
                continue

            except sqlite3.Error as exc:
                raise AccountStoreError(
                    f"Failed to create activation key: {exc}"
                ) from exc

        raise AccountStoreError(
            "Failed to generate a unique activation key."
        )

    def activate_key(
        self,
        activation_key: str,
        account_id: str,
    ) -> bool:
        """
        Bind one unused activation key to one account.

        Returns:
            True when activation succeeds.

        Returns False when:
            - the key does not exist
            - the key has already been activated
            - the key is revoked
            - the key is otherwise unavailable
        """

        if not activation_key:
            return False

        if not account_id:
            raise AccountStoreError(
                "Account ID is required."
            )

        key_hash = self._hash_activation_key(
            activation_key
        )

        now = self._timestamp(
            self._now()
        )

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        status,
                        account_id
                    FROM activation_keys
                    WHERE key_hash = ?
                    """,
                    (key_hash,),
                ).fetchone()

                if not row:
                    return False

                if row["status"] != "UNUSED":
                    return False

                if row["account_id"] is not None:
                    return False

                account_exists = connection.execute(
                    """
                    SELECT id
                    FROM accounts
                    WHERE id = ?
                    """,
                    (account_id,),
                ).fetchone()

                if not account_exists:
                    raise AccountStoreError(
                        "Account does not exist."
                    )

                result = connection.execute(
                    """
                    UPDATE activation_keys
                    SET status = ?,
                        account_id = ?,
                        activated_at = ?
                    WHERE id = ?
                      AND status = 'UNUSED'
                      AND account_id IS NULL
                    """,
                    (
                        "ACTIVE",
                        account_id,
                        now,
                        row["id"],
                    ),
                )

                connection.commit()

                return result.rowcount == 1

        except AccountStoreError:
            raise

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to activate key: {exc}"
            ) from exc

    def get_activation_status(
        self,
        account_id: str,
    ) -> Optional[str]:
        """Return the activation status for an account."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT status
                    FROM activation_keys
                    WHERE account_id = ?
                    ORDER BY activated_at DESC
                    LIMIT 1
                    """,
                    (account_id,),
                ).fetchone()

                if not row:
                    return None

                return str(
                    row["status"]
                )

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to retrieve activation status: {exc}"
            ) from exc

    def is_activated(
        self,
        account_id: str,
    ) -> bool:
        """Return whether an account has an active license."""

        return (
            self.get_activation_status(
                account_id
            )
            == "ACTIVE"
        )

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def create_account(
        self,
        email: str,
        password: str,
    ) -> str:
        """
        Create a MailPilot account.

        Activation is deliberately separate. A newly created account
        does not automatically receive product access.
        """

        normalized_email = email.strip().lower()

        if not normalized_email:
            raise AccountStoreError(
                "Email address is required."
            )

        if "@" not in normalized_email:
            raise AccountStoreError(
                "A valid email address is required."
            )

        password_salt, password_hash = (
            self._create_password_credentials(
                password
            )
        )

        account_id = secrets.token_urlsafe(
            24
        )

        now = self._timestamp(
            self._now()
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO accounts (
                        id,
                        email,
                        password_hash,
                        password_salt,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        normalized_email,
                        password_hash,
                        password_salt,
                        "ACTIVE",
                        now,
                        now,
                    ),
                )

                connection.commit()

                return account_id

        except sqlite3.IntegrityError as exc:
            raise AccountStoreError(
                "An account with this email already exists."
            ) from exc

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to create account: {exc}"
            ) from exc

    def get_account(
        self,
        account_id: str,
    ) -> Optional[dict[str, str]]:
        """Retrieve an account by ID."""

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        email,
                        status,
                        created_at,
                        updated_at
                    FROM accounts
                    WHERE id = ?
                    """,
                    (account_id,),
                ).fetchone()

                if not row:
                    return None

                return dict(row)

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to retrieve account: {exc}"
            ) from exc

    def get_account_by_email(
        self,
        email: str,
    ) -> Optional[dict[str, str]]:
        """Retrieve an account by email."""

        normalized_email = (
            email.strip().lower()
        )

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        email,
                        status,
                        created_at,
                        updated_at
                    FROM accounts
                    WHERE email = ?
                    """,
                    (normalized_email,),
                ).fetchone()

                if not row:
                    return None

                return dict(row)

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to retrieve account: {exc}"
            ) from exc

    def verify_login(
        self,
        email: str,
        password: str,
    ) -> Optional[dict[str, str]]:
        """
        Verify account credentials.

        Returns the account when valid, otherwise None.
        """

        normalized_email = (
            email.strip().lower()
        )

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        email,
                        password_hash,
                        password_salt,
                        status,
                        created_at,
                        updated_at
                    FROM accounts
                    WHERE email = ?
                    """,
                    (normalized_email,),
                ).fetchone()

                if not row:
                    return None

                if row["status"] != "ACTIVE":
                    return None

                valid = self._verify_password(
                    password=password,
                    salt_hex=row["password_salt"],
                    expected_hash=row["password_hash"],
                )

                if not valid:
                    return None

                return {
                    "id": row["id"],
                    "email": row["email"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to verify login: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # License administration
    # ------------------------------------------------------------------

    def revoke_activation(
        self,
        account_id: str,
    ) -> bool:
        """
        Revoke the active activation associated with an account.
        """

        try:
            with self._connect() as connection:
                result = connection.execute(
                    """
                    UPDATE activation_keys
                    SET status = 'REVOKED'
                    WHERE account_id = ?
                      AND status = 'ACTIVE'
                    """,
                    (account_id,),
                )

                connection.commit()

                return result.rowcount > 0

        except sqlite3.Error as exc:
            raise AccountStoreError(
                f"Failed to revoke activation: {exc}"
            ) from exc