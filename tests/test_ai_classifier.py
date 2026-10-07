from __future__ import annotations



from pathlib import Path



from app.classifier.ai import AIClassificationOrchestrator

from app.classifier.providers.mock import MockAIProvider

from app.classifier.providers.ai_provider import (

    AIClassificationResponse,

)

from app.classifier.resolver import OrganizationResolver

from app.mail.models import (

    ClassificationConfidence,

    EmailMessage,

)

from database.manager import DatabaseManager





def create_message(

    message_id: str = "message-123",

    sender_email: str = "person@example.com",

) -> EmailMessage:

    return EmailMessage(

        id=message_id,

        sender_email=sender_email,

        sender_name="Example User",

        subject="Project update",

        body_preview="Here is the latest project update.",

        is_read=False,

        has_attachments=False,

    )





def create_system(tmp_path: Path):

    database = DatabaseManager(

        database_path=tmp_path / "mailpilot.db",

        schema_path="database/schema.sql",

    )



    database.initialize()



    resolver = OrganizationResolver(database)



    return database, resolver





def create_response(

    organization="Example Corporation",

    domain="example.com",

    folder_name="Example Corporation",

    confidence="HIGH",

    reason="The sender appears to belong to Example Corporation.",

):

    return AIClassificationResponse(

        organization=organization,

        domain=domain,

        folder_name=folder_name,

        confidence=confidence,

        reason=reason,

    )





def test_known_domain_bypasses_ai(tmp_path):

    database, resolver = create_system(tmp_path)



    database.save_mapping(

        domain="example.com",

        organization="Example Corporation",

        folder_name="Example Corporation",

        confidence="HIGH",

    )



    provider = MockAIProvider(

        default_response=create_response(

            organization="Wrong AI Organization"

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.ai_used is False

    assert result.source == "exact_domain_mapping"

    assert result.classification.organization == (

        "Example Corporation"

    )

    assert provider.classification_count == 0





def test_known_parent_domain_bypasses_ai(tmp_path):

    database, resolver = create_system(tmp_path)



    database.save_mapping(

        domain="example.com",

        organization="Example Corporation",

        folder_name="Example Corporation",

        confidence="HIGH",

    )



    provider = MockAIProvider(

        default_response=create_response(

            organization="Wrong AI Organization",

            domain="mail.example.com",

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message(

            sender_email="alerts@mail.example.com"

        )

    )



    assert result.ai_used is False

    assert result.source == "parent_domain_mapping"

    assert result.classification.organization == (

        "Example Corporation"

    )

    assert provider.classification_count == 0





def test_unknown_domain_uses_ai(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response()

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.ai_used is True

    assert result.source == "ai_provider"

    assert result.classification.organization == (

        "Example Corporation"

    )

    assert provider.classification_count == 1





def test_high_confidence_ai_result_is_accepted(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            confidence="HIGH"

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.classification.confidence == (

        ClassificationConfidence.HIGH

    )

    assert result.classification.organization == (

        "Example Corporation"

    )

    assert result.classification.folder_name == (

        "Example Corporation"

    )





def test_medium_confidence_ai_result_is_accepted(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            confidence="MEDIUM"

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.classification.confidence == (

        ClassificationConfidence.MEDIUM

    )

    assert result.classification.organization == (

        "Example Corporation"

    )





def test_low_confidence_ai_result_is_accepted(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            organization=None,

            folder_name=None,

            confidence="LOW",

            reason="Insufficient evidence.",

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )

    assert result.classification.organization is None

    assert result.classification.folder_name is None





def test_ai_domain_mismatch_is_rejected_safely(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            domain="different-company.com"

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.ai_used is False

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )

    assert result.classification.organization is None

    assert result.classification.folder_name is None





def test_high_confidence_without_organization_is_rejected(

    tmp_path,

):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            organization=None,

            folder_name="Example Corporation",

            confidence="HIGH",

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )

    assert result.classification.organization is None





def test_high_confidence_without_folder_is_rejected(

    tmp_path,

):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            organization="Example Corporation",

            folder_name=None,

            confidence="HIGH",

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )





def test_medium_confidence_without_organization_is_rejected(

    tmp_path,

):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            organization=None,

            folder_name=None,

            confidence="MEDIUM",

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )





def test_missing_reason_is_rejected(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            reason="   "

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )





def test_invalid_confidence_is_rejected(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            confidence="CERTAIN"

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )





def test_ai_failure_returns_safe_fallback(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider()



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.source == "safe_fallback"

    assert result.ai_used is False

    assert result.classification.confidence == (

        ClassificationConfidence.LOW

    )

    assert result.classification.organization is None

    assert result.classification.folder_name is None





def test_reserved_system_folder_is_rejected_safely(tmp_path):
    _, resolver = create_system(tmp_path)

    provider = MockAIProvider(
        default_response=create_response(
            organization="Microsoft",
            domain="microsoft.com",
            folder_name="Outbox",
            confidence="HIGH",
            reason="The sender belongs to Microsoft.",
        )
    )

    orchestrator = AIClassificationOrchestrator(
        resolver=resolver,
        ai_provider=provider,
    )

    result = orchestrator.classify(
        create_message(sender_email="no-reply@microsoft.com")
    )

    assert result.source == "safe_fallback"
    assert result.ai_used is False
    assert result.classification.confidence == ClassificationConfidence.LOW
    assert result.classification.organization is None
    assert result.classification.folder_name is None


def test_existing_folders_are_passed_to_ai(tmp_path):

    _, resolver = create_system(tmp_path)



    captured = {}



    class CapturingProvider(MockAIProvider):

        def classify(self, request):

            captured["folders"] = request.existing_folders

            return super().classify(request)



    provider = CapturingProvider(

        default_response=create_response()

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    orchestrator.classify(

        create_message(),

        existing_folders=[

            "Example Corporation",

            "Finance",

            "Personal",

        ],

    )



    assert captured["folders"] == (

        "Example Corporation",

        "Finance",

        "Personal",

    )





def test_ai_domain_is_normalized(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            domain="EXAMPLE.COM"

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.classification.domain == "example.com"





def test_ai_text_fields_are_trimmed(tmp_path):

    _, resolver = create_system(tmp_path)



    provider = MockAIProvider(

        default_response=create_response(

            organization="  Example Corporation  ",

            folder_name="  Example Corporation  ",

            reason="  Valid classification.  ",

        )

    )



    orchestrator = AIClassificationOrchestrator(

        resolver=resolver,

        ai_provider=provider,

    )



    result = orchestrator.classify(

        create_message()

    )



    assert result.classification.organization == (

        "Example Corporation"

    )

    assert result.classification.folder_name == (

        "Example Corporation"

    )

    assert result.classification.reason == (

        "Valid classification."

    )