-- Face monitoring: when the elder (or a stranger) was in front of the camera, one row per person per day.
CREATE TABLE IF NOT EXISTS ai_face_sightings (
    sighting_id TEXT PRIMARY KEY,                 -- {patient_id}:{local_date}:{who}
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    local_date TEXT NOT NULL,                     -- YYYY-MM-DD in the patient's timezone
    who TEXT NOT NULL CHECK (who IN ('patient', 'stranger')),
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    seen_seconds REAL NOT NULL DEFAULT 0,
    appearances INTEGER NOT NULL DEFAULT 1,       -- separate visits (a gap of 10 minutes starts a new one)
    visits_json TEXT NOT NULL DEFAULT '[]'        -- [[start_utc, end_utc], ...] (last 50)
);
CREATE INDEX IF NOT EXISTS ix_ai_face_sightings_patient_date ON ai_face_sightings(patient_id, local_date);
