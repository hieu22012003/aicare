-- ==============================================================================
-- Schema: Chuẩn hoá 3NF CSDL Nhật Ký Sức Khoẻ & Bác Sĩ Theo Dõi Từ Xa (P0-P2)
-- Tuân thủ ANSI SQL / PostgreSQL & SQLite với khoá ngoại, CHECK, UNIQUE, INDEX
-- ==============================================================================

PRAGMA foreign_keys = ON;

-- 1. Bảng Người dùng hệ thống (Tài khoản gốc)
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    full_name TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- 2. Bảng Người cao tuổi (Bệnh nhân)
CREATE TABLE IF NOT EXISTS patients (
    patient_id TEXT PRIMARY KEY,
    user_id TEXT UNIQUE,
    full_name TEXT NOT NULL,
    date_of_birth TEXT,
    gender TEXT NOT NULL DEFAULT 'Nam',
    timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    notes TEXT DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

-- 3. Bảng Người thân / Người chăm sóc
CREATE TABLE IF NOT EXISTS caregivers (
    caregiver_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    email_verified INTEGER NOT NULL DEFAULT 1,
    phone_verified INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 4. Bảng Bác sĩ / Cán bộ y tế
CREATE TABLE IF NOT EXISTS clinicians (
    clinician_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    workplace TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT '',
    license_number TEXT NOT NULL DEFAULT '',
    is_verified INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 5. Quan hệ N-N Bệnh nhân - Người chăm sóc (Kèm thời hạn và quyền)
CREATE TABLE IF NOT EXISTS patient_caregivers (
    patient_caregiver_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    caregiver_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL DEFAULT 'Người thân',
    is_primary INTEGER NOT NULL DEFAULT 1,
    notification_priority INTEGER NOT NULL DEFAULT 1,
    can_edit_schedule INTEGER NOT NULL DEFAULT 1,
    can_view_health INTEGER NOT NULL DEFAULT 1,
    consent_status TEXT NOT NULL DEFAULT 'GRANTED',
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (caregiver_id) REFERENCES caregivers(caregiver_id) ON DELETE RESTRICT,
    UNIQUE(patient_id, caregiver_id)
);

-- 6. Quan hệ N-N Bệnh nhân - Bác sĩ theo dõi (care_team)
CREATE TABLE IF NOT EXISTS care_team (
    care_team_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    clinician_id TEXT NOT NULL,
    role_description TEXT NOT NULL DEFAULT 'Bác sĩ phụ trách',
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'GRANTED',
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    UNIQUE(patient_id, clinician_id)
);

-- 7. Danh mục loại số đo (Observation Types)
CREATE TABLE IF NOT EXISTS observation_types (
    observation_type_id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    standard_unit TEXT NOT NULL,
    min_plausible_value REAL NOT NULL,
    max_plausible_value REAL NOT NULL,
    category TEXT NOT NULL DEFAULT 'vitals'
);

-- 8. Nhóm đo lường (Measurement Groups)
-- Một lần đo (như Huyết áp gồm 2 thành phần) được gom trong một nhóm đo có chung thời điểm đo, thời điểm ghi, nguồn đo
CREATE TABLE IF NOT EXISTS measurement_groups (
    group_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    entered_by_user_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('manual_patient', 'manual_caregiver', 'device_import')),
    device_label TEXT NOT NULL DEFAULT 'Người dùng nhập',
    context TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'corrected', 'entered_in_error')),
    client_request_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (entered_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 9. Số đo sức khoẻ chi tiết (Health Observations)
CREATE TABLE IF NOT EXISTS health_observations (
    observation_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    observation_type_id TEXT NOT NULL,
    value_numeric REAL NOT NULL,
    unit_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'corrected', 'entered_in_error')),
    corrects_observation_id TEXT,
    notes TEXT DEFAULT '',
    FOREIGN KEY (group_id) REFERENCES measurement_groups(group_id) ON DELETE CASCADE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_type_id) REFERENCES observation_types(observation_type_id) ON DELETE RESTRICT,
    FOREIGN KEY (corrects_observation_id) REFERENCES health_observations(observation_id) ON DELETE SET NULL,
    UNIQUE(group_id, observation_type_id)
);

-- 10. Nhật ký triệu chứng tự khai báo (Symptom Entries)
CREATE TABLE IF NOT EXISTS symptom_entries (
    symptom_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    reported_by_user_id TEXT NOT NULL,
    reported_at TEXT NOT NULL,
    text TEXT NOT NULL,
    discomfort_level INTEGER DEFAULT 1 CHECK(discomfort_level BETWEEN 1 AND 5),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'retracted')),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (reported_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 11. Đánh dấu đã xem của Bác sĩ (Clinician Reviews)
CREATE TABLE IF NOT EXISTS clinician_reviews (
    review_id TEXT PRIMARY KEY,
    clinician_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CONFIRMED' CHECK(status IN ('CONFIRMED', 'CANCELLED')),
    notes TEXT DEFAULT '',
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- 12. Phản hồi và tin nhắn chuyên môn của Bác sĩ (Clinical Messages)
CREATE TABLE IF NOT EXISTS clinical_messages (
    message_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    clinician_id TEXT NOT NULL,
    review_id TEXT,
    observation_id TEXT,
    body TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'PATIENT_AND_CAREGIVERS' CHECK(visibility IN ('PATIENT_AND_CAREGIVERS', 'PATIENT_ONLY', 'CLINICIAN_ONLY')),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent' CHECK(status IN ('sent', 'delivered', 'read')),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    FOREIGN KEY (review_id) REFERENCES clinician_reviews(review_id) ON DELETE SET NULL,
    FOREIGN KEY (observation_id) REFERENCES health_observations(observation_id) ON DELETE SET NULL
);

-- 13. Biên nhận đọc tin nhắn (Message Receipts)
CREATE TABLE IF NOT EXISTS message_receipts (
    receipt_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    read_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES clinical_messages(message_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    UNIQUE(message_id, user_id)
);

-- 14. Kế hoạch theo dõi chỉ số do Bác sĩ thiết lập (Monitoring Plans - P2)
CREATE TABLE IF NOT EXISTS monitoring_plans (
    plan_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    clinician_id TEXT NOT NULL,
    observation_type_id TEXT NOT NULL,
    frequency_rule TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_type_id) REFERENCES observation_types(observation_type_id) ON DELETE RESTRICT
);

-- 15. Ngưỡng theo dõi cá nhân hóa do Bác sĩ định nghĩa (Patient Thresholds - P2, không mặc định)
CREATE TABLE IF NOT EXISTS patient_thresholds (
    threshold_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    min_value REAL,
    max_value REAL,
    unit_code TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'INFO',
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (plan_id) REFERENCES monitoring_plans(plan_id) ON DELETE CASCADE
);

-- ==============================================================================
-- INDEX TỐI ƯU HOÁ TRUY VẤN
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_obs_patient_type_time ON health_observations(patient_id, observation_type_id, status);
CREATE INDEX IF NOT EXISTS idx_groups_patient_time ON measurement_groups(patient_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_groups_client_req ON measurement_groups(client_request_id);
CREATE INDEX IF NOT EXISTS idx_care_team_clinician ON care_team(clinician_id, is_active, valid_to);
CREATE INDEX IF NOT EXISTS idx_care_team_patient ON care_team(patient_id, is_active);
CREATE INDEX IF NOT EXISTS idx_caregivers_patient ON patient_caregivers(patient_id, is_active, can_view_health);
CREATE INDEX IF NOT EXISTS idx_reviews_patient ON clinician_reviews(patient_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_patient ON clinical_messages(patient_id, created_at DESC);
