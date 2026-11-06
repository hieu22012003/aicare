-- ============================================================================
-- COGNITIVE MEMORY GAMES & RELAXATION MODULE FOR ELDERLY (P0 - P4)
-- Database Schema: 3NF Relational Structure, Foreign Keys, Integrity Constraints
-- Reference: KE_HOACH_TRO_CHOI_TRI_NHO_NGUOI_CAO_TUOI.md & THIET_KE_CSDL_CHAM_SOC_NGUOI_CAO_TUOI.md
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- 1. CORE ACTORS & AUTHORIZATION (Adapted from System Specs)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(32) PRIMARY KEY,
    email VARCHAR(128) NOT NULL,
    phone VARCHAR(32) NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    full_name VARCHAR(128) NOT NULL,
    date_of_birth TEXT NOT NULL,
    gender VARCHAR(16) NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS caregivers (
    caregiver_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    email_verified INTEGER NOT NULL DEFAULT 1,
    phone_verified INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS clinicians (
    clinician_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    workplace VARCHAR(128) NOT NULL,
    specialty VARCHAR(128) NOT NULL,
    license_number VARCHAR(64) NOT NULL,
    is_verified INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS patient_caregivers (
    patient_caregiver_id VARCHAR(32) PRIMARY KEY,
    patient_id VARCHAR(32) NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    caregiver_id VARCHAR(32) NOT NULL REFERENCES caregivers(caregiver_id) ON DELETE CASCADE,
    relationship_type VARCHAR(64) NOT NULL,
    can_view_health INTEGER NOT NULL DEFAULT 1 CHECK(can_view_health IN (0, 1)),
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS care_team (
    care_team_id VARCHAR(32) PRIMARY KEY,
    patient_id VARCHAR(32) NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    clinician_id VARCHAR(32) NOT NULL REFERENCES clinicians(clinician_id) ON DELETE CASCADE,
    role_description VARCHAR(128) NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    consent_status VARCHAR(32) NOT NULL DEFAULT 'GRANTED' CHECK(consent_status IN ('GRANTED', 'REVOKED', 'EXPIRED')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))
);

-- ----------------------------------------------------------------------------
-- 2. GAME CATALOG & CONTENT PACKS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_types (
    game_type_id VARCHAR(32) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    rules_version VARCHAR(16) NOT NULL DEFAULT 'v1.0',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS content_packs (
    pack_id VARCHAR(64) PRIMARY KEY,
    game_type_id VARCHAR(32) NOT NULL REFERENCES game_types(game_type_id),
    title VARCHAR(128) NOT NULL,
    theme VARCHAR(64) NOT NULL,
    assets_manifest TEXT NOT NULL,
    license VARCHAR(64) NOT NULL DEFAULT 'CC0-1.0',
    locale VARCHAR(16) NOT NULL DEFAULT 'vi-VN',
    version VARCHAR(16) NOT NULL DEFAULT '1.0'
);

-- ----------------------------------------------------------------------------
-- 3. SESSIONS, PROGRESS & METRICS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS game_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(32) NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    game_type_id VARCHAR(32) NOT NULL REFERENCES game_types(game_type_id),
    pack_id VARCHAR(64) NOT NULL REFERENCES content_packs(pack_id),
    difficulty VARCHAR(32) NOT NULL CHECK(difficulty IN ('easy', 'medium', 'hard', 'expert')),
    seed INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status VARCHAR(32) NOT NULL CHECK(status IN ('in_progress', 'completed', 'abandoned', 'paused')),
    active_seconds REAL NOT NULL DEFAULT 0.0,
    help_count INTEGER NOT NULL DEFAULT 0,
    rules_version VARCHAR(16) NOT NULL DEFAULT 'v1.0',
    client_request_id VARCHAR(64) UNIQUE NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS game_results (
    session_id VARCHAR(64) PRIMARY KEY REFERENCES game_sessions(session_id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    accuracy_percent REAL NOT NULL DEFAULT 0.0,
    user_feedback VARCHAR(32) DEFAULT '' CHECK(user_feedback IN ('easy', 'fair', 'hard', 'skipped', '')),
    enjoyment VARCHAR(32) DEFAULT '' CHECK(enjoyment IN ('enjoyed', 'neutral', 'disliked', 'skipped', '')),
    metrics_json TEXT
);

-- ----------------------------------------------------------------------------
-- 4. RELAXING MUSIC & BINAURAL TRIAL SESSIONS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS music_tracks (
    track_id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(128) NOT NULL,
    artist_attribution VARCHAR(128) NOT NULL,
    asset_uri TEXT NOT NULL,
    license VARCHAR(64) NOT NULL,
    audio_mode VARCHAR(32) NOT NULL CHECK(audio_mode IN ('ordinary', 'binaural_trial')),
    duration_seconds INTEGER NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS listening_sessions (
    listen_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(32) NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    track_id VARCHAR(64) NOT NULL REFERENCES music_tracks(track_id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    played_seconds REAL NOT NULL DEFAULT 0.0,
    is_binaural INTEGER NOT NULL DEFAULT 0 CHECK(is_binaural IN (0, 1)),
    headphone_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(headphone_confirmed IN (0, 1)),
    feedback VARCHAR(32) DEFAULT '' CHECK(feedback IN ('pleasant', 'normal', 'disliked', 'skipped', '')),
    stopped_reason VARCHAR(64) NOT NULL DEFAULT 'user_stopped',
    client_request_id VARCHAR(64) UNIQUE NOT NULL
);

-- Indices for performance & isolation
CREATE INDEX IF NOT EXISTS idx_sessions_patient_time ON game_sessions(patient_id, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_game_diff ON game_sessions(game_type_id, difficulty, rules_version);
CREATE INDEX IF NOT EXISTS idx_listening_patient_time ON listening_sessions(patient_id, started_at);
