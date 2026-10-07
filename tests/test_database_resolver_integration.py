from __future__ import annotations

from database.manager import DatabaseManager
from app.classifier.resolver import OrganizationResolver


def create_test_system(tmp_path):
    """Create a real SQLite database and resolver."""
    database_path = tmp_path / "mailpilot.db"

    database = DatabaseManager(
        database_path=database_path,
        schema_path="database/schema.sql",
    )

    database.initialize()

    resolver = OrganizationResolver(database)

    return database, resolver


def test_database_and_resolver_work_together(tmp_path):
    database, resolver = create_test_system(tmp_path)

    database.save_mapping(
        domain="acme.com",
        organization="Acme Corporation",
        folder_name="Acme Corporation",
        confidence="HIGH",
        reason="Confirmed organization domain",
    )

    result = resolver.resolve("employee@acme.com")

    assert result is not None
    assert result.domain == "acme.com"
    assert result.organization == "Acme Corporation"
    assert result.folder_name == "Acme Corporation"
    assert result.source == "exact_domain_mapping"
    assert result.confidence == "HIGH"


def test_database_and_resolver_support_subdomains(tmp_path):
    database, resolver = create_test_system(tmp_path)

    database.save_mapping(
        domain="acme.com",
        organization="Acme Corporation",
        folder_name="Acme Corporation",
        confidence="HIGH",
    )

    result = resolver.resolve(
        "alerts@mail.acme.com"
    )

    assert result is not None
    assert result.domain == "mail.acme.com"
    assert result.organization == "Acme Corporation"
    assert result.folder_name == "Acme Corporation"
    assert result.source == "parent_domain_mapping"


def test_unknown_domain_does_not_create_mapping(
    tmp_path,
):
    database, resolver = create_test_system(tmp_path)

    result = resolver.resolve(
        "employee@unknown-company.com"
    )

    assert result is None
    assert database.list_mappings() == []


def test_generic_email_provider_is_not_stored_as_organization(
    tmp_path,
):
    database, resolver = create_test_system(tmp_path)

    result = resolver.resolve(
        "person@gmail.com"
    )

    assert result is None
    assert database.list_mappings() == []


def test_updated_mapping_is_used_by_resolver(
    tmp_path,
):
    database, resolver = create_test_system(tmp_path)

    database.save_mapping(
        domain="example.com",
        organization="Old Organization",
        folder_name="Old Organization",
        confidence="MEDIUM",
    )

    database.save_mapping(
        domain="example.com",
        organization="New Organization",
        folder_name="New Organization",
        confidence="HIGH",
        reason="Confirmed by user",
    )

    result = resolver.resolve(
        "person@example.com"
    )

    assert result is not None
    assert result.organization == "New Organization"
    assert result.folder_name == "New Organization"
    assert result.confidence == "HIGH"

    mappings = database.list_mappings()

    assert len(mappings) == 1