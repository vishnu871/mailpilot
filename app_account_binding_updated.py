"""
MailPilot web application.

Provides the MailPilot web interface, MailPilot account registration,
activation-key validation, account authentication, per-user Gmail OAuth,
per-user Outlook OAuth, server-side provider sessions, and the mailbox
dashboard.

The MailPilot account/license is the primary application identity.

Provider-specific OAuth credentials remain stored in UserStore and are
encrypted at rest.

Each provider connection belongs to the authenticated MailPilot account
through its provider session.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

from app.auth.gmail_web import (
    GmailWebAuthenticationError,
    GmailWebAuthenticator,
)
from app.auth.outlook_web import (
    OutlookWebAuthenticationError,
    OutlookWebAuthenticator,
)
from app.mail.gmail.provider import (
    GmailProvider,
    MailProviderError,
)
from app.mail.outlook.provider import OutlookProvider
from database.account_store import (
    AccountStore,
    AccountStoreError,
)
from database.user_store import (
    UserStore,
    UserStoreError,
)


BASE_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = BASE_DIR / "templates"

ACCOUNT_SESSION_COOKIE_NAME = "mailpilot_account_session"
PROVIDER_SESSION_COOKIE_NAME = "mailpilot_provider_session"

ACCOUNT_SESSION_MAX_AGE = 24 * 60 * 60

GRAPH_API_BASE_URL = "https://graph.microsoft.com/v1.0"


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


app = FastAPI(
    title="MailPilot",
    description="AI-powered email organizer",
    version="1.0.0",
)


account_store = AccountStore()
user_store = UserStore()


# ----------------------------------------------------------------------
# Microsoft Graph client
# ----------------------------------------------------------------------


class MicrosoftGraphClient:
    """
    Small authenticated Microsoft Graph HTTP client.

    Authentication is deliberately injected through an access token.
    The Outlook provider remains responsible only for mailbox semantics,
    while this class handles Graph HTTP authentication.
    """

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = GRAPH_API_BASE_URL,
        timeout: int = 30,
    ) -> None:
        access_token = access_token.strip()

        if not access_token:
            raise ValueError(
                "Microsoft Graph access token cannot be empty."
            )

        if timeout <= 0:
            raise ValueError(
                "Microsoft Graph timeout must be greater than zero."
            )

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )

    def get(
        self,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform an authenticated Microsoft Graph GET request."""

        return self._request(
            "GET",
            path,
            **kwargs,
        )

    def post(
        self,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform an authenticated Microsoft Graph POST request."""

        return self._request(
            "POST",
            path,
            **kwargs,
        )

    def patch(
        self,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Perform an authenticated Microsoft Graph PATCH request."""

        return self._request(
            "PATCH",
            path,
            **kwargs,
        )

    def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute one authenticated Graph request."""

        if path.startswith("https://"):
            url = path
        elif path.startswith("http://"):
            url = path
        else:
            url = (
                f"{self.base_url}/"
                f"{path.lstrip('/')}"
            )

        kwargs.setdefault(
            "timeout",
            self.timeout,
        )

        try:
            response = self.session.request(
                method,
                url,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise MailProviderError(
                "Microsoft Graph request failed: "
                f"{exc}"
            ) from exc

        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = {}

            error_data = payload.get(
                "error",
                {},
            )

            if isinstance(
                error_data,
                dict,
            ):
                error_code = error_data.get(
                    "code",
                    "GraphError",
                )
                error_message = error_data.get(
                    "message",
                    response.text or "Unknown Graph error.",
                )
            else:
                error_code = "GraphError"
                error_message = (
                    response.text
                    or "Unknown Microsoft Graph error."
                )

            raise MailProviderError(
                "Microsoft Graph request failed "
                f"({response.status_code}) "
                f"{error_code}: {error_message}"
            )

        if not response.content:
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            raise MailProviderError(
                "Microsoft Graph returned an invalid JSON response."
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):
            raise MailProviderError(
                "Microsoft Graph returned an invalid response payload."
            )

        return payload


# ----------------------------------------------------------------------
# Session helpers
# ----------------------------------------------------------------------


def _session_secret() -> str:
    """
    Return the local application session secret.

    A stable environment-provided secret should be configured before
    public deployment.
    """

    configured_secret = os.getenv(
        "MAILPILOT_SESSION_SECRET"
    )

    if configured_secret:
        return configured_secret

    # Development fallback.
    #
    # This value is process-local and therefore sessions will be
    # invalidated when the application restarts. Production deployment
    # must configure MAILPILOT_SESSION_SECRET.
    if not hasattr(
        _session_secret,
        "_development_secret",
    ):
        setattr(
            _session_secret,
            "_development_secret",
            secrets.token_urlsafe(48),
        )

    return getattr(
        _session_secret,
        "_development_secret",
    )


def _sign_account_id(
    account_id: str,
) -> str:
    """Create a signed MailPilot account session value."""

    signature = hmac.new(
        _session_secret().encode("utf-8"),
        account_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return f"{account_id}.{signature}"


def _verify_account_session(
    value: str,
) -> str | None:
    """Verify and return an account ID from a signed session value."""

    if not value or "." not in value:
        return None

    account_id, signature = value.rsplit(
        ".",
        1,
    )

    if not account_id or not signature:
        return None

    expected_signature = hmac.new(
        _session_secret().encode("utf-8"),
        account_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        signature,
        expected_signature,
    ):
        return None

    return account_id


def _set_account_session(
    response: RedirectResponse,
    account_id: str,
) -> None:
    """Set the authenticated MailPilot account cookie."""

    response.set_cookie(
        key=ACCOUNT_SESSION_COOKIE_NAME,
        value=_sign_account_id(account_id),
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=ACCOUNT_SESSION_MAX_AGE,
        path="/",
    )


def _set_provider_session(
    response: RedirectResponse,
    session_id: str,
) -> None:
    """Set the authenticated provider session cookie."""

    response.set_cookie(
        key=PROVIDER_SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=ACCOUNT_SESSION_MAX_AGE,
        path="/",
    )


def _get_current_account(
    request: Request,
) -> dict[str, str] | None:
    """Resolve the authenticated MailPilot account."""

    session_value = request.cookies.get(
        ACCOUNT_SESSION_COOKIE_NAME
    )

    if not session_value:
        return None

    account_id = _verify_account_session(
        session_value
    )

    if not account_id:
        return None

    try:
        account = account_store.get_account(
            account_id
        )

        if not account:
            return None

        if account.get("status") != "ACTIVE":
            return None

        if not account_store.is_activated(
            account_id
        ):
            return None

        return account

    except AccountStoreError:
        return None


def _get_provider_user(
    request: Request,
) -> dict[str, str] | None:
    """Resolve the current provider user from the provider session."""

    session_id = request.cookies.get(
        PROVIDER_SESSION_COOKIE_NAME
    )

    if not session_id:
        return None

    try:
        return user_store.get_user_by_session(
            session_id
        )

    except UserStoreError:
        return None


def _get_current_user(
    request: Request,
) -> dict[str, str] | None:
    """
    Resolve the provider user only when it belongs to the
    authenticated MailPilot account.
    """

    account = _get_current_account(
        request
    )

    if not account:
        return None

    provider_user = _get_provider_user(
        request
    )

    if not provider_user:
        return None

    if provider_user.get("account_id") != account.get("id"):
        return None

    return provider_user


# ----------------------------------------------------------------------
# Redirect helpers
# ----------------------------------------------------------------------


def _redirect_with_message(
    path: str,
    message: str,
) -> RedirectResponse:
    """Redirect while preserving a user-facing message."""

    query = urlencode(
        {
            "message": message,
        }
    )

    return RedirectResponse(
        url=f"{path}?{query}",
        status_code=303,
    )


# ----------------------------------------------------------------------
# Account registration
# ----------------------------------------------------------------------


@app.post("/register")
async def register(
    request: Request,
) -> RedirectResponse:
    """
    Register a new MailPilot account.

    Registration requires:
    - email
    - password
    - activation key
    """

    try:
        form = await request.form()

        email = str(
            form.get("email", "")
        ).strip()

        password = str(
            form.get("password", "")
        )

        activation_key = str(
            form.get("activation_key", "")
        ).strip()

        if not email:
            return _redirect_with_message(
                "/",
                "Email address is required.",
            )

        if not password:
            return _redirect_with_message(
                "/",
                "Password is required.",
            )

        if not activation_key:
            return _redirect_with_message(
                "/",
                "Activation key is required.",
            )

        account_id = account_store.create_account(
            email=email,
            password=password,
        )

        activated = account_store.activate_key(
            activation_key=activation_key,
            account_id=account_id,
        )

        if not activated:
            return _redirect_with_message(
                "/",
                "Activation key is invalid, already used, "
                "or unavailable.",
            )

        response = RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

        _set_account_session(
            response,
            account_id,
        )

        return response

    except AccountStoreError as exc:
        return _redirect_with_message(
            "/",
            str(exc),
        )

    except Exception as exc:
        return _redirect_with_message(
            "/",
            f"Registration failed: {exc}",
        )


# ----------------------------------------------------------------------
# Login
# ----------------------------------------------------------------------


@app.post("/login")
async def login(
    request: Request,
) -> RedirectResponse:
    """Authenticate an existing MailPilot account."""

    try:
        form = await request.form()

        email = str(
            form.get("email", "")
        ).strip()

        password = str(
            form.get("password", "")
        )

        if not email or not password:
            return _redirect_with_message(
                "/",
                "Email and password are required.",
            )

        account = account_store.verify_login(
            email=email,
            password=password,
        )

        if not account:
            return _redirect_with_message(
                "/",
                "Invalid email or password.",
            )

        account_id = account["id"]

        if not account_store.is_activated(
            account_id
        ):
            return _redirect_with_message(
                "/",
                "This MailPilot account does not have "
                "an active activation key.",
            )

        response = RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

        _set_account_session(
            response,
            account_id,
        )

        return response

    except AccountStoreError as exc:
        return _redirect_with_message(
            "/",
            str(exc),
        )

    except Exception as exc:
        return _redirect_with_message(
            "/",
            f"Login failed: {exc}",
        )


# ----------------------------------------------------------------------
# Home
# ----------------------------------------------------------------------


@app.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request,
) -> HTMLResponse:
    """
    Display the MailPilot account page.

    Authenticated accounts are shown the provider-selection interface.
    Unauthenticated visitors receive registration/login forms.
    """

    current_account = _get_current_account(
        request
    )

    current_user = _get_current_user(
        request
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": "MailPilot",
            "current_account": current_account,
            "current_user": current_user,
            "gmail_connected": bool(
                current_user
                and current_user.get("provider") == "gmail"
            ),
            "outlook_connected": bool(
                current_user
                and current_user.get("provider") == "outlook"
            ),
            "connection_message": request.query_params.get(
                "message",
                "",
            ),
        },
    )


# ----------------------------------------------------------------------
# Gmail OAuth
# ----------------------------------------------------------------------


@app.get("/connect/gmail")
async def connect_gmail(
    request: Request,
) -> RedirectResponse:
    """
    Start Gmail web OAuth.

    Gmail OAuth is only available after a MailPilot account has been
    authenticated and activated.
    """

    current_account = _get_current_account(
        request
    )

    if not current_account:
        return _redirect_with_message(
            "/",
            "Please register or log in to MailPilot first.",
        )

    try:
        authenticator = GmailWebAuthenticator()

        state = secrets.token_urlsafe(32)

        authorization_url, code_verifier = (
            authenticator.create_authorization_url(
                state=state
            )
        )

        user_store.save_oauth_state(
            state=state,
            code_verifier=code_verifier,
            account_id=current_account["id"],
        )

        return RedirectResponse(
            url=authorization_url,
            status_code=302,
        )

    except (
        GmailWebAuthenticationError,
        UserStoreError,
    ) as exc:
        return _redirect_with_message(
            "/",
            f"Gmail connection failed: {exc}",
        )


@app.get("/auth/gmail/callback")
async def gmail_callback(
    request: Request,
) -> RedirectResponse:
    """
    Handle the Google OAuth callback.

    The callback requires a valid authenticated MailPilot account.
    """

    current_account = _get_current_account(
        request
    )

    if not current_account:
        return _redirect_with_message(
            "/",
            "Your MailPilot session is not active. "
            "Please log in again.",
        )

    error = request.query_params.get(
        "error"
    )

    if error:
        error_description = request.query_params.get(
            "error_description",
            error,
        )

        return _redirect_with_message(
            "/",
            "Gmail authorization was not completed: "
            f"{error_description}",
        )

    state = request.query_params.get(
        "state"
    )

    if not state:
        return _redirect_with_message(
            "/",
            "Gmail connection failed: missing OAuth state.",
        )

    try:
        oauth_state = user_store.consume_oauth_state_with_account(
            state
        )

    except UserStoreError as exc:
        return _redirect_with_message(
            "/",
            f"Gmail connection failed: {exc}",
        )

    if not oauth_state:
        return _redirect_with_message(
            "/",
            "Gmail connection failed: invalid or expired OAuth state.",
        )

    if oauth_state.get("account_id") != current_account["id"]:
        return _redirect_with_message(
            "/",
            "Gmail connection failed: OAuth state does not belong "
            "to this MailPilot account.",
        )

    code_verifier = oauth_state["code_verifier"]

    authorization_response = str(
        request.url
    )

    try:
        authenticator = GmailWebAuthenticator()

        credentials = (
            authenticator.exchange_code_for_credentials(
                authorization_response=authorization_response,
                state=state,
                code_verifier=code_verifier,
            )
        )

        service = build(
            "gmail",
            "v1",
            credentials=credentials,
            cache_discovery=False,
        )

        profile = (
            service.users()
            .getProfile(
                userId="me"
            )
            .execute()
        )

        email_address = profile.get(
            "emailAddress"
        )

        if not email_address:
            return _redirect_with_message(
                "/",
                "Gmail connection failed: Google did not "
                "return an email address.",
            )

        display_name = email_address

        user_id = user_store.get_or_create_user(
            provider="gmail",
            email=email_address,
            display_name=display_name,
            account_id=current_account["id"],
        )

        user_store.save_credentials(
            user_id=user_id,
            provider="gmail",
            credentials=credentials,
        )

        session_id = user_store.create_session(
            user_id=user_id
        )

        response = RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

        _set_provider_session(
            response,
            session_id,
        )

        _set_account_session(
            response,
            current_account["id"],
        )

        return response

    except (
        GmailWebAuthenticationError,
        UserStoreError,
    ) as exc:
        return _redirect_with_message(
            "/",
            f"Gmail connection failed: {exc}",
        )

    except Exception as exc:
        return _redirect_with_message(
            "/",
            f"Gmail connection failed: {exc}",
        )


# ----------------------------------------------------------------------
# Outlook OAuth
# ----------------------------------------------------------------------


@app.get("/connect/outlook")
async def connect_outlook(
    request: Request,
) -> RedirectResponse:
    """
    Start Microsoft Outlook web OAuth.

    Outlook OAuth is only available after a MailPilot account has been
    authenticated and activated.
    """

    current_account = _get_current_account(
        request
    )

    if not current_account:
        return _redirect_with_message(
            "/",
            "Please register or log in to MailPilot first.",
        )

    try:
        authenticator = OutlookWebAuthenticator()

        state = secrets.token_urlsafe(32)

        authorization_url, code_verifier = (
            authenticator.create_authorization_url(
                state=state
            )
        )

        user_store.save_oauth_state(
            state=state,
            code_verifier=code_verifier,
            account_id=current_account["id"],
        )

        return RedirectResponse(
            url=authorization_url,
            status_code=302,
        )

    except (
        OutlookWebAuthenticationError,
        UserStoreError,
    ) as exc:
        return _redirect_with_message(
            "/",
            f"Outlook connection failed: {exc}",
        )


@app.get("/auth/outlook/callback")
async def outlook_callback(
    request: Request,
) -> RedirectResponse:
    """
    Handle the Microsoft OAuth callback.

    The callback requires a valid authenticated MailPilot account.
    """

    current_account = _get_current_account(
        request
    )

    if not current_account:
        return _redirect_with_message(
            "/",
            "Your MailPilot session is not active. "
            "Please log in again.",
        )

    error = request.query_params.get(
        "error"
    )

    if error:
        error_description = request.query_params.get(
            "error_description",
            error,
        )

        return _redirect_with_message(
            "/",
            "Outlook authorization was not completed: "
            f"{error_description}",
        )

    state = request.query_params.get(
        "state"
    )

    if not state:
        return _redirect_with_message(
            "/",
            "Outlook connection failed: missing OAuth state.",
        )

    try:
        oauth_state = user_store.consume_oauth_state_with_account(
            state
        )

    except UserStoreError as exc:
        return _redirect_with_message(
            "/",
            f"Outlook connection failed: {exc}",
        )

    if not oauth_state:
        return _redirect_with_message(
            "/",
            "Outlook connection failed: invalid or expired OAuth state.",
        )

    if oauth_state.get("account_id") != current_account["id"]:
        return _redirect_with_message(
            "/",
            "Outlook connection failed: OAuth state does not belong "
            "to this MailPilot account.",
        )

    code_verifier = oauth_state["code_verifier"]

    authorization_code = request.query_params.get(
        "code"
    )

    if not authorization_code:
        return _redirect_with_message(
            "/",
            "Outlook connection failed: missing authorization code.",
        )

    try:
        authenticator = OutlookWebAuthenticator()

        token_result = (
            authenticator.exchange_code_for_credentials(
                authorization_code=authorization_code,
                code_verifier=code_verifier,
            )
        )

        access_token = token_result.get(
            "access_token"
        )

        if not access_token:
            return _redirect_with_message(
                "/",
                "Outlook connection failed: Microsoft did not "
                "return an access token.",
            )

        graph_client = MicrosoftGraphClient(
            access_token
        )

        profile = graph_client.get(
            "/me"
        )

        email_address = (
            profile.get("mail")
            or profile.get("userPrincipalName")
        )

        if not email_address:
            return _redirect_with_message(
                "/",
                "Outlook connection failed: Microsoft did not "
                "return an email address.",
            )

        display_name = (
            profile.get("displayName")
            or email_address
        )

        user_id = user_store.get_or_create_user(
            provider="outlook",
            email=email_address,
            display_name=display_name,
            account_id=current_account["id"],
        )

        user_store.save_provider_credentials(
            user_id=user_id,
            provider="outlook",
            credentials=token_result,
        )

        session_id = user_store.create_session(
            user_id=user_id
        )

        response = RedirectResponse(
            url="/dashboard",
            status_code=303,
        )

        _set_provider_session(
            response,
            session_id,
        )

        _set_account_session(
            response,
            current_account["id"],
        )

        return response

    except (
        OutlookWebAuthenticationError,
        UserStoreError,
        MailProviderError,
    ) as exc:
        return _redirect_with_message(
            "/",
            f"Outlook connection failed: {exc}",
        )

    except Exception as exc:
        return _redirect_with_message(
            "/",
            f"Outlook connection failed: {exc}",
        )


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------


@app.get(
    "/dashboard",
    response_class=HTMLResponse,
)
async def dashboard(
    request: Request,
) -> HTMLResponse:
    """
    Display the authenticated user's mailbox.

    This endpoint requires:
    1. A valid MailPilot account session.
    2. An active activation key.
    3. A connected provider session.

    The dashboard currently reads the inbox only. Mail organization
    actions remain in the provider/organizer layers.
    """

    current_account = _get_current_account(
        request
    )

    if not current_account:
        return _redirect_with_message(
            "/",
            "Please register or log in to MailPilot first.",
        )

    current_user = _get_current_user(
        request
    )

    if not current_user:
        return _redirect_with_message(
            "/",
            "Please connect your email provider first.",
        )

    provider_name = current_user.get(
        "provider",
        "",
    )

    if provider_name not in {
        "gmail",
        "outlook",
    }:
        return _redirect_with_message(
            "/",
            "Unsupported email provider.",
        )

    user_id = current_user["id"]

    messages: list[Any] = []
    inbox_error = ""

    try:
        if provider_name == "gmail":
            credentials = user_store.get_credentials(
                user_id=user_id,
                scopes=GmailWebAuthenticator.DEFAULT_SCOPES,
            )

            if not credentials:
                return _redirect_with_message(
                    "/",
                    "Gmail credentials were not found. "
                    "Please reconnect Gmail.",
                )

            # Refresh expired Gmail credentials for this user.
            if (
                credentials.expired
                and credentials.refresh_token
            ):
                credentials.refresh(
                    GoogleRequest()
                )

                user_store.save_credentials(
                    user_id=user_id,
                    provider="gmail",
                    credentials=credentials,
                )

            service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )

            provider = GmailProvider(
                service=service
            )

            messages = provider.list_inbox_messages(
                limit=50
            )

            provider_label = "Gmail"

        else:
            credentials = user_store.get_provider_credentials(
                user_id=user_id,
                provider="outlook",
            )

            if not credentials:
                return _redirect_with_message(
                    "/",
                    "Outlook credentials were not found. "
                    "Please reconnect Outlook.",
                )

            access_token = credentials.get(
                "access_token"
            )

            if not isinstance(
                access_token,
                str,
            ) or not access_token.strip():
                return _redirect_with_message(
                    "/",
                    "Outlook access token is missing. "
                    "Please reconnect Outlook.",
                )

            graph_client = MicrosoftGraphClient(
                access_token
            )

            provider = OutlookProvider(
                graph_client=graph_client
            )

            messages = provider.list_inbox_messages(
                limit=50
            )

            provider_label = "Outlook"

    except (
        MailProviderError,
        UserStoreError,
    ) as exc:
        inbox_error = str(exc)
        provider_label = (
            "Gmail"
            if provider_name == "gmail"
            else "Outlook"
        )

    except Exception as exc:
        inbox_error = (
            f"Failed to load {provider_name.title()} "
            f"inbox: {exc}"
        )
        provider_label = (
            "Gmail"
            if provider_name == "gmail"
            else "Outlook"
        )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": "MailPilot",
            "provider": provider_label,
            "status": "Connected",
            "messages": messages,
            "message_count": len(messages),
            "inbox_error": inbox_error,
            "current_account": current_account,
            "current_user": current_user,
        },
    )


# ----------------------------------------------------------------------
# Logout
# ----------------------------------------------------------------------


@app.get("/logout")
async def logout(
    request: Request,
) -> RedirectResponse:
    """Log out the MailPilot account and provider session."""

    provider_session_id = request.cookies.get(
        PROVIDER_SESSION_COOKIE_NAME
    )

    if provider_session_id:
        try:
            user_store.delete_session(
                provider_session_id
            )
        except UserStoreError:
            pass

    response = RedirectResponse(
        url="/",
        status_code=303,
    )

    response.delete_cookie(
        key=ACCOUNT_SESSION_COOKIE_NAME,
        path="/",
    )

    response.delete_cookie(
        key=PROVIDER_SESSION_COOKIE_NAME,
        path="/",
    )

    return response


# ----------------------------------------------------------------------
# Health
# ----------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a simple application health response."""

    return {
        "status": "ok",
        "application": "MailPilot",
    }