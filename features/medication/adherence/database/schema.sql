-- ==============================================================================
-- Schema: Chuẩn hoá 3NF CSDL Chăm Sóc Người Cao Tuổi & Nhắc Uống Thuốc
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

-- 6. Quan hệ N-N Bệnh nhân - Bác sĩ theo dõi
CREATE TABLE IF NOT EXISTS care_team (
    care_team_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    clinician_id TEXT NOT NULL,
    role_description TEXT NOT NULL DEFAULT 'Bác sĩ phụ trách',
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'GRANTED',
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT,
    UNIQUE(patient_id, clinician_id)
);

-- 7. Danh mục sản phẩm thuốc (Hàm lượng sản phẩm, dạng bào chế)
CREATE TABLE IF NOT EXISTS medications (
    medication_id TEXT PRIMARY KEY,
    brand_name TEXT NOT NULL,
    generic_name TEXT DEFAULT '',
    product_strength TEXT NOT NULL,       -- Ví dụ: '500' hoặc '5'
    strength_unit TEXT NOT NULL DEFAULT 'mg', -- Ví dụ: 'mg', 'ml'
    dosage_form TEXT NOT NULL DEFAULT 'Viên nén', -- Ví dụ: 'Viên nén', 'Viên nang', 'Siro'
    image_path TEXT DEFAULT '',
    safety_notes TEXT DEFAULT ''
);

-- 8. Đơn thuốc chỉ định
CREATE TABLE IF NOT EXISTS prescriptions (
    prescription_id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    entered_by_user_id TEXT NOT NULL,
    prescription_code TEXT DEFAULT '',
    prescribed_date TEXT NOT NULL,
    prescriber_clinician_id TEXT,
    source TEXT NOT NULL DEFAULT 'caregiver_transcribed',
    document_url TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (entered_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    FOREIGN KEY (prescriber_clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT
);

-- 9. Dòng thuốc trong đơn (Liều mỗi lần tách biệt khỏi hàm lượng)
CREATE TABLE IF NOT EXISTS prescription_items (
    prescription_item_id TEXT PRIMARY KEY,
    prescription_id TEXT NOT NULL,
    medication_id TEXT NOT NULL,
    dose_amount REAL NOT NULL CHECK(dose_amount > 0), -- Ví dụ: 1.0 (viên), 10.0 (ml)
    dose_unit TEXT NOT NULL DEFAULT 'viên',
    route_of_administration TEXT NOT NULL DEFAULT 'Đường uống',
    frequency_description TEXT DEFAULT 'Hàng ngày',
    original_instructions TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (prescription_id) REFERENCES prescriptions(prescription_id) ON DELETE RESTRICT,
    FOREIGN KEY (medication_id) REFERENCES medications(medication_id) ON DELETE RESTRICT
);

-- 10. Lịch nhắc uống thuốc (Cấu hình giờ theo múi giờ, phiên bản lịch)
CREATE TABLE IF NOT EXISTS medication_schedules (
    schedule_id TEXT NOT NULL,
    prescription_item_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    timezone TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    local_times TEXT NOT NULL,             -- JSON mảng các giờ 'HH:MM', vd: '["08:00", "20:00"]'
    days_of_week TEXT NOT NULL,            -- JSON mảng ngày 0..6, vd: '[0,1,2,3,4,5,6]'
    reminder_delay_minutes INTEGER NOT NULL DEFAULT 10 CHECK(reminder_delay_minutes >= 1),
    grace_period_minutes INTEGER NOT NULL DEFAULT 60 CHECK(grace_period_minutes >= 1),
    caregiver_alert_enabled INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    PRIMARY KEY (schedule_id, revision),
    FOREIGN KEY (prescription_item_id) REFERENCES prescription_items(prescription_item_id) ON DELETE RESTRICT
);

-- 11. Các lần uống đến hạn (Dose Occurrences)
CREATE TABLE IF NOT EXISTS dose_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    scheduled_at_utc TEXT NOT NULL,
    local_scheduled_time TEXT NOT NULL,
    grace_deadline_utc TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN (
        'PENDING', 'REMINDING', 'SNOOZED', 'CONFIRMED', 'SKIPPED', 'OVERDUE', 'MISSED', 'CANCELLED'
    )),
    medication_name_snapshot TEXT NOT NULL,
    dose_snapshot TEXT NOT NULL,
    instructions_snapshot TEXT NOT NULL,
    image_snapshot TEXT DEFAULT '',
    patient_id TEXT NOT NULL,
    snooze_count INTEGER NOT NULL DEFAULT 0,
    snoozed_until_utc TEXT,
    action_taken_at_utc TEXT,
    action_actor TEXT,
    action_notes TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    UNIQUE(schedule_id, revision, scheduled_at_utc)
);

-- 12. Nhật ký sự kiện kiểm toán liều (Dose Events - Append Only)
CREATE TABLE IF NOT EXISTS dose_events (
    event_id TEXT PRIMARY KEY,
    occurrence_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'reminded', 'snoozed', 'self_reported_taken', 'caregiver_reported_taken',
        'help_requested', 'overdue_unconfirmed', 'resolved'
    )),
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL DEFAULT 'patient',
    timestamp_utc TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (occurrence_id) REFERENCES dose_occurrences(occurrence_id) ON DELETE RESTRICT
);

-- 13. Cảnh báo nghiệp vụ (Alerts: OVERDUE, HELP_REQUESTED)
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    occurrence_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN ('OVERDUE', 'HELP_REQUESTED')),
    status TEXT NOT NULL DEFAULT 'NEW' CHECK(status IN ('NEW', 'DISPATCHED', 'ACKNOWLEDGED', 'RESOLVED')),
    created_at_utc TEXT NOT NULL,
    resolved_at_utc TEXT,
    resolved_by TEXT,
    resolution_notes TEXT,
    FOREIGN KEY (occurrence_id) REFERENCES dose_occurrences(occurrence_id) ON DELETE RESTRICT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    UNIQUE(occurrence_id, alert_type)
);

-- 14. Lần gửi thông báo cho Người chăm sóc (Notification Deliveries)
CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    caregiver_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'EMAIL' CHECK(channel IN ('EMAIL', 'SMS', 'IN_APP')),
    recipient_snapshot TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'SENT', 'DELIVERED', 'FAILED', 'ACKNOWLEDGED')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at_utc TEXT,
    error_message TEXT DEFAULT '',
    idempotency_key TEXT NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id) ON DELETE RESTRICT,
    FOREIGN KEY (caregiver_id) REFERENCES caregivers(caregiver_id) ON DELETE RESTRICT,
    UNIQUE(alert_id, caregiver_id, channel)
);

-- 15. Nhật ký giám sát gián đoạn hệ thống (Downtime & Clock Rollback Audit)
CREATE TABLE IF NOT EXISTS coverage_audit (
    audit_id TEXT PRIMARY KEY,
    start_utc TEXT NOT NULL,
    end_utc TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('DOWNTIME', 'CLOCK_BACKWARD')),
    notes TEXT DEFAULT ''
);

-- 16. Metadata hệ thống (Heartbeat, Rate limit)
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ==============================================================================
-- INDEX TỐI ƯU HOÁ TRUY VẤN NGHIỆP VỤ
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_occurrences_state_grace ON dose_occurrences(state, grace_deadline_utc);
CREATE INDEX IF NOT EXISTS idx_occurrences_patient_sched ON dose_occurrences(patient_id, scheduled_at_utc);
CREATE INDEX IF NOT EXISTS idx_patient_caregivers_active ON patient_caregivers(patient_id, is_active, valid_to);
CREATE INDEX IF NOT EXISTS idx_prescriptions_patient_status ON prescriptions(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_alerts_patient_status ON alerts(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_events_occurrence ON dose_events(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_events_idempotency ON dose_events(idempotency_key);
