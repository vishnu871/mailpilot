from __future__ import annotations

import pytest

from app.classifier.resolver import OrganizationResolver


class FakeMappingDatabase:
    """Small in-memory database substitute for resolver unit tests."""

    def __init__(self, mappings=None):
        self.mappings = {
            domain.lower(): mapping
            for domain, mapping in (mappings or {}).items()
        }

    def get_mapping(self, domain):
        return self.mappings.get(domain.lower())


class FakeMapping:
    """Minimal mapping object matching the resolver's requirements."""

    def __init__(
        self,
        domain,
        organization,
        folder_name,
        confidence="HIGH",
        reason=None,
    ):
        self.domain = domain
        self.organization = organization
        self.folder_name = folder_name
        self.confidence = confidence
        self.reason = reason


def create_resolver(mappings=None):
    return OrganizationResolver(
        database=FakeMappingDatabase(mappings)
    )


def test_normalize_email():
    result = OrganizationResolver.normalize_email(
        "  USER@Example.COM  "
    )

    assert result == "user@example.com"


def test_normalize_email_rejects_empty_email():
    with pytest.raises(ValueError, match="cannot be empty"):
        OrganizationResolver.normalize_email("   ")


def test_normalize_email_rejects_multiple_at_symbols():
    with pytest.raises(
        ValueError,
        match="exactly one '@'",
    ):
        OrganizationResolver.normalize_email(
            "user@example@company.com"
        )


def test_normalize_email_rejects_missing_local_part():
    with pytest.raises(
        ValueError,
        match="local part",
    ):
        OrganizationResolver.normalize_email("@example.com")


def test_normalize_email_rejects_missing_domain():
    with pytest.raises(
        ValueError,
        match="domain",
    ):
        OrganizationResolver.normalize_email("user@")


def test_extract_domain():
    result = OrganizationResolver.extract_domain(
        "Person@Example.COM"
    )

    assert result == "example.com"


@pytest.mark.parametrize(
    "domain",
    [
        "gmail.com",
        "GMAIL.COM",
        "outlook.com",
        "hotmail.com",
        "yahoo.com",
        "icloud.com",
        "proton.me",
    ],
)
def test_generic_email_provider_is_detected(domain):
    assert OrganizationResolver.is_generic_email_provider(domain)


def test_business_domain_is_not_generic_provider():
    assert not OrganizationResolver.is_generic_email_provider(
        "example.com"
    )


def test_unknown_business_domain_returns_none():
    resolver = create_resolver()

    result = resolver.resolve("person@example.com")

    assert result is None


def test_generic_email_provider_returns_none():
    resolver = create_resolver()

    result = resolver.resolve("person@gmail.com")

    assert result is None


def test_exact_domain_mapping_is_used():
    resolver = create_resolver(
        {
            "example.com": FakeMapping(
                domain="example.com",
                organization="Example Corporation",
                folder_name="Example Corporation",
                confidence="HIGH",
                reason="Known company domain",
            )
        }
    )

    result = resolver.resolve("person@EXAMPLE.COM")

    assert result is not None
    assert result.domain == "example.com"
    assert result.organization == "Example Corporation"
    assert result.folder_name == "Example Corporation"
    assert result.source == "exact_domain_mapping"
    assert result.confidence == "HIGH"
    assert "existing organization mapping" in result.reason


def test_subdomain_uses_parent_domain_mapping():
    resolver = create_resolver(
        {
            "example.com": FakeMapping(
                domain="example.com",
                organization="Example Corporation",
                folder_name="Example Corporation",
                confidence="HIGH",
            )
        }
    )

    result = resolver.resolve(
        "alerts@mail.example.com"
    )

    assert result is not None
    assert result.domain == "mail.example.com"
    assert result.organization == "Example Corporation"
    assert result.folder_name == "Example Corporation"
    assert result.source == "parent_domain_mapping"
    assert "example.com" in result.reason


def test_exact_mapping_takes_priority_over_parent_mapping():
    resolver = create_resolver(
        {
            "example.com": FakeMapping(
                domain="example.com",
                organization="Parent Corporation",
                folder_name="Parent Corporation",
                confidence="HIGH",
            ),
            "mail.example.com": FakeMapping(
                domain="mail.example.com",
                organization="Specific Corporation",
                folder_name="Specific Corporation",
                confidence="HIGH",
            ),
        }
    )

    result = resolver.resolve(
        "person@mail.example.com"
    )

    assert result is not None
    assert result.organization == "Specific Corporation"
    assert result.folder_name == "Specific Corporation"
    assert result.source == "exact_domain_mapping"


def test_parent_mapping_is_not_used_for_generic_provider():
    resolver = create_resolver(
        {
            "gmail.com": FakeMapping(
                domain="gmail.com",
                organization="Gmail",
                folder_name="Gmail",
                confidence="HIGH",
            )
        }
    )

    result = resolver.resolve("person@gmail.com")

    assert result is None


def test_parent_domains_are_generated_correctly():
    result = OrganizationResolver._parent_domains(
        "alerts.mail.example.com"
    )

    assert result == [
        "mail.example.com",
        "example.com",
    ]


def test_parent_domains_for_root_domain_are_empty():
    result = OrganizationResolver._parent_domains(
        "example.com"
    )

    assert result == []


def test_resolution_preserves_database_confidence():
    resolver = create_resolver(
        {
            "example.com": FakeMapping(
                domain="example.com",
                organization="Example",
                folder_name="Example",
                confidence="MEDIUM",
            )
        }
    )

    result = resolver.resolve("person@example.com")

    assert result is not None
    assert result.confidence == "MEDIUM"


def test_resolution_preserves_database_reason_when_building_result():
    resolver = create_resolver(
        {
            "example.com": FakeMapping(
                domain="example.com",
                organization="Example",
                folder_name="Example",
                confidence="HIGH",
                reason="Manually confirmed",
            )
        }
    )

    result = resolver.resolve("person@example.com")

    assert result is not None
    assert result.reason == (
        "Domain 'example.com' matches an existing "
        "organization mapping."
    )


def test_domain_matching_is_case_insensitive():
    resolver = create_resolver(
        {
            "Example.COM": FakeMapping(
                domain="example.com",
                organization="Example",
                folder_name="Example",
                confidence="HIGH",
            )
        }
    )

    result = resolver.resolve("person@example.com")

    assert result is not None
    assert result.organization == "Example"


def test_subdomain_resolution_avoids_generic_parent_domain():
    resolver = create_resolver(
        {
            "com": FakeMapping(
                domain="com",
                organization="Incorrect",
                folder_name="Incorrect",
                confidence="HIGH",
            )
        }
    )

    result = resolver.resolve("person@example.com")

    assert result is None