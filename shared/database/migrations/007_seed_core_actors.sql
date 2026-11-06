-- 007_seed_core_actors.sql
-- Seed baseline actors (users, patients, caregivers, clinicians, relationships, and legacy ID maps)
-- Ensures 100% out-of-the-box readiness for all clean/release deployments without external imports.

-- 1. NGƯỜI DÙNG CƠ SỞ (USERS)
INSERT INTO users (user_id, email, phone, full_name, password_hash, role, is_active, created_at)
VALUES
    ('user_admin_001', 'admin@unifiedcare.local', '0900000000', 'Quản trị viên Hệ thống', '!', 'admin', 1, '2026-01-01T00:00:00Z'),
    ('user_pat_001', 'hoanganh.patient@example.com', '0901111222', 'Cụ Hoàng Anh', '', 'patient', 1, '2026-01-01T00:00:00Z'),
    ('user_pat_002', 'maihuong.patient@example.com', '0902222333', 'Cụ Mai Hương', '', 'patient', 1, '2026-01-01T00:00:00Z')
ON CONFLICT(user_id) DO NOTHING;

-- 2. HỒ SƠ NGƯỜI CAO TUỔI (PATIENTS)
INSERT INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes)
VALUES
    ('patient_001', 'user_pat_001', 'Cụ Hoàng Anh', '1951-04-15', 'Nam', 'Asia/Ho_Chi_Minh', 'ACTIVE', 'Tiền sử tăng huyết áp nhẹ, minh mẫn'),
    ('patient_002', 'user_pat_002', 'Cụ Mai Hương', '1948-09-20', 'Nữ', 'Asia/Ho_Chi_Minh', 'ACTIVE', 'Đái tháo đường type 2, cần nhắc nhở đều đặn')
ON CONFLICT(patient_id) DO NOTHING;

-- 7. ÁNH XẠ ĐỊNH DANH CŨ (LEGACY_PATIENT_MAP)
INSERT INTO legacy_patient_map (legacy_id, source_system, patient_id)
VALUES
    ('1', 'camera_face', 'patient_001'),
    ('patient_001', 'medication_module', 'patient_001'),
    ('pat-001', 'health_module', 'patient_001'),
    ('2', 'camera_face', 'patient_002'),
    ('patient_002', 'medication_module', 'patient_002'),
    ('pat-002', 'health_module', 'patient_002')
ON CONFLICT(legacy_id) DO NOTHING;
