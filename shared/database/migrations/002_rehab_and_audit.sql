-- ==============================================================================
-- MIGRATION 002: REHABILITATION (6A), MOOD/EMOTION, CAREGIVER REPORTS, RECOMMENDATIONS,
-- EMERGENCY CONTACTS & AUDIT LOGGING
-- Compatible with PostgreSQL and SQLite
-- ==============================================================================

-- 1. TÂM TRẠNG TỰ BÁO CÁO (MOOD_CHECKINS - BỆNH NHÂN TỰ GHI NHẬN)
CREATE TABLE IF NOT EXISTS mood_checkins (
    checkin_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    self_rating INTEGER NOT NULL CHECK(self_rating BETWEEN 1 AND 5), -- 1: Rất buồn, 3: Bình thường, 5: Rất vui
    note TEXT DEFAULT '',
    recorded_at TEXT NOT NULL,
    recorded_by VARCHAR(64) NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- 2. TÍN HIỆU BIỂU CẢM TỪ CAMERA (EMOTION_INFERENCES - THAM KHẢO RIÊNG, KHÔNG PHẢI CHẨN ĐOÁN)
CREATE TABLE IF NOT EXISTS emotion_inferences (
    inference_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    model_version VARCHAR(64) NOT NULL DEFAULT 'ExpressionNet-v1.0',
    label VARCHAR(32) NOT NULL, -- 'Bình thường', 'Vui vẻ', 'Buồn bã'
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    observed_at TEXT NOT NULL,
    consent_id VARCHAR(64),
    is_consent_active INTEGER NOT NULL DEFAULT 1 CHECK(is_consent_active IN (0, 1)),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- 3. KẾ HOẠCH PHỤC HỒI CHỨC NĂNG (REHAB_PLANS - CẤU HÌNH NHẮC 16:00 VÀ CHỈ TIÊU)
CREATE TABLE IF NOT EXISTS rehab_plans (
    plan_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    exercise_id VARCHAR(64) NOT NULL, -- 'arm_raise', 'sit_to_stand', 'marching'
    exercise_name VARCHAR(128) NOT NULL,
    target_count INTEGER NOT NULL CHECK(target_count > 0),
    local_reminder_time VARCHAR(16) NOT NULL DEFAULT '16:00',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    version INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- 4. BUỔI TẬP PHỤC HỒI (REHAB_SESSIONS - QUẢN LÝ TIẾN ĐỘ VÀ SNAPSHOT CHỈ TIÊU)
CREATE TABLE IF NOT EXISTS rehab_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    plan_id VARCHAR(64) NOT NULL,
    plan_version INTEGER NOT NULL,
    local_date VARCHAR(16) NOT NULL, -- 'YYYY-MM-DD'
    started_at TEXT,
    ended_at TEXT,
    target_count_snapshot INTEGER NOT NULL, -- Khóa chỉ tiêu lúc bắt đầu phiên
    verified_count INTEGER NOT NULL DEFAULT 0,
    completion_source VARCHAR(64) DEFAULT 'camera', -- 'camera', 'manual_reviewed'
    status VARCHAR(32) NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'reminded', 'in_progress', 'camera_verified_completed', 'incomplete', 'verification_unavailable', 'manual_reviewed'
    )),
    algorithm_version VARCHAR(64) DEFAULT 'MediaPipe-Pose-FSM-v1.0',
    notes TEXT DEFAULT '',
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (plan_id) REFERENCES rehab_plans(plan_id) ON DELETE RESTRICT,
    UNIQUE(patient_id, plan_id, local_date)
);

-- 5. SỰ KIỆN TỪNG LẦN LẶP ĐƯỢC CAMERA CHẤP NHẬN (REHAB_EVENTS - CHỐNG ĐẾM LẶP)
CREATE TABLE IF NOT EXISTS rehab_events (
    event_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    patient_id VARCHAR(64) NOT NULL,
    exercise_id VARCHAR(64) NOT NULL,
    rep_number INTEGER NOT NULL,
    score REAL NOT NULL,
    timestamp_utc TEXT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE, -- Chống gửi lặp cùng 1 rep
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES rehab_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- 6. GHI NHẬN BẤT THƯỜNG DO NGƯỜI NHÀ GỬI (CAREGIVER_REPORTS)
CREATE TABLE IF NOT EXISTS caregiver_reports (
    report_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    caregiver_id VARCHAR(64) NOT NULL,
    description TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    severity VARCHAR(32) NOT NULL DEFAULT 'NORMAL' CHECK(severity IN ('LOW', 'NORMAL', 'URGENT')),
    status VARCHAR(32) NOT NULL DEFAULT 'NEW' CHECK(status IN ('NEW', 'VIEWED', 'ADDITIONAL_INFO', 'RESPONDED', 'CLOSED')),
    attachments TEXT DEFAULT '[]', -- JSON danh sách đường dẫn file/ảnh
    created_at TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (caregiver_id) REFERENCES caregivers(caregiver_id) ON DELETE RESTRICT
);

-- Bảng phản hồi trao đổi của Bác sĩ đối với báo cáo người nhà
CREATE TABLE IF NOT EXISTS report_messages (
    message_id VARCHAR(64) PRIMARY KEY,
    report_id VARCHAR(64) NOT NULL,
    sender_user_id VARCHAR(64) NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'sent' CHECK(status IN ('sent', 'delivered', 'read')),
    FOREIGN KEY (report_id) REFERENCES caregiver_reports(report_id) ON DELETE CASCADE,
    FOREIGN KEY (sender_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 7. LỜI KHUYÊN CHUYÊN MÔN THEO PHIÊN BẢN CỦA BÁC SĨ (CARE_RECOMMENDATIONS)
CREATE TABLE IF NOT EXISTS care_recommendations (
    recommendation_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    clinician_id VARCHAR(64) NOT NULL,
    category VARCHAR(64) NOT NULL CHECK(category IN ('nutrition', 'exercise', 'mental', 'general')),
    title VARCHAR(128) NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'SUPERSEDED', 'REVOKED')),
    is_simulation INTEGER NOT NULL DEFAULT 1 CHECK(is_simulation IN (0, 1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (clinician_id) REFERENCES clinicians(clinician_id) ON DELETE RESTRICT
);

-- Biên nhận xem lời khuyên
CREATE TABLE IF NOT EXISTS recommendation_receipts (
    receipt_id VARCHAR(64) PRIMARY KEY,
    recommendation_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    read_at TEXT NOT NULL,
    FOREIGN KEY (recommendation_id) REFERENCES care_recommendations(recommendation_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    UNIQUE(recommendation_id, user_id)
);

-- 8. DANH BẠ VÀ LỊCH TRỰC KHẨN CẤP (EMERGENCY_CONTACTS)
CREATE TABLE IF NOT EXISTS emergency_contacts (
    contact_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    contact_type VARCHAR(32) NOT NULL CHECK(contact_type IN ('clinician_on_duty', 'local_emergency', 'caregiver_primary')),
    contact_name VARCHAR(128) NOT NULL,
    phone VARCHAR(32) NOT NULL,
    on_duty_start VARCHAR(8) DEFAULT '08:00', -- 'HH:MM'
    on_duty_end VARCHAR(8) DEFAULT '17:00',   -- 'HH:MM'
    is_verified INTEGER NOT NULL DEFAULT 0 CHECK(is_verified IN (0, 1)), -- 0: số demo/chưa xác nhận, 1: số thật
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    priority INTEGER NOT NULL DEFAULT 1,
    notes TEXT DEFAULT '',
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT
);

-- Nhật ký sự kiện bấm gọi khẩn cấp (không giả lập kết quả cuộc gọi)
CREATE TABLE IF NOT EXISTS emergency_contact_events (
    event_id VARCHAR(64) PRIMARY KEY,
    patient_id VARCHAR(64) NOT NULL,
    initiator_user_id VARCHAR(64) NOT NULL,
    contact_id VARCHAR(64),
    phone_snapshot VARCHAR(32) NOT NULL,
    on_duty_status VARCHAR(32) NOT NULL CHECK(on_duty_status IN ('ON_DUTY', 'OFF_DUTY')),
    channel VARCHAR(32) NOT NULL DEFAULT 'TEL_PROMPT',
    user_reported_status VARCHAR(64) DEFAULT 'dial_initiated',
    timestamp_utc TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (initiator_user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 9. NHẬT KÝ KIỂM TOÁN HỆ THỐNG (AUDIT_EVENTS)
CREATE TABLE IF NOT EXISTS audit_events (
    audit_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    action VARCHAR(64) NOT NULL, -- 'READ_RECORDS', 'REVISE_PRESCRIPTION', 'ACK_ALERT', 'POST_RECOMMENDATION', 'SUBMIT_REPORT'
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    details TEXT DEFAULT '{}',
    ip_address VARCHAR(45) DEFAULT '127.0.0.1',
    timestamp_utc TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- ==============================================================================
-- INDEX TỐI ƯU TRUY VẤN
-- ==============================================================================
CREATE INDEX IF NOT EXISTS idx_rehab_sessions_patient_date ON rehab_sessions(patient_id, local_date);
CREATE INDEX IF NOT EXISTS idx_rehab_events_session ON rehab_events(session_id);
CREATE INDEX IF NOT EXISTS idx_caregiver_reports_patient ON caregiver_reports(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_recommendations_patient ON care_recommendations(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_emergency_contacts_patient ON emergency_contacts(patient_id, is_active);
CREATE INDEX IF NOT EXISTS idx_audit_user_action ON audit_events(user_id, action, timestamp_utc DESC);

