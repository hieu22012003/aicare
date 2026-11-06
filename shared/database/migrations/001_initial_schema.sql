-- ==============================================================================
-- MIGRATION 001: INITIAL CORE 3NF RELATIONAL SCHEMA
-- Compatible with PostgreSQL (current) and SQLite (offline zero-dependency)
-- ==============================================================================

-- Bảng quản lý các file migration đã áp dụng
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- 1. BẢNG TÀI KHOẢN NGƯỜI DÙNG (USERS)
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(64) PRIMARY KEY,
    email VARCHAR(128) NOT NULL,
    phone VARCHAR(32) NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    password_hash VARCHAR(128) DEFAULT '',
    role VARCHAR(32) NOT NULL DEFAULT 'caregiver' CHECK(role IN ('admin', 'clinician', 'caregiver', 'patient')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

-- 2. BẢNG NGƯỜI CAO TUỔI (PATIENTS)
CREATE TABLE IF NOT EXISTS patients (
    patient_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) UNIQUE,
    full_name VARCHAR(128) NOT NULL,
    date_of_birth TEXT,
    gender VARCHAR(16) NOT NULL DEFAULT 'Nam',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    notes TEXT DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

-- Bảng ánh xạ ID cũ/ID nhận diện khuôn mặt sang patient_id chuẩn
CREATE TABLE IF NOT EXISTS legacy_patient_map (
    legacy_id VARCHAR(64) PRIMARY KEY,
    source_system VARCHAR(64) NOT NULL, -- 'camera_face', 'health_module', 'cognitive_module', 'local_json'
    patient_id VARCHAR(64) NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
);

-- 3. BẢNG NGƯỜI CHĂM SÓC / NGƯỜI THÂN (CAREGIVERS)
CREATE TABLE IF NOT EXISTS caregivers (
    caregiver_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL UNIQUE,
    email_verified INTEGER NOT NULL DEFAULT 1 CHECK(email_verified IN (0, 1)),
    phone_verified INTEGER NOT NULL DEFAULT 1 CHECK(phone_verified IN (0, 1)),
    relationship_desc VARCHAR(64) DEFAULT 'Người thân',
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 4. BẢNG BÁC SĨ / CÁN BỘ Y TẾ (CLINICIANS)
CREATE TABLE IF NOT EXISTS clinicians (
    clinician_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL UNIQUE,
    workplace VARCHAR(128) NOT NULL DEFAULT '',
    specialty VARCHAR(128) NOT NULL DEFAULT 'Lão khoa',
    license_number VARCHAR(64) NOT NULL DEFAULT '',
    is_verified INTEGER NOT NULL DEFAULT 1 CHECK(is_verified IN (0, 1)),
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK(is_simulation IN (0, 1)), -- 1 = Tài khoản mô phỏng (demo)
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 5. QUAN HỆ N-N BỆNH NHÂN - NGƯỜI CHĂM SÓC (PATIENT_CAREGIVERS)
CREATE TABLE IF NOT EXISTS patient_caregivers (
    patient_caregiver_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    caregiver_id VARCHAR(64) NOT NULL,
    relationship_type VARCHAR(64) NOT NULL DEFAULT 'Người thân',
    is_primary INTEGER NOT NULL DEFAULT 1 CHECK(is_primary IN (0, 1)),
    notification_priority INTEGER NOT NULL DEFAULT 1,
    can_edit_schedule INTEGER NOT NULL DEFAULT 1 CHECK(can_edit_schedule IN (0, 1)),
    can_view_health INTEGER NOT NULL DEFAULT 1 CHECK(can_view_health IN (0, 1)),
    consent_status VARCHAR(32) NOT NULL DEFAULT 'GRANTED' CHECK(consent_status IN ('GRANTED', 'REVOKED', 'PENDING')),
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (caregiver_id) REFERENCES caregivers(caregiver_id) ON DELETE RESTRICT,
    UNIQUE(patient_id, caregiver_id)
);

-- 6. QUAN HỆ N-N BỆNH NHÂN - BÁC SĨ THEO DÕI (CARE_TEAM)
CREATE TABLE IF NOT EXISTS care_team (
    care_team_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    clinician_id VARCHAR(64) NOT NULL,
    role_description VARCHAR(128) NOT NULL DEFAULT 'Bác sĩ phụ trách',
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    consent_status VARCHAR(32) NOT NULL DEFAULT 'GRANTED' CHECK(consent_status IN ('GRANTED', 'REVOKED', 'EXPIRED')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    UNIQUE(patient_id, clinician_id)
);

-- 7. DANH MỤC THUỐC (MEDICATIONS)
CREATE TABLE IF NOT EXISTS medications (
    medication_id VARCHAR(64) PRIMARY KEY,
    brand_name VARCHAR(128) NOT NULL,
    generic_name VARCHAR(128) DEFAULT '',
    product_strength VARCHAR(64) NOT NULL,
    strength_unit VARCHAR(32) NOT NULL DEFAULT 'mg',
    dosage_form VARCHAR(64) NOT NULL DEFAULT 'Viên nén',
    image_path TEXT DEFAULT '',
    safety_notes TEXT DEFAULT ''
);

-- 8. ĐƠN THUỐC (PRESCRIPTIONS)
CREATE TABLE IF NOT EXISTS prescriptions (
    prescription_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    entered_by_user_id VARCHAR(64) NOT NULL,
    prescription_code VARCHAR(64) DEFAULT '',
    prescribed_date TEXT NOT NULL,
    prescriber_clinician_id VARCHAR(64),
    source VARCHAR(64) NOT NULL DEFAULT 'caregiver_transcribed' CHECK(source IN ('clinician_entered', 'uploaded_document', 'caregiver_transcribed')),
    document_url TEXT DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED', 'EXPIRED')),
    version INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (entered_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    FOREIGN KEY (prescriber_clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT
);

-- 9. DÒNG THUỐC TRONG ĐƠN (PRESCRIPTION_ITEMS)
CREATE TABLE IF NOT EXISTS prescription_items (
    prescription_item_id VARCHAR(64) PRIMARY KEY,
    prescription_id VARCHAR(64) NOT NULL,
    medication_id VARCHAR(64) NOT NULL,
    dose_amount REAL NOT NULL CHECK(dose_amount > 0),
    dose_unit VARCHAR(32) NOT NULL DEFAULT 'viên',
    route_of_administration VARCHAR(64) NOT NULL DEFAULT 'Đường uống',
    frequency_description VARCHAR(128) DEFAULT 'Hàng ngày',
    original_instructions TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'STOPPED', 'COMPLETED')),
    FOREIGN KEY (prescription_id) REFERENCES prescriptions(prescription_id) ON DELETE RESTRICT,
    FOREIGN KEY (medication_id) REFERENCES medications(medication_id) ON DELETE RESTRICT
);

-- 10. LỊCH NHẮC THUỐC (MEDICATION_SCHEDULES)
CREATE TABLE IF NOT EXISTS medication_schedules (
    schedule_id VARCHAR(64) NOT NULL,
    prescription_item_id VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    local_times TEXT NOT NULL,             -- JSON mảng các mốc giờ, vd: '["08:00", "20:00"]'
    days_of_week TEXT NOT NULL,            -- JSON mảng ngày trong tuần, vd: '[0,1,2,3,4,5,6]'
    reminder_delay_minutes INTEGER NOT NULL DEFAULT 10 CHECK(reminder_delay_minutes >= 1),
    grace_period_minutes INTEGER NOT NULL DEFAULT 60 CHECK(grace_period_minutes >= 1),
    caregiver_alert_enabled INTEGER NOT NULL DEFAULT 1 CHECK(caregiver_alert_enabled IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    PRIMARY KEY (schedule_id, revision),
    FOREIGN KEY (prescription_item_id) REFERENCES prescription_items(prescription_item_id) ON DELETE RESTRICT
);

-- 11. CÁC LẦN UỐNG ĐẾN HẠN (DOSE_OCCURRENCES)
CREATE TABLE IF NOT EXISTS dose_occurrences (
    occurrence_id VARCHAR(64) PRIMARY KEY,
    schedule_id VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL,
    scheduled_at_utc TEXT NOT NULL,
    local_scheduled_time TEXT NOT NULL,
    grace_deadline_utc TEXT NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'PENDING' CHECK(state IN (
        'PENDING', 'REMINDING', 'SNOOZED', 'CONFIRMED', 'SKIPPED', 'OVERDUE', 'MISSED', 'CANCELLED'
    )),
    medication_name_snapshot VARCHAR(128) NOT NULL,
    dose_snapshot VARCHAR(64) NOT NULL,
    instructions_snapshot TEXT NOT NULL,
    image_snapshot TEXT DEFAULT '',
    patient_id VARCHAR(64) NOT NULL,
    snooze_count INTEGER NOT NULL DEFAULT 0,
    snoozed_until_utc TEXT,
    action_taken_at_utc TEXT,
    action_actor VARCHAR(64),
    action_notes TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    UNIQUE(schedule_id, revision, scheduled_at_utc)
);

-- 12. SỰ KIỆN LIỀU (DOSE_EVENTS - KIỂM TOÁN CHỈ THÊM)
CREATE TABLE IF NOT EXISTS dose_events (
    event_id VARCHAR(64) PRIMARY KEY,
    occurrence_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(32) NOT NULL CHECK(event_type IN (
        'reminded', 'snoozed', 'self_reported_taken', 'caregiver_reported_taken',
        'help_requested', 'overdue_unconfirmed', 'resolved'
    )),
    actor_id VARCHAR(64) NOT NULL,
    actor_role VARCHAR(32) NOT NULL DEFAULT 'patient',
    timestamp_utc TEXT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (occurrence_id) REFERENCES dose_occurrences(occurrence_id) ON DELETE RESTRICT
);

-- 13. CẢNH BÁO NGHIỆP VỤ (ALERTS)
CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR(64) PRIMARY KEY,
    occurrence_id VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    alert_type VARCHAR(32) NOT NULL CHECK(alert_type IN ('OVERDUE', 'HELP_REQUESTED')),
    status VARCHAR(32) NOT NULL DEFAULT 'NEW' CHECK(status IN ('NEW', 'DISPATCHED', 'ACKNOWLEDGED', 'RESOLVED')),
    created_at_utc TEXT NOT NULL,
    resolved_at_utc TEXT,
    resolved_by VARCHAR(64),
    resolution_notes TEXT,
    FOREIGN KEY (occurrence_id) REFERENCES dose_occurrences(occurrence_id) ON DELETE RESTRICT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    UNIQUE(occurrence_id, alert_type)
);

-- 14. LẦN GỬI THÔNG BÁO CHO NGƯỜI CHĂM SÓC (NOTIFICATION_DELIVERIES)
CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_id VARCHAR(64) PRIMARY KEY,
    alert_id VARCHAR(64) NOT NULL,
    caregiver_id VARCHAR(64) NOT NULL,
    channel VARCHAR(32) NOT NULL DEFAULT 'EMAIL' CHECK(channel IN ('EMAIL', 'SMS', 'IN_APP')),
    recipient_snapshot VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'SENT', 'DELIVERED', 'FAILED', 'ACKNOWLEDGED')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at_utc TEXT,
    error_message TEXT DEFAULT '',
    idempotency_key VARCHAR(128) NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id) ON DELETE RESTRICT,
    FOREIGN KEY (caregiver_id) REFERENCES caregivers(caregiver_id) ON DELETE RESTRICT,
    UNIQUE(alert_id, caregiver_id, channel)
);

-- 15. DANH MỤC LOẠI SỐ ĐO (OBSERVATION_TYPES)
CREATE TABLE IF NOT EXISTS observation_types (
    observation_type_id VARCHAR(64) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    standard_unit VARCHAR(32) NOT NULL,
    min_plausible_value REAL NOT NULL,
    max_plausible_value REAL NOT NULL,
    category VARCHAR(32) NOT NULL DEFAULT 'vitals'
);

-- 16. NHÓM ĐO LƯỜNG (MEASUREMENT_GROUPS)
CREATE TABLE IF NOT EXISTS measurement_groups (
    group_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    measured_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    entered_by_user_id VARCHAR(64) NOT NULL,
    source_type VARCHAR(32) NOT NULL CHECK(source_type IN ('manual_patient', 'manual_caregiver', 'device_import')),
    device_label VARCHAR(128) NOT NULL DEFAULT 'Người dùng nhập',
    context TEXT DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'corrected', 'entered_in_error')),
    client_request_id VARCHAR(128) NOT NULL UNIQUE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (entered_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 17. SỐ ĐO CHI TIẾT (HEALTH_OBSERVATIONS)
CREATE TABLE IF NOT EXISTS health_observations (
    observation_id VARCHAR(64) PRIMARY KEY,
    group_id VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    observation_type_id VARCHAR(64) NOT NULL,
    value_numeric REAL NOT NULL,
    unit_code VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'corrected', 'entered_in_error')),
    corrects_observation_id VARCHAR(64),
    notes TEXT DEFAULT '',
    FOREIGN KEY (group_id) REFERENCES measurement_groups(group_id) ON DELETE CASCADE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_type_id) REFERENCES observation_types(observation_type_id) ON DELETE RESTRICT,
    FOREIGN KEY (corrects_observation_id) REFERENCES health_observations(observation_id) ON DELETE SET NULL,
    UNIQUE(group_id, observation_type_id)
);

-- 18. TRIỆU CHỨNG TỰ BÁO CÁO (SYMPTOM_ENTRIES)
CREATE TABLE IF NOT EXISTS symptom_entries (
    symptom_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    reported_by_user_id VARCHAR(64) NOT NULL,
    reported_at TEXT NOT NULL,
    text TEXT NOT NULL,
    discomfort_level INTEGER DEFAULT 1 CHECK(discomfort_level BETWEEN 1 AND 5),
    status VARCHAR(32) NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'retracted')),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (reported_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 19. ĐÁNH DẤU ĐÃ XEM CỦA BÁC SĨ (CLINICIAN_REVIEWS)
CREATE TABLE IF NOT EXISTS clinician_reviews (
    review_id VARCHAR(64) PRIMARY KEY,
    clinician_id VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    reviewed_at TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'CONFIRMED' CHECK(status IN ('CONFIRMED', 'CANCELLED')),
    notes TEXT DEFAULT '',
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- 20. PHẢN HỒI CHUYÊN MÔN CỦA BÁC SĨ (CLINICAL_MESSAGES)
CREATE TABLE IF NOT EXISTS clinical_messages (
    message_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    clinician_id VARCHAR(64) NOT NULL,
    review_id VARCHAR(64),
    observation_id VARCHAR(64),
    body TEXT NOT NULL,
    visibility VARCHAR(32) NOT NULL DEFAULT 'PATIENT_AND_CAREGIVERS' CHECK(visibility IN ('PATIENT_AND_CAREGIVERS', 'PATIENT_ONLY', 'CLINICIAN_ONLY')),
    created_at TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'sent' CHECK(status IN ('sent', 'delivered', 'read')),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    FOREIGN KEY (review_id) REFERENCES clinician_reviews(review_id) ON DELETE SET NULL,
    FOREIGN KEY (observation_id) REFERENCES health_observations(observation_id) ON DELETE SET NULL
);

-- 21. BIÊN NHẬN ĐỌC TIN NHẮN (MESSAGE_RECEIPTS)
CREATE TABLE IF NOT EXISTS message_receipts (
    receipt_id VARCHAR(64) PRIMARY KEY,
    message_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    read_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES clinical_messages(message_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    UNIQUE(message_id, user_id)
);

-- 22. KẾ HOẠCH THEO DÕI VÀ NGƯỠNG BÁC SĨ (MONITORING_PLANS & THRESHOLDS)
CREATE TABLE IF NOT EXISTS monitoring_plans (
    plan_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    clinician_id VARCHAR(64) NOT NULL,
    observation_type_id VARCHAR(64) NOT NULL,
    frequency_rule VARCHAR(128) NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_type_id) REFERENCES observation_types(observation_type_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS patient_thresholds (
    threshold_id VARCHAR(64) PRIMARY KEY,
    plan_id VARCHAR(64) NOT NULL,
    min_value REAL,
    max_value REAL,
    unit_code VARCHAR(32) NOT NULL,
    severity VARCHAR(32) NOT NULL DEFAULT 'INFO',
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (plan_id) REFERENCES monitoring_plans(plan_id) ON DELETE CASCADE
);

-- 23. HOẠT ĐỘNG TRÍ NHỚ (GAMES & RELAXATION)
CREATE TABLE IF NOT EXISTS game_types (
    game_type_id VARCHAR(64) PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    rules_version VARCHAR(16) NOT NULL DEFAULT 'v1.0',
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS content_packs (
    pack_id VARCHAR(64) PRIMARY KEY,
    game_type_id VARCHAR(64) NOT NULL REFERENCES game_types(game_type_id),
    title VARCHAR(128) NOT NULL,
    theme VARCHAR(64) NOT NULL,
    assets_manifest TEXT NOT NULL,
    license VARCHAR(64) NOT NULL DEFAULT 'CC0-1.0',
    locale VARCHAR(16) NOT NULL DEFAULT 'vi-VN',
    version VARCHAR(16) NOT NULL DEFAULT '1.0'
);

CREATE TABLE IF NOT EXISTS game_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
    game_type_id VARCHAR(64) NOT NULL REFERENCES game_types(game_type_id),
    pack_id VARCHAR(64) NOT NULL REFERENCES content_packs(pack_id),
    difficulty VARCHAR(32) NOT NULL CHECK(difficulty IN ('easy', 'medium', 'hard', 'expert')),
    seed INTEGER NOT NULL DEFAULT 42,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status VARCHAR(32) NOT NULL CHECK(status IN ('in_progress', 'completed', 'abandoned', 'paused')),
    active_seconds REAL NOT NULL DEFAULT 0.0,
    help_count INTEGER NOT NULL DEFAULT 0,
    rules_version VARCHAR(16) NOT NULL DEFAULT 'v1.0',
    client_request_id VARCHAR(64) UNIQUE,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS game_results (
    session_id VARCHAR(64) PRIMARY KEY REFERENCES game_sessions(session_id) ON DELETE CASCADE,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    accuracy_percent REAL NOT NULL DEFAULT 0.0,
    user_feedback VARCHAR(32) DEFAULT '',
    enjoyment VARCHAR(32) DEFAULT '',
    metrics_json TEXT DEFAULT '{}'
);

-- ==============================================================================
-- INDEX TỐI ƯU TRUY VẤN
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_occurrences_state_grace ON dose_occurrences(state, grace_deadline_utc);
CREATE INDEX IF NOT EXISTS idx_occurrences_patient_sched ON dose_occurrences(patient_id, scheduled_at_utc);
CREATE INDEX IF NOT EXISTS idx_patient_caregivers_active ON patient_caregivers(patient_id, is_active, valid_to);
CREATE INDEX IF NOT EXISTS idx_care_team_clinician ON care_team(clinician_id, is_active, valid_to);
CREATE INDEX IF NOT EXISTS idx_care_team_patient ON care_team(patient_id, is_active);
CREATE INDEX IF NOT EXISTS idx_prescriptions_patient_status ON prescriptions(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_alerts_patient_status ON alerts(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_events_occurrence ON dose_events(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_events_idempotency ON dose_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_obs_patient_type_time ON health_observations(patient_id, observation_type_id, status);
CREATE INDEX IF NOT EXISTS idx_groups_patient_time ON measurement_groups(patient_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_patient ON clinician_reviews(patient_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_patient ON clinical_messages(patient_id, created_at DESC);
