-- 015: commands for home devices when the AI runs on the cloud backend.
-- The backend cannot play sound: what it would say (reminders, check-in questions) is queued here
-- and the device in the elder's home polls GET /api/device/patients/{id}/commands and speaks it.
CREATE TABLE IF NOT EXISTS ai_device_commands (
    command_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id VARCHAR(64) NOT NULL,
    kind VARCHAR(16) NOT NULL CHECK(kind IN ('speak')),
    payload TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_device_commands_patient ON ai_device_commands(patient_id, command_id);
