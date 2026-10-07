from __future__ import annotations

import sqlite3

import pytest

from database.manager import (
    DatabaseManager,
    DatabaseOperationError,
)


def create_database(tmp_path):
    """Create an isolated MailPilot database for testing."""
    database_path = tmp_path / "mailpilot.db"

    manager = DatabaseManager(
        database_path=database_path,
        schema_path="database/schema.sql",
    )

    manager.initialize()

    return manager


def test_initialize_creates_database(tmp_path):
    manager = create_database(tmp_path)

    assert manager.database_path.exists()


def test_initialize_creates_expected_tables(tmp_path):
    manager = create_database(tmp_path)

    with sqlite3.connect(manager.database_path) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        ).fetchall()

    tables = {row[0] for row in rows}

    assert "database_metadata" in tables
    assert "organization_mappings" in tables
    assert "processing_history" in tables


def test_schema_version_is_available(tmp_path):
    manager = create_database(tmp_path)

    assert manager.get_schema_version() == "1"


def test_get_mapping_returns_none_for_unknown_domain(tmp_path):
    manager = create_database(tmp_path)

    result = manager.get_mapping("unknown.example")

    assert result is None


def test_save_and_get_mapping(tmp_path):
    manager = create_database(tmp_path)

    saved = manager.save_mapping(
        domain="Example.COM",
        organization="Example Corporation",
        folder_name="Example Corporation",
        confidence="HIGH",
        reason="Known organization domain",
    )

    result = manager.get_mapping("example.com")

    assert result is not None
    assert result.domain == "example.com"
    assert result.organization == "Example Corporation"
    assert result.folder_name == "Example Corporation"
    assert result.confidence == "HIGH"
    assert result.reason == "Known organization domain"

    assert saved.domain == result.domain


def test_domain_is_normalized(tmp_path):
    manager = create_database(tmp_path)

    manager.save_mapping(
        domain="  Example.COM  ",
        organization="Example",
        folder_name="Example",
        confidence="HIGH",
    )

    result = manager.get_mapping("EXAMPLE.com")

    assert result is not None
    assert result.domain == "example.com"


def test_existing_mapping_is_updated_instead_of_duplicated(tmp_path):
    manager = create_database(tmp_path)

    manager.save_mapping(
        domain="example.com",
        organization="Old Organization",
        folder_name="Old Organization",
        confidence="MEDIUM",
        reason="Initial classification",
    )

    manager.save_mapping(
        domain="EXAMPLE.COM",
        organization="New Organization",
        folder_name="New Organization",
        confidence="HIGH",
        reason="Confirmed classification",
    )

    mappings = manager.list_mappings()

    assert len(mappings) == 1
    assert mappings[0].domain == "example.com"
    assert mappings[0].organization == "New Organization"
    assert mappings[0].folder_name == "New Organization"
    assert mappings[0].confidence == "HIGH"
    assert mappings[0].reason == "Confirmed classification"


def test_list_mappings_returns_all_mappings(tmp_path):
    manager = create_database(tmp_path)

    manager.save_mapping(
        domain="zeta.com",
        organization="Zeta",
        folder_name="Zeta",
        confidence="HIGH",
    )

    manager.save_mapping(
        domain="alpha.com",
        organization="Alpha",
        folder_name="Alpha",
        confidence="HIGH",
    )

    mappings = manager.list_mappings()

    assert len(mappings) == 2
    assert {mapping.domain for mapping in mappings} == {
        "alpha.com",
        "zeta.com",
    }


def test_invalid_confidence_is_rejected(tmp_path):
    manager = create_database(tmp_path)

    with pytest.raises(ValueError, match="Confidence must be"):
        manager.save_mapping(
            domain="example.com",
            organization="Example",
            folder_name="Example",
            confidence="UNKNOWN",
        )


def test_empty_domain_is_rejected(tmp_path):
    manager = create_database(tmp_path)

    with pytest.raises(ValueError, match="Domain cannot be empty"):
        manager.get_mapping("   ")


def test_empty_organization_is_rejected(tmp_path):
    manager = create_database(tmp_path)

    with pytest.raises(ValueError, match="Organization cannot be empty"):
        manager.save_mapping(
            domain="example.com",
            organization="   ",
            folder_name="Example",
            confidence="HIGH",
        )


def test_empty_folder_name_is_rejected(tmp_path):
    manager = create_database(tmp_path)

    with pytest.raises(ValueError, match="Folder name cannot be empty"):
        manager.save_mapping(
            domain="example.com",
            organization="Example",
            folder_name="   ",
            confidence="HIGH",
        )


def test_record_processing(tmp_path):
    manager = create_database(tmp_path)

    record = manager.record_processing(
        message_id="message-123",
        sender_email="User@Example.COM",
        organization="Example Corporation",
        domain="Example.COM",
        destination_folder="Example Corporation",
        confidence="HIGH",
        action="move",
        status="success",
        reason="High-confidence organization match",
    )

    assert record.message_id == "message-123"
    assert record.sender_email == "user@example.com"
    assert record.organization == "Example Corporation"
    assert record.domain == "example.com"
    assert record.destination_folder == "Example Corporation"
    assert record.confidence == "HIGH"
    assert record.action == "move"
    assert record.status == "success"


def test_was_message_processed(tmp_path):
    manager = create_database(tmp_path)

    assert manager.was_message_processed("message-123") is False

    manager.record_processing(
        message_id="message-123",
        sender_email="user@example.com",
        organization="Example",
        domain="example.com",
        destination_folder="Example",
        confidence="HIGH",
        action="move",
        status="success",
    )

    assert manager.was_message_processed("message-123") is True


def test_processing_history_can_be_retrieved(tmp_path):
    manager = create_database(tmp_path)

    manager.record_processing(
        message_id="message-123",
        sender_email="user@example.com",
        organization="Example",
        domain="example.com",
        destination_folder="Example",
        confidence="HIGH",
        action="move",
        status="success",
        reason="Matched known domain",
    )

    history = manager.get_processing_history()

    assert len(history) == 1
    assert history[0].message_id == "message-123"
    assert history[0].sender_email == "user@example.com"
    assert history[0].organization == "Example"
    assert history[0].domain == "example.com"
    assert history[0].destination_folder == "Example"
    assert history[0].confidence == "HIGH"
    assert history[0].action == "move"
    assert history[0].status == "success"
    assert history[0].reason == "Matched known domain"


def test_processing_history_can_be_filtered_by_message(tmp_path):
    manager = create_database(tmp_path)

    manager.record_processing(
        message_id="message-1",
        sender_email="one@example.com",
        organization="Example One",
        domain="example.com",
        destination_folder="Example One",
        confidence="HIGH",
        action="move",
        status="success",
    )

    manager.record_processing(
        message_id="message-2",
        sender_email="two@example.com",
        organization="Example Two",
        domain="example.com",
        destination_folder="Example Two",
        confidence="MEDIUM",
        action="none",
        status="left_untouched",
    )

    history = manager.get_processing_history(message_id="message-1")

    assert len(history) == 1
    assert history[0].message_id == "message-1"


def test_processing_history_limit(tmp_path):
    manager = create_database(tmp_path)

    for index in range(5):
        manager.record_processing(
            message_id=f"message-{index}",
            sender_email=f"user{index}@example.com",
            organization="Example",
            domain="example.com",
            destination_folder="Example",
            confidence="HIGH",
            action="move",
            status="success",
        )

    history = manager.get_processing_history(limit=3)

    assert len(history) == 3


def test_uninitialized_database_is_rejected(tmp_path):
    manager = DatabaseManager(
        database_path=tmp_path / "missing.db",
        schema_path="database/schema.sql",
    )

    with pytest.raises(
        DatabaseOperationError,
        match="Database does not exist",
    ):
        manager.get_schema_version()


def test_missing_schema_is_rejected(tmp_path):
    manager = DatabaseManager(
        database_path=tmp_path / "mailpilot.db",
        schema_path=tmp_path / "missing-schema.sql",
    )

    with pytest.raises(
        Exception,
        match="Database schema not found",
    ):
        manager.initialize()