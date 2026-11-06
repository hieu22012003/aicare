CREATE TABLE IF NOT EXISTS emotion_monitor_state (
    patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE,
    state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emotion_summaries (
    summary_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    observed_until TEXT NOT NULL,
    published_at TEXT NOT NULL,
    label TEXT NOT NULL CHECK(label IN ('Happy', 'Neutral', 'Sad')),
    dominant_label TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN ('window_summary', 'mixed_threshold')),
    durations_json TEXT NOT NULL,
    observed_seconds REAL NOT NULL,
    sadness_override INTEGER NOT NULL DEFAULT 0,
    UNIQUE(patient_id, window_start, reason)
);
CREATE INDEX IF NOT EXISTS idx_emotion_summaries_patient_time
    ON emotion_summaries(patient_id, published_at DESC);
