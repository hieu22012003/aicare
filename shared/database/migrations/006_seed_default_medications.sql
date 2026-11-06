-- 006_seed_default_medications.sql
-- Seed baseline medications and ensure demo care team relationships for clean/release deployments.

-- 1. DANH MỤC THUỐC MẪU (DEFAULT MEDICATIONS)
INSERT INTO medications (medication_id, brand_name, generic_name, product_strength, strength_unit, dosage_form, image_path, safety_notes)
VALUES
    ('med_001', 'Amlor 5mg (Amlodipine)', 'Amlodipine besylate', '5', 'mg', 'Viên nén', '', 'Thuốc hạ huyết áp nhóm chẹn kênh canxi.'),
    ('med_002', 'Glucophage 500mg (Metformin)', 'Metformin Hydrochloride', '500', 'mg', 'Viên bao phim', '', 'Thuốc kiểm soát đường huyết.'),
    ('med_003', 'Panadol 500mg (Paracetamol)', 'Paracetamol', '500', 'mg', 'Viên nén', '', 'Thuốc giảm đau, hạ sốt thông thường.'),
    ('med_004', 'Losartan 50mg (Cozaar)', 'Losartan potassium', '50', 'mg', 'Viên nén', '', 'Thuốc điều trị tăng huyết áp.'),
    ('med_005', 'Lipitor 20mg (Atorvastatin)', 'Atorvastatin calcium', '20', 'mg', 'Viên bao phim', '', 'Thuốc điều hòa mỡ máu, bảo vệ tim mạch.')
ON CONFLICT(medication_id) DO NOTHING;
