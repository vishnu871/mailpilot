"""
Tests for MailPilot UserStore.
"""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from database.user_store import (
    UserStore,
    UserStoreError,
)


def create_store(
    tmp_path,
) -> UserStore:
    """Create an isolated UserStore for testing."""

    return UserStore(
        database_path=(
            tmp_path / "mailpilot_users.sqlite3"
        ),
        encryption_key=Fernet.generate_key().decode(
            "utf-8"
        ),
    )


def test_user_store_can_create_user(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
        display_name="Test User",
    )

    assert user_id

    user = store.get_user(user_id)

    assert user is not None
    assert user["provider"] == "outlook"
    assert user["email"] == "user@example.com"
    assert user["display_name"] == "Test User"


def test_provider_credentials_are_encrypted_and_retrievable(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
        display_name="Test User",
    )

    credentials = {
        "access_token": "secret-access-token",
        "refresh_token": "secret-refresh-token",
        "token_type": "Bearer",
        "expires_in": 3600,
    }

    store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=credentials,
    )

    loaded = store.get_provider_credentials(
        user_id=user_id,
        provider="outlook",
    )

    assert loaded == credentials


def test_provider_credentials_are_not_stored_as_plaintext(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
        display_name="Test User",
    )

    access_token = "secret-access-token"

    credentials = {
        "access_token": access_token,
        "refresh_token": "secret-refresh-token",
    }

    store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=credentials,
    )

    database_bytes = (
        tmp_path / "mailpilot_users.sqlite3"
    ).read_bytes()

    assert access_token.encode(
        "utf-8"
    ) not in database_bytes

    assert b"secret-refresh-token" not in database_bytes


def test_missing_provider_credentials_return_none(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
    )

    result = store.get_provider_credentials(
        user_id=user_id,
        provider="outlook",
    )

    assert result is None


def test_provider_credentials_are_updated(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
    )

    first_credentials = {
        "access_token": "first-token",
        "refresh_token": "first-refresh",
    }

    second_credentials = {
        "access_token": "second-token",
        "refresh_token": "second-refresh",
    }

    store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=first_credentials,
    )

    store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=second_credentials,
    )

    loaded = store.get_provider_credentials(
        user_id=user_id,
        provider="outlook",
    )

    assert loaded == second_credentials


def test_provider_credentials_are_isolated_by_user(
    tmp_path,
):
    store = create_store(tmp_path)

    first_user = store.get_or_create_user(
        provider="outlook",
        email="first@example.com",
    )

    second_user = store.get_or_create_user(
        provider="outlook",
        email="second@example.com",
    )

    first_credentials = {
        "access_token": "first-token",
    }

    second_credentials = {
        "access_token": "second-token",
    }

    store.save_provider_credentials(
        user_id=first_user,
        provider="outlook",
        credentials=first_credentials,
    )

    store.save_provider_credentials(
        user_id=second_user,
        provider="outlook",
        credentials=second_credentials,
    )

    assert store.get_provider_credentials(
        user_id=first_user,
        provider="outlook",
    ) == first_credentials

    assert store.get_provider_credentials(
        user_id=second_user,
        provider="outlook",
    ) == second_credentials


def test_provider_credentials_are_isolated_by_provider(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
    )

    outlook_credentials = {
        "access_token": "outlook-token",
    }

    gmail_credentials = {
        "access_token": "gmail-token",
    }

    store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=outlook_credentials,
    )

    store.save_provider_credentials(
        user_id=user_id,
        provider="gmail",
        credentials=gmail_credentials,
    )

    assert store.get_provider_credentials(
        user_id=user_id,
        provider="outlook",
    ) == outlook_credentials

    assert store.get_provider_credentials(
        user_id=user_id,
        provider="gmail",
    ) == gmail_credentials


def test_invalid_provider_credentials_payload_is_rejected(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
    )

    with pytest.raises(
        UserStoreError,
        match="credentials",
    ):
        store.save_provider_credentials(
            user_id=user_id,
            provider="outlook",
            credentials="not-a-dictionary",
        )


def test_empty_provider_is_rejected(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
    )

    with pytest.raises(
        UserStoreError,
        match="Provider",
    ):
        store.save_provider_credentials(
            user_id=user_id,
            provider="",
            credentials={
                "access_token": "token",
            },
        )


def test_credentials_json_round_trip_is_stable(
    tmp_path,
):
    store = create_store(tmp_path)

    user_id = store.get_or_create_user(
        provider="outlook",
        email="user@example.com",
    )

    credentials = {
        "access_token": "token",
        "refresh_token": "refresh",
        "scope": [
            "User.Read",
            "Mail.ReadWrite",
        ],
        "metadata": {
            "provider": "microsoft",
        },
    }

    store.save_provider_credentials(
        user_id=user_id,
        provider="outlook",
        credentials=credentials,
    )

    loaded = store.get_provider_credentials(
        user_id=user_id,
        provider="outlook",
    )

    assert json.loads(
        json.dumps(loaded)
    ) == credentials