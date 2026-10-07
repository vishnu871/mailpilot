"""
MailPilot — Organizer Web Routes

Web endpoints for explicitly running mailbox organization.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter()


@router.post(
    "/organize/outlook",
    response_class=HTMLResponse,
)
async def organize_outlook(
    request: Request,
) -> HTMLResponse:
    """
    Run the Outlook organizer in DRY RUN mode.

    This endpoint deliberately performs no mailbox mutations.
    """

    from app.web.app import (
        MicrosoftGraphClient,
        _get_current_account,
        _get_current_user,
        _redirect_with_message,
        account_store,
        templates,
        user_store,
    )

    from app.mail.outlook.provider import OutlookProvider
    from app.organizer.runtime import run_outlook_organization

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

    if provider_name != "outlook":
        return _redirect_with_message(
            "/dashboard",
            "Outlook must be the connected provider "
            "to run Outlook organization.",
        )

    user_id = current_user["id"]

    try:
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

        if (
            not isinstance(access_token, str)
            or not access_token.strip()
        ):
            return _redirect_with_message(
                "/",
                "Outlook access token is missing. "
                "Please reconnect Outlook.",
            )

        results = run_outlook_organization(
            access_token=access_token,
            dry_run=False,
            limit=50,
            enable_learning=False,
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

        result_by_message_id = {
            result.decision.message_id: result
            for result in results
        }

        organization_results = []

        for message in messages:
            result = result_by_message_id.get(
                message.id
            )

            if result is not None:
                organization_results.append(
                    {
                        "message": message,
                        "result": result,
                    }
                )

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "app_name": "MailPilot",
                "provider": "Outlook",
                "status": "Connected",
                "messages": messages,
                "message_count": len(messages),
                "inbox_error": "",
                "current_account": current_account,
                "current_user": current_user,
                "organization_results": organization_results,
                "organization_run": True,
                "organization_error": "",
            },
        )

    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "app_name": "MailPilot",
                "provider": "Outlook",
                "status": "Connected",
                "messages": [],
                "message_count": 0,
                "inbox_error": "",
                "current_account": current_account,
                "current_user": current_user,
                "organization_results": [],
                "organization_run": False,
                "organization_error": str(exc),
            },
        )