"""
MailPilot — Ollama AI Provider

Real local AI provider for MailPilot.

Responsibilities:
- Send email classification requests to a local Ollama server.
- Ask the model to identify the organization and domain.
- Parse JSON from the model response.
- Validate the response before returning it.

Safety:
- This provider only classifies emails.
- It never creates folders.
- It never moves messages.
- It never modifies the mailbox.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from app.classifier.providers.ai_provider import (
    AIClassificationRequest,
    AIClassificationResponse,
    AIProvider,
    AIProviderConfigurationError,
    AIProviderResponseError,
)


class OllamaAIProvider(AIProvider):
    """
    AIProvider implementation using a local Ollama server.
    """

    BODY_PREVIEW_LIMIT = 600
    MAX_OUTPUT_TOKENS = 120

    def __init__(
        self,
        *,
        model: str = "qwen2.5vl:3b",
        base_url: str = "http://localhost:11434",
        timeout: int = 120,
    ) -> None:
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        if not self.model:
            raise AIProviderConfigurationError(
                "Ollama model must not be empty."
            )

        if not self.base_url:
            raise AIProviderConfigurationError(
                "Ollama base URL must not be empty."
            )

        if self.timeout <= 0:
            raise AIProviderConfigurationError(
                "Ollama timeout must be greater than zero."
            )

    def classify(
        self,
        request: AIClassificationRequest,
    ) -> AIClassificationResponse:
        """
        Classify one email using the configured local Ollama model.
        """

        prompt = self._build_prompt(request)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "num_predict": self.MAX_OUTPUT_TOKENS,
            },
        }

        endpoint = f"{self.base_url}/api/generate"

        try:
            response = requests.post(
                endpoint,
                json=payload,
                timeout=self.timeout,
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            raise AIProviderResponseError(
                "Failed to communicate with Ollama: "
                f"{exc}"
            ) from exc

        try:
            response_data = response.json()

        except ValueError as exc:
            raise AIProviderResponseError(
                "Ollama returned a response that was not valid JSON."
            ) from exc

        raw_model_response = response_data.get("response")

        if not isinstance(raw_model_response, str):
            raise AIProviderResponseError(
                "Ollama response did not contain a valid 'response' field."
            )

        classification = self._parse_response(
            raw_model_response
        )

        # The sender email is authoritative for the domain.
        #
        # The AI is responsible for identifying the organization,
        # confidence, folder name, and reason. It must not be trusted
        # to rewrite structured sender metadata.
        sender_domain = self._extract_sender_domain(
            request.message.sender_email
        )

        if sender_domain:
            classification = AIClassificationResponse(
                organization=classification.organization,
                domain=sender_domain,
                folder_name=classification.folder_name,
                confidence=classification.confidence,
                reason=classification.reason,
            )

        return classification

    @classmethod
    def _build_prompt(
        cls,
        request: AIClassificationRequest,
    ) -> str:
        """
        Build a compact, constrained classification prompt.

        The model is given the strongest available organization signals:
        sender identity, sender domain, subject, and a short body preview.

        Folder names are deliberately derived from the organization
        rather than freely invented by the model.
        """

        message = request.message

        existing_folders = ", ".join(
            request.existing_folders
        ) or "None"

        known_mapping = (
            request.known_domain_mapping
            if request.known_domain_mapping
            else "None"
        )

        body_preview = (
            message.body_preview or "No body preview"
        )[: cls.BODY_PREVIEW_LIMIT]

        return f"""
You are the classification component of MailPilot, an AI Outlook
email organizer.

Your ONLY task is to identify the organization associated with the
email sender.

The destination folder represents the ORGANIZATION, not the email topic.
Do NOT create topic folders such as Welcome, Accounts, Newsletters,
Invoices, Promotions, Support, Jobs, or Notifications.

Rules:

1. Identify the organization primarily from the sender email domain,
   then use sender name, subject, and body preview as supporting evidence.
2. The domain MUST be only the domain portion of the sender email.
3. NEVER return the full sender email address in the domain field.
4. folder_name MUST be the organization name.
5. confidence MUST be exactly HIGH, MEDIUM, or LOW.
6. Use HIGH only when the organization is strongly supported.
7. Use MEDIUM when the organization is plausible but not certain.
8. Use LOW when the organization cannot be reliably determined.
9. For generic personal email providers such as Gmail, Yahoo, Outlook,
   Hotmail, iCloud, ProtonMail, etc., do not pretend the provider is
   the organization. Use LOW unless the email content provides strong
   evidence for a specific organization.
10. Return ONLY one valid JSON object.
11. Do not return Markdown.
12. Do not return explanations outside the JSON object.
13. Return the JSON immediately.
14. Keep the reason brief.

Existing Outlook folders:

{existing_folders}

Known domain mapping:

{known_mapping}

Email:

Sender name: {message.sender_name or "Unknown"}
Sender email: {message.sender_email}
Subject: {message.subject or "No subject"}
Body preview: {body_preview}

Required JSON format:

{{
  "organization": "Organization Name or null",
  "domain": "google.com",
  "folder_name": "Organization Name or null",
  "confidence": "HIGH",
  "reason": "Brief reason for the classification."
}}
""".strip()

    @staticmethod
    def _extract_sender_domain(
        sender_email: str,
    ) -> str | None:
        """
        Extract the authoritative domain from a sender email address.

        The sender email is structured provider data, so its domain is
        preferred over any domain string generated by the AI model.
        """

        if not isinstance(sender_email, str):
            return None

        normalized = sender_email.strip().lower()

        if "@" not in normalized:
            return None

        local_part, domain = normalized.rsplit(
            "@",
            1,
        )

        if not local_part or not domain:
            return None

        if "." not in domain:
            return None

        return domain

    @staticmethod
    def _parse_response(
        raw_response: str,
    ) -> AIClassificationResponse:
        """
        Extract and validate the JSON object returned by Ollama.
        """

        json_data = OllamaAIProvider._extract_json(
            raw_response
        )

        if not isinstance(json_data, dict):
            raise AIProviderResponseError(
                "Ollama classification response must be a JSON object."
            )

        organization = OllamaAIProvider._optional_string(
            json_data,
            "organization",
        )

        domain = OllamaAIProvider._optional_string(
            json_data,
            "domain",
        )

        folder_name = OllamaAIProvider._optional_string(
            json_data,
            "folder_name",
        )

        confidence = OllamaAIProvider._required_string(
            json_data,
            "confidence",
        ).upper()

        reason = OllamaAIProvider._required_string(
            json_data,
            "reason",
        )

        if confidence not in {
            "HIGH",
            "MEDIUM",
            "LOW",
        }:
            raise AIProviderResponseError(
                "Ollama returned an invalid confidence value: "
                f"'{confidence}'."
            )

        if confidence == "HIGH":
            if not organization:
                raise AIProviderResponseError(
                    "HIGH confidence requires an organization."
                )

            if not domain:
                raise AIProviderResponseError(
                    "HIGH confidence requires a domain."
                )

            if not folder_name:
                raise AIProviderResponseError(
                    "HIGH confidence requires a folder name."
                )

        if confidence == "MEDIUM" and not organization:
            raise AIProviderResponseError(
                "MEDIUM confidence requires an organization."
            )

        if domain is not None:
            domain = domain.strip().lower()

        if organization is not None:
            organization = organization.strip()

        if folder_name is not None:
            folder_name = folder_name.strip()

        reason = reason.strip()

        if not reason:
            raise AIProviderResponseError(
                "Ollama classification response requires a reason."
            )

        return AIClassificationResponse(
            organization=organization,
            domain=domain,
            folder_name=folder_name,
            confidence=confidence,
            reason=reason,
        )

    @staticmethod
    def _extract_json(
        raw_response: str,
    ) -> dict[str, Any]:
        """
        Extract the first valid JSON object from the model output.
        """

        text = raw_response.strip()

        if not text:
            raise AIProviderResponseError(
                "Ollama returned an empty classification response."
            )

        decoder = json.JSONDecoder()

        for index, character in enumerate(text):
            if character != "{":
                continue

            candidate = text[index:]

            try:
                parsed, _ = decoder.raw_decode(
                    candidate
                )

            except json.JSONDecodeError:
                continue

            if isinstance(parsed, dict):
                return parsed

        raise AIProviderResponseError(
            "Could not find a valid JSON classification object "
            "in the Ollama response."
        )

    @staticmethod
    def _required_string(
        data: dict[str, Any],
        field_name: str,
    ) -> str:
        value = data.get(field_name)

        if not isinstance(value, str):
            raise AIProviderResponseError(
                f"Ollama response field '{field_name}' must be a string."
            )

        value = value.strip()

        if not value:
            raise AIProviderResponseError(
                f"Ollama response field '{field_name}' must not be empty."
            )

        return value

    @staticmethod
    def _optional_string(
        data: dict[str, Any],
        field_name: str,
    ) -> str | None:
        value = data.get(field_name)

        if value is None:
            return None

        if not isinstance(value, str):
            raise AIProviderResponseError(
                f"Ollama response field '{field_name}' must be "
                "a string or null."
            )

        value = value.strip()

        return value or None