-- Required reference data must exist even when the legacy importer is not run.
-- Preserve existing identifiers referenced by observations and measurement requests.
INSERT INTO observation_types
    (observation_type_id, code, name, standard_unit, min_plausible_value, max_plausible_value, category)
VALUES
    ('ot-sys', 'blood_pressure_systolic', 'Huyết áp tâm thu', 'mmHg', 50, 300, 'vitals'),
    ('ot-dia', 'blood_pressure_diastolic', 'Huyết áp tâm trương', 'mmHg', 30, 200, 'vitals'),
    ('ot-hr', 'heart_rate', 'Nhịp tim', 'bpm', 30, 250, 'vitals'),
    ('ot-spo2', 'spo2', 'Độ bão hòa oxy SpO2', '%', 50, 100, 'vitals'),
    ('ot-temp', 'temperature', 'Thân nhiệt', '°C', 34, 43, 'vitals'),
    ('ot-glu', 'blood_glucose', 'Đường huyết', 'mg/dL', 20, 600, 'labs'),
    ('ot-weight', 'weight', 'Cân nặng', 'kg', 20, 250, 'physical')
ON CONFLICT DO NOTHING;
