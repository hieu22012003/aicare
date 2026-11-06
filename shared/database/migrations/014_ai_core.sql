-- 014: AI monitoring (ai_core): posture & activity, fall/dizziness risk (trial), object finder,
-- camera-observed medication intake. Additive only: no existing table is changed.
-- Times are ISO 8601 UTC strings, like the rest of the schema.

-- Per-patient settings for each AI feature (JSON validated by the pydantic configs in ai_core).
CREATE TABLE IF NOT EXISTS ai_settings (
    patient_id VARCHAR(64) NOT NULL,
    feature VARCHAR(32) NOT NULL CHECK(feature IN ('posture', 'fall', 'objects', 'medication')),
    config TEXT NOT NULL DEFAULT '{}',
    updated_at_utc TEXT NOT NULL,
    updated_by VARCHAR(64),
    PRIMARY KEY (patient_id, feature)
);

-- Latest posture state per patient (one row, overwritten).
CREATE TABLE IF NOT EXISTS ai_posture_state (
    patient_id VARCHAR(64) PRIMARY KEY,
    posture VARCHAR(16) NOT NULL,
    location VARCHAR(16) NOT NULL,
    since_utc TEXT,
    observed INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    sitting_streak_seconds REAL NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_posture_segments (
    segment_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    posture VARCHAR(16) NOT NULL,
    location VARCHAR(16) NOT NULL,
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    confidence REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_posture_segments_patient ON ai_posture_segments(patient_id, started_at_utc);

CREATE TABLE IF NOT EXISTS ai_posture_reminders (
    reminder_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    rule VARCHAR(8) NOT NULL,
    audience VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    message TEXT NOT NULL,
    caregiver_message TEXT,
    triggered_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_posture_reminders_patient ON ai_posture_reminders(patient_id, triggered_at_utc);

-- Fall events from the new detector. trial=1 means: shown on the portal, no email/request sent.
CREATE TABLE IF NOT EXISTS ai_fall_events (
    event_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    kind VARCHAR(4) NOT NULL,
    status VARCHAR(16) NOT NULL,
    trial INTEGER NOT NULL DEFAULT 1,
    detected_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    message TEXT NOT NULL,
    caregiver_message TEXT,
    label VARCHAR(16),
    label_by VARCHAR(64),
    note TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_ai_fall_events_patient ON ai_fall_events(patient_id, detected_at_utc);

CREATE TABLE IF NOT EXISTS ai_risk_signals (
    signal_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    code VARCHAR(4) NOT NULL,
    observed_at_utc TEXT NOT NULL,
    value REAL NOT NULL,
    threshold REAL NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_risk_signals_patient ON ai_risk_signals(patient_id, observed_at_utc);

CREATE TABLE IF NOT EXISTS ai_object_sightings (
    sighting_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    item_key VARCHAR(48) NOT NULL,
    type_key VARCHAR(32) NOT NULL,
    camera_index INTEGER NOT NULL DEFAULT 0,
    region_id VARCHAR(40),
    box TEXT NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    confidence REAL NOT NULL,
    status VARCHAR(12) NOT NULL,
    status_at_utc TEXT
);
CREATE INDEX IF NOT EXISTS ix_ai_object_sightings_patient ON ai_object_sightings(patient_id, last_seen_utc);

-- Camera evidence for a dose. VERIFIED confirms the dose; LIKELY only asks the elder.
CREATE TABLE IF NOT EXISTS ai_medication_evidence (
    evidence_id VARCHAR(64) PRIMARY KEY,
    occurrence_id VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    verdict VARCHAR(16) NOT NULL CHECK(verdict IN ('VERIFIED', 'LIKELY')),
    observed_at_utc TEXT NOT NULL,
    score REAL NOT NULL,
    steps TEXT NOT NULL DEFAULT '{}',
    applied VARCHAR(32) NOT NULL DEFAULT 'none'
);
CREATE INDEX IF NOT EXISTS ix_ai_medication_evidence_occ ON ai_medication_evidence(occurrence_id);

-- Which monitoring milestones (open, speak_due, overdue, escalate, close) were done per dose.
CREATE TABLE IF NOT EXISTS ai_medication_monitor (
    occurrence_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    actions_done TEXT NOT NULL DEFAULT '[]',
    updated_at_utc TEXT NOT NULL
);
