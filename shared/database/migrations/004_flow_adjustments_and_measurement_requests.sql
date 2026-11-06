-- ==============================================================================
-- MIGRATION 004: FLOW ADJUSTMENTS & HEALTH MEASUREMENT REQUESTS
-- Supports:
-- 1. Caregiver health measurement requests & tracking (measurement_requests)
-- 2. Exercise plan version history & quality thresholds (exercise_plan_versions)
-- 3. Measurement request audit logs
-- Compatible with PostgreSQL and SQLite
-- ==============================================================================

-- 1. BẢNG YÊU CẦU NHẮC ĐO CHỈ SỐ SỨC KHỎE DO NGƯỜI NHÀ TẠO (MEASUREMENT_REQUESTS)
CREATE TABLE IF NOT EXISTS measurement_requests (
    request_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    requested_by_user_id VARCHAR(64) NOT NULL,
    observation_type_id VARCHAR(64) NOT NULL,
    due_at_utc TEXT NOT NULL,
    local_due_time TEXT NOT NULL,
    instructions TEXT DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'COMPLETED', 'MISSED', 'CANCELLED')),
    completed_observation_id VARCHAR(64),
    created_at_utc TEXT NOT NULL,
    client_request_id VARCHAR(128) UNIQUE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (requested_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_type_id) REFERENCES observation_types(observation_type_id) ON DELETE RESTRICT,
    FOREIGN KEY (completed_observation_id) REFERENCES health_observations(observation_id) ON DELETE SET NULL
);

-- 2. BẢNG LỊCH SỬ PHIÊN BẢN KẾ HOẠCH BÀI TẬP PHỤC HỒI (EXERCISE_PLAN_VERSIONS)
CREATE TABLE IF NOT EXISTS exercise_plan_versions (
    plan_version_id VARCHAR(64) PRIMARY KEY,
    plan_id VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    version INTEGER NOT NULL,
    target_count INTEGER NOT NULL CHECK(target_count > 0),
    quality_threshold REAL NOT NULL DEFAULT 80.0,
    local_reminder_time VARCHAR(16) NOT NULL DEFAULT '16:00',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    created_by_user_id VARCHAR(64) NOT NULL,
    created_at TEXT NOT NULL,
    reason TEXT DEFAULT '',
    FOREIGN KEY (plan_id) REFERENCES rehab_plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    UNIQUE(plan_id, version)
);

-- 3. BẢNG LỊCH SỬ AUDIT YÊU CẦU ĐO (MEASUREMENT_REQUEST_LOGS)
CREATE TABLE IF NOT EXISTS measurement_request_logs (
    log_id VARCHAR(64) PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL CHECK(action IN ('CREATED', 'COMPLETED', 'MARKED_MISSED', 'CANCELLED')),
    actor_id VARCHAR(64) NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    details TEXT DEFAULT '{}',
    FOREIGN KEY (request_id) REFERENCES measurement_requests(request_id) ON DELETE CASCADE
);

-- ==============================================================================
-- INDEX TỐI ƯU TRUY VẤN
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_meas_requests_patient_status ON measurement_requests(patient_id, status, due_at_utc);
CREATE INDEX IF NOT EXISTS idx_meas_requests_due ON measurement_requests(status, due_at_utc);
CREATE INDEX IF NOT EXISTS idx_exercise_plan_versions_plan ON exercise_plan_versions(plan_id, version DESC);

