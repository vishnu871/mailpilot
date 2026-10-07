"""
Tests for MailPilot Outlook web OAuth authentication.
"""

from __future__ import annotations

import pytest

from app.auth.outlook_web import (
    OutlookWebAuthenticationError,
    OutlookWebAuthenticator,
)


def create_authenticator() -> OutlookWebAuthenticator:
    return OutlookWebAuthenticator(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri=(
            "http://localhost:8000/auth/outlook/callback"
        ),
        authority=(
            "https://login.microsoftonline.com/common"
        ),
    )


def test_configuration_is_loaded():
    authenticator = create_authenticator()

    assert authenticator.client_id == "test-client-id"
    assert authenticator.client_secret == "test-client-secret"
    assert authenticator.redirect_uri == (
        "http://localhost:8000/auth/outlook/callback"
    )
    assert authenticator.authority == (
        "https://login.microsoftonline.com/common"
    )


def test_missing_client_id_is_rejected(
    monkeypatch,
):
    monkeypatch.delenv(
        "MICROSOFT_CLIENT_ID",
        raising=False,
    )

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="MICROSOFT_CLIENT_ID",
    ):
        OutlookWebAuthenticator(
            client_id=None,
            client_secret="secret",
            redirect_uri=(
                "http://localhost:8000/auth/outlook/callback"
            ),
        )


def test_missing_client_secret_is_rejected(
    monkeypatch,
):
    monkeypatch.delenv(
        "MICROSOFT_CLIENT_SECRET",
        raising=False,
    )

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="MICROSOFT_CLIENT_SECRET",
    ):
        OutlookWebAuthenticator(
            client_id="client-id",
            client_secret=None,
            redirect_uri=(
                "http://localhost:8000/auth/outlook/callback"
            ),
        )


def test_missing_redirect_uri_is_rejected(
    monkeypatch,
):
    monkeypatch.delenv(
        "MICROSOFT_REDIRECT_URI",
        raising=False,
    )

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="MICROSOFT_REDIRECT_URI",
    ):
        OutlookWebAuthenticator(
            client_id="client-id",
            client_secret="secret",
            redirect_uri=None,
        )


def test_authorization_url_contains_expected_parameters():
    authenticator = create_authenticator()

    url, code_verifier = (
        authenticator.create_authorization_url(
            state="test-state-123"
        )
    )

    assert (
        "client_id=test-client-id"
        in url
    )

    assert (
        "redirect_uri="
        "http%3A%2F%2Flocalhost%3A8000%2Fauth%2Foutlook%2Fcallback"
        in url
    )

    assert (
        "response_type=code"
        in url
    )

    assert (
        "response_mode=query"
        in url
    )

    assert (
        "state=test-state-123"
        in url
    )

    assert (
        "code_challenge="
        in url
    )

    assert (
        "code_challenge_method=S256"
        in url
    )

    assert (
        "scope="
        in url
    )

    assert code_verifier


def test_authorization_url_generates_unique_pkce_verifiers():
    authenticator = create_authenticator()

    first_url, first_verifier = (
        authenticator.create_authorization_url(
            state="state-one"
        )
    )

    second_url, second_verifier = (
        authenticator.create_authorization_url(
            state="state-two"
        )
    )

    assert first_verifier != second_verifier
    assert first_url != second_url


def test_empty_authorization_code_is_rejected():
    authenticator = create_authenticator()

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="authorization code",
    ):
        authenticator.exchange_code_for_credentials(
            authorization_code="",
            code_verifier="test-verifier",
        )


def test_missing_code_verifier_is_rejected():
    authenticator = create_authenticator()

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="code verifier",
    ):
        authenticator.exchange_code_for_credentials(
            authorization_code="test-code",
            code_verifier="",
        )


def test_token_exchange_success(
    monkeypatch,
):
    authenticator = create_authenticator()

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "token_type": "Bearer",
            }

    captured = {}

    def fake_post(
        url,
        data,
        headers,
        timeout,
    ):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["timeout"] = timeout

        return FakeResponse()

    monkeypatch.setattr(
        "app.auth.outlook_web.requests.post",
        fake_post,
    )

    result = authenticator.exchange_code_for_credentials(
        authorization_code="test-code",
        code_verifier="test-verifier",
    )

    assert result["access_token"] == (
        "test-access-token"
    )

    assert result["refresh_token"] == (
        "test-refresh-token"
    )

    assert captured["url"] == (
        "https://login.microsoftonline.com/common"
        "/oauth2/v2.0/token"
    )

    assert captured["data"]["client_id"] == (
        "test-client-id"
    )

    assert captured["data"]["client_secret"] == (
        "test-client-secret"
    )

    assert captured["data"]["grant_type"] == (
        "authorization_code"
    )

    assert captured["data"]["code"] == (
        "test-code"
    )

    assert captured["data"]["code_verifier"] == (
        "test-verifier"
    )

    assert captured["data"]["redirect_uri"] == (
        "http://localhost:8000/auth/outlook/callback"
    )

    assert captured["data"]["scope"] == (
        "User.Read Mail.ReadWrite"
    )

    assert captured["headers"]["Content-Type"] == (
        "application/x-www-form-urlencoded"
    )

    assert captured["timeout"] == 30


def test_token_exchange_microsoft_error(
    monkeypatch,
):
    authenticator = create_authenticator()

    class FakeResponse:
        ok = False
        status_code = 400

        def json(self):
            return {
                "error": "invalid_grant",
                "error_description": (
                    "The authorization code is invalid."
                ),
            }

    def fake_post(
        url,
        data,
        headers,
        timeout,
    ):
        return FakeResponse()

    monkeypatch.setattr(
        "app.auth.outlook_web.requests.post",
        fake_post,
    )

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="invalid_grant",
    ):
        authenticator.exchange_code_for_credentials(
            authorization_code="bad-code",
            code_verifier="test-verifier",
        )


def test_token_exchange_without_access_token_is_rejected(
    monkeypatch,
):
    authenticator = create_authenticator()

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {
                "token_type": "Bearer",
            }

    def fake_post(
        url,
        data,
        headers,
        timeout,
    ):
        return FakeResponse()

    monkeypatch.setattr(
        "app.auth.outlook_web.requests.post",
        fake_post,
    )

    with pytest.raises(
        OutlookWebAuthenticationError,
        match="access token",
    ):
        authenticator.exchange_code_for_credentials(
            authorization_code="test-code",
            code_verifier="test-verifier",
        )