from __future__ import annotations

from app.classifier.learning import LearningManager
from app.mail.models import ClassificationConfidence, OrganizationClassification
from database.manager import DatabaseManager


def create_system(tmp_path):
    database = DatabaseManager(
        database_path=str(tmp_path / "mailpilot.db"),
    )
    database.initialize()

    learning_manager = LearningManager(database)

    return database, learning_manager


def create_classification(
    organization: str | None = "Example Corporation",
    domain: str = "example.com",
    folder_name: str | None = "Example Corporation",
    confidence: ClassificationConfidence = ClassificationConfidence.HIGH,
    reason: str = "AI identified the organization.",
) -> OrganizationClassification:
    return OrganizationClassification(
        organization=organization,
        domain=domain,
        folder_name=folder_name,
        confidence=confidence,
        reason=reason,
    )


def test_high_confidence_classification_is_learned(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification()

    learned = learning_manager.learn(classification)

    assert learned is True

    mapping = database.get_mapping("example.com")

    assert mapping is not None
    assert mapping.organization == "Example Corporation"
    assert mapping.folder_name == "Example Corporation"
    assert mapping.confidence == "HIGH"


def test_medium_confidence_classification_is_not_learned(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification(
        confidence=ClassificationConfidence.MEDIUM,
        reason="AI has moderate confidence.",
    )

    learned = learning_manager.learn(classification)

    assert learned is False
    assert database.get_mapping("example.com") is None


def test_low_confidence_classification_is_not_learned(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification(
        organization=None,
        folder_name=None,
        confidence=ClassificationConfidence.LOW,
        reason="Insufficient evidence.",
    )

    learned = learning_manager.learn(classification)

    assert learned is False
    assert database.get_mapping("example.com") is None


def test_high_confidence_without_organization_is_not_learned(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification(
        organization=None,
        folder_name="Example Corporation",
        confidence=ClassificationConfidence.HIGH,
    )

    learned = learning_manager.learn(classification)

    assert learned is False
    assert database.get_mapping("example.com") is None


def test_high_confidence_without_folder_is_not_learned(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification(
        organization="Example Corporation",
        folder_name=None,
        confidence=ClassificationConfidence.HIGH,
    )

    learned = learning_manager.learn(classification)

    assert learned is False
    assert database.get_mapping("example.com") is None


def test_existing_mapping_is_not_overwritten(tmp_path):
    database, learning_manager = create_system(tmp_path)

    database.save_mapping(
        domain="example.com",
        organization="Original Corporation",
        folder_name="Original Corporation",
        confidence="HIGH",
        reason="Existing trusted mapping.",
    )

    classification = create_classification(
        organization="Different Corporation",
        folder_name="Different Corporation",
        confidence=ClassificationConfidence.HIGH,
        reason="New AI classification.",
    )

    learned = learning_manager.learn(classification)

    assert learned is False

    mapping = database.get_mapping("example.com")

    assert mapping is not None
    assert mapping.organization == "Original Corporation"
    assert mapping.folder_name == "Original Corporation"


def test_domain_is_normalized_before_learning(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification(
        domain="  Example.COM  ",
    )

    learned = learning_manager.learn(classification)

    assert learned is True

    mapping = database.get_mapping("example.com")

    assert mapping is not None
    assert mapping.domain == "example.com"


def test_learning_returns_false_for_generic_email_provider(tmp_path):
    database, learning_manager = create_system(tmp_path)

    classification = create_classification(
        organization="Someone",
        domain="gmail.com",
        folder_name="Someone",
        confidence=ClassificationConfidence.HIGH,
    )

    learned = learning_manager.learn(classification)

    assert learned is False
    assert database.get_mapping("gmail.com") is None