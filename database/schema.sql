
-- ============================================================
-- MailPilot — SQLite Database Schema
-- ============================================================
-- This database stores MailPilot's local operational memory.
--
-- It does NOT store email bodies or authentication tokens.
-- Microsoft authentication state is stored separately.
-- ============================================================


-- ============================================================
-- Database Metadata
-- ============================================================

CREATE TABLE IF NOT EXISTS database_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);


-- ============================================================
-- Organization Mappings
-- ============================================================
-- Stores MailPilot's learned relationship between an email
-- domain and its canonical organization/folder.
--
-- Example:
--
-- domain       = example.com
-- organization = Example Corporation
-- folder_name  = Example Corporation
--
-- A UNIQUE domain prevents duplicate mappings.
-- ============================================================

CREATE TABLE IF NOT EXISTS organization_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    domain TEXT NOT NULL UNIQUE,

    organization TEXT NOT NULL,

    folder_name TEXT NOT NULL,

    confidence TEXT NOT NULL
        CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),

    reason TEXT,

    created_at TEXT NOT NULL,

    updated_at TEXT NOT NULL
);


-- ============================================================
-- Processing History
-- ============================================================
-- Records what MailPilot decided and what action occurred.
--
-- This is an audit trail.
--
-- MailPilot never deletes an email as part of this workflow.
-- ============================================================

CREATE TABLE IF NOT EXISTS processing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    message_id TEXT NOT NULL,

    sender_email TEXT NOT NULL,

    organization TEXT,

    domain TEXT,

    destination_folder TEXT,

    confidence TEXT
        CHECK (
            confidence IS NULL
            OR confidence IN ('HIGH', 'MEDIUM', 'LOW')
        ),

    action TEXT NOT NULL,

    status TEXT NOT NULL,

    reason TEXT,

    processed_at TEXT NOT NULL
);


-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_organization_mappings_domain
    ON organization_mappings(domain);

CREATE INDEX IF NOT EXISTS idx_processing_history_message_id
    ON processing_history(message_id);

CREATE INDEX IF NOT EXISTS idx_processing_history_domain
    ON processing_history(domain);

CREATE INDEX IF NOT EXISTS idx_processing_history_processed_at
    ON processing_history(processed_at);


-- ============================================================
-- Initial Database Metadata
-- ============================================================

INSERT OR IGNORE INTO database_metadata (key, value)
VALUES ('schema_version', '1');