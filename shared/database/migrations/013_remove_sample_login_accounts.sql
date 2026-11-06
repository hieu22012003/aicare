-- Remove only the seven shipped caregiver/clinician demo identities.
-- Patient records, administrator and independently registered users are retained.
BEGIN IMMEDIATE;
CREATE TEMP TABLE removed_demo_users AS
SELECT user_id FROM users WHERE (user_id, lower(email)) IN (
 ('user_care_001', 'tuan.caregiver@example.com'),
 ('user_care_002', 'lan.caregiver@example.com'),
 ('user_care_003', 'binh.old@example.com'),
 ('user_care_004', 'contact-a2e402b0@example.com'),
 ('user_doc_001', 'dr.an@laokhoa.example.com'),
 ('user_doc_002', 'contact-4bcc03e4@example.com'),
 ('user_doc_003', 'dr.cuong@laokhoa.example.com')
);
DELETE FROM patient_caregivers WHERE caregiver_id IN
 (SELECT caregiver_id FROM caregivers WHERE user_id IN (SELECT user_id FROM removed_demo_users));
DELETE FROM care_team WHERE clinician_id IN
 (SELECT clinician_id FROM clinicians WHERE user_id IN (SELECT user_id FROM removed_demo_users));
DELETE FROM caregivers WHERE user_id IN (SELECT user_id FROM removed_demo_users);
DELETE FROM clinicians WHERE user_id IN (SELECT user_id FROM removed_demo_users);
DELETE FROM users WHERE user_id IN (SELECT user_id FROM removed_demo_users);
DROP TABLE removed_demo_users;
COMMIT;
