CREATE TABLE emotion_monitor_state_v2 (
patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id) ON DELETE CASCADE,
state_json TEXT NOT NULL);
CREATE TABLE emotion_summaries_v2 (
summary_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
window_start TEXT NOT NULL, window_end TEXT NOT NULL, observed_until TEXT NOT NULL,
published_at TEXT NOT NULL, label TEXT NOT NULL, dominant_label TEXT NOT NULL,
reason TEXT NOT NULL, durations_json TEXT NOT NULL, observed_seconds REAL NOT NULL,
sadness_override INTEGER NOT NULL DEFAULT 0, UNIQUE(patient_id, window_start, reason));
CREATE INDEX idx_emotion_v2_patient_time ON emotion_summaries_v2(patient_id,published_at DESC);
