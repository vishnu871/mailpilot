"""

MailPilot — AI Classification Orchestrator



Coordinates deterministic organization resolution, AI classification,

and trusted organization learning.



Flow:



1. Check known organization mappings.

2. If known, use the deterministic mapping and skip AI.

3. If unknown, ask the AI provider.

4. Validate the AI response.

5. Automatically learn HIGH-confidence classifications.

6. Never automatically learn MEDIUM or LOW classifications.



This component never moves, deletes, or modifies emails.

"""



from __future__ import annotations



from dataclasses import dataclass

from typing import Optional



from app.classifier.base import ClassifierError

from app.classifier.learning import LearningManager

from app.classifier.resolver import (

    OrganizationResolution,

    OrganizationResolver,

    OrganizationResolverError,

)

from app.classifier.providers.ai_provider import (

    AIClassificationRequest,

    AIClassificationResponse,

    AIProvider,

    AIProviderError,

)

from app.mail.models import (

    ClassificationConfidence,

    EmailMessage,

    OrganizationClassification,

)





_RESERVED_FOLDER_NAMES = frozenset(
    {
        "inbox",
        "outbox",
        "sent",
        "sent items",
        "drafts",
        "deleted items",
        "deleted",
        "trash",
        "junk",
        "junk email",
        "spam",
        "archive",
        "all mail",
        "important",
        "starred",
        "conversation history",
        "clutter",
    }
)


class AIClassificationError(ClassifierError):

    """Raised when AI classification cannot be completed safely."""





@dataclass(frozen=True)

class ClassificationResult:

    """Complete result of the classification orchestration process."""



    classification: OrganizationClassification

    source: str

    ai_used: bool

    learned: bool = False





class AIClassificationOrchestrator:

    """

    Combines deterministic resolution, AI classification,

    and organization learning.



    Deterministic knowledge always takes priority over AI.

    """



    def __init__(

        self,

        resolver: OrganizationResolver,

        ai_provider: AIProvider,

        learning_manager: Optional[LearningManager] = None,

    ) -> None:

        self.resolver = resolver

        self.ai_provider = ai_provider

        self.learning_manager = learning_manager



    def classify(

        self,

        message: EmailMessage,

        existing_mappings=None,

        existing_folders: Optional[

            list[str] | tuple[str, ...]

        ] = None,

    ) -> ClassificationResult:

        """

        Classify an email.



        Args:

            message:

                Email being classified.



            existing_mappings:

                Accepted for compatibility with the EmailClassifier

                interface. Deterministic organization resolution is

                handled by OrganizationResolver, so this parameter

                is not required by the orchestrator.



            existing_folders:

                Current mailbox folder names available to the AI provider.



        Resolution order:



        1. Deterministic organization resolver.

        2. AI provider if no deterministic result exists.

        3. Automatic learning for eligible HIGH-confidence AI results.

        4. Safe LOW-confidence fallback if AI fails.

        """

        del existing_mappings



        folders = tuple(existing_folders or ())



        try:

            deterministic_result = self.resolver.resolve(

                message.sender_email

            )

        except (

            ValueError,

            OrganizationResolverError,

        ):

            deterministic_result = None



        if deterministic_result is not None:

            classification = self._classification_from_resolution(

                deterministic_result

            )



            return ClassificationResult(

                classification=classification,

                source=deterministic_result.source,

                ai_used=False,

                learned=False,

            )



        return self._classify_with_ai(

            message=message,

            existing_folders=folders,

        )



    def _classify_with_ai(

        self,

        message: EmailMessage,

        existing_folders: tuple[str, ...],

    ) -> ClassificationResult:

        """Classify an unresolved email using the configured AI provider."""



        request = AIClassificationRequest(

            message=message,

            existing_folders=existing_folders,

            known_domain_mapping=None,

        )



        try:

            response = self.ai_provider.classify(request)



        except AIProviderError as exc:

            return self._safe_failure_result(

                reason=f"AI provider failed: {exc}"

            )



        except Exception as exc:

            return self._safe_failure_result(

                reason=f"Unexpected AI provider failure: {exc}"

            )



        try:

            classification = self._validate_ai_response(

                message=message,

                response=response,

            )



        except AIClassificationError as exc:

            return self._safe_failure_result(

                reason=str(exc)

            )



        learned = False



        if self.learning_manager is not None:

            try:

                learned = self.learning_manager.learn(

                    classification

                )

            except Exception:

                # Learning must never cause classification itself to fail.

                learned = False



        return ClassificationResult(

            classification=classification,

            source="ai_provider",

            ai_used=True,

            learned=learned,

        )



    def _validate_ai_response(

        self,

        message: EmailMessage,

        response: AIClassificationResponse,

    ) -> OrganizationClassification:

        """

        Validate and convert an AI response.



        AI output is treated as untrusted input. It must satisfy

        deterministic validation before entering the application.

        """

        confidence = self._normalize_confidence(

            response.confidence

        )



        organization = self._normalize_optional_text(

            response.organization

        )



        domain = self._normalize_optional_text(

            response.domain

        )



        folder_name = self._normalize_optional_text(

            response.folder_name

        )



        reason = self._normalize_optional_text(

            response.reason

        )



        if not reason:

            raise AIClassificationError(

                "AI response must include a reason."

            )



        expected_domain = self.resolver.extract_domain(

            message.sender_email

        )



        if domain is None:

            domain = expected_domain



        if domain.lower() != expected_domain.lower():

            raise AIClassificationError(

                "AI response domain does not match the "

                "sender email domain."

            )



        if folder_name and folder_name.casefold() in _RESERVED_FOLDER_NAMES:
            raise AIClassificationError(
                "AI response proposes a reserved mail-system folder "
                "as an organization destination."
            )


        if confidence == ClassificationConfidence.HIGH:

            if not organization:

                raise AIClassificationError(

                    "HIGH-confidence AI classification requires "

                    "an organization."

                )



            if not folder_name:

                raise AIClassificationError(

                    "HIGH-confidence AI classification requires "

                    "a folder name."

                )



        if confidence == ClassificationConfidence.MEDIUM:

            if not organization:

                raise AIClassificationError(

                    "MEDIUM-confidence AI classification requires "

                    "an organization."

                )



        return OrganizationClassification(

            organization=organization,

            domain=domain,

            folder_name=folder_name,

            confidence=confidence,

            reason=reason,

        )



    @staticmethod

    def _classification_from_resolution(

        resolution: OrganizationResolution,

    ) -> OrganizationClassification:

        """Convert deterministic resolution to application classification."""



        confidence = AIClassificationOrchestrator._normalize_confidence(

            resolution.confidence

        )



        return OrganizationClassification(

            organization=resolution.organization,

            domain=resolution.domain,

            folder_name=resolution.folder_name,

            confidence=confidence,

            reason=resolution.reason,

        )



    @staticmethod

    def _normalize_optional_text(

        value: Optional[str],

    ) -> Optional[str]:

        """Normalize optional text values."""



        if value is None:

            return None



        normalized = value.strip()



        return normalized if normalized else None



    @staticmethod

    def _normalize_confidence(

        confidence: str,

    ) -> ClassificationConfidence:

        """Convert confidence text into the application's enum."""



        if isinstance(

            confidence,

            ClassificationConfidence,

        ):

            return confidence



        if not isinstance(confidence, str):

            raise AIClassificationError(

                "AI response confidence must be a string."

            )



        normalized = confidence.strip().lower()



        try:

            return ClassificationConfidence(normalized)

        except ValueError as exc:

            raise AIClassificationError(

                "Confidence must be HIGH, MEDIUM, or LOW."

            ) from exc



    @staticmethod

    def _safe_failure_result(

        reason: str,

    ) -> ClassificationResult:

        """

        Produce a safe LOW-confidence result.



        No organization or destination folder is proposed when

        classification fails.

        """

        classification = OrganizationClassification(

            organization=None,

            domain="unknown",

            folder_name=None,

            confidence=ClassificationConfidence.LOW,

            reason=reason,

        )



        return ClassificationResult(

            classification=classification,

            source="safe_fallback",

            ai_used=False,

            learned=False,

        )