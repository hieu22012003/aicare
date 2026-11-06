CREATE TABLE IF NOT EXISTS family_music (
    track_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    uploaded_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(patient_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_family_music_patient_time ON family_music(patient_id, created_at DESC);
