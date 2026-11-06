import os
os.environ['AI_CARE_TEST_MODE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import tempfile
import unittest
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from unittest.mock import patch, MagicMock

from features.medication.medication_service import MedicationService, due_times, validate_schedule
from shared.services.email_service import EmailService, DeliveryResult


class MedicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026,10,2,0,59,tzinfo=timezone.utc)
        self.path = Path(self.tmp.name)/'medication.sqlite3'
        self.service = MedicationService(self.path,clock=lambda:self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def schedule(self, **kwargs):
        d = dict(patient_id='p1',medication_name='Thuốc mẫu',instructions_text='Theo đơn mẫu',
                 local_times=['08:00'],timezone='Asia/Ho_Chi_Minh',start_date='2026-10-02',
                 weekdays=list(range(7)),reminder_delay_minutes=5,overdue_minutes=30,
                 caregiver_alert_enabled=True)
        d.update(kwargs)
        return self.service.save_schedule(d)

    def due(self):
        self.now += timedelta(minutes=1)
        self.service.tick()
        return self.service.occurrences()[0]

    def test_confirm_idempotent_and_persistent(self):
        self.schedule()
        d = self.due()
        self.assertEqual(d['status'],'REMINDING')
        for _ in range(2):
            self.service.act(d['occurrence_id'],'CONFIRMED')
        with self.service.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM actions').fetchone()[0],1)
        restart = MedicationService(self.path,clock=lambda:self.now)
        restart.tick()
        self.assertEqual(restart.occurrences()[0]['status'],'CONFIRMED')
        self.assertEqual(len(restart.occurrences()),8)

    def test_simultaneous_patients_independent(self):
        self.schedule()
        self.schedule(patient_id='p2')
        self.due()
        self.assertEqual(len([d for d in self.service.occurrences() if d['status']=='REMINDING']),2)
        self.assertEqual({d['patient_id'] for d in self.service.occurrences('p2')},{'p2'})

    def test_snooze_then_late_confirmation(self):
        self.schedule()
        d = self.due()
        self.service.act(d['occurrence_id'],'SNOOZED')
        self.now += timedelta(minutes=5)
        self.service.tick()
        self.assertEqual(self.service.occurrences()[0]['status'],'REMINDING')
        self.now += timedelta(minutes=30)
        self.service.tick()
        self.assertEqual(self.service.occurrences()[0]['status'],'OVERDUE')
        result = self.service.act(d['occurrence_id'],'CONFIRMED')
        self.assertEqual(result['confirmed_at'],self.now.isoformat())

    def test_skip_terminal_and_no_email(self):
        self.schedule()
        d = self.due()
        self.service.act(d['occurrence_id'],'SKIPPED')
        self.now += timedelta(hours=1)
        self.service.tick()
        self.assertIsNone(self.service.claim_alert())

    def test_revision_preserves_past_and_cancels_future(self):
        s = self.schedule()
        self.due()
        self.now += timedelta(minutes=1)
        s['local_times'] = ['09:00']
        changed = self.service.save_schedule(s,s['schedule_id'])
        self.assertEqual(changed['revision'],2)
        self.assertEqual(self.service.occurrences()[0]['revision'],1)
        self.assertTrue(any(d['status']=='CANCELLED' for d in self.service.occurrences()))
        self.assertTrue(any(d['revision']==2 and '09:00' in d['local_due_time'] for d in self.service.occurrences()))

    def test_disable_preserves_history(self):
        s = self.schedule()
        self.due()
        self.now += timedelta(seconds=1)
        s['active'] = False
        self.service.save_schedule(s,s['schedule_id'])
        self.assertFalse(any(d['status']=='PENDING' for d in self.service.occurrences()))

    def test_restart_downtime_suppresses_old_email(self):
        self.schedule()
        self.now += timedelta(hours=3)
        restart = MedicationService(self.path,clock=lambda:self.now)
        restart.tick()
        d = restart.occurrences()[0]
        self.assertEqual(d['status'],'OVERDUE')
        self.assertEqual(d['caregiver_alert_status'],'SUPPRESSED_OLD')
        self.assertIn('Không giám sát',d['coverage'])
        self.assertIsNone(restart.claim_alert())
        self.assertTrue(restart.coverage())

    def test_long_downtime_recovers_unmaterialized_days(self):
        self.schedule()
        self.now += timedelta(days=15,hours=3)
        self.service.tick()
        historical = [d for d in self.service.occurrences() if d['status']=='MISSED']
        self.assertEqual(len(historical),15)
        self.assertTrue(all(d['caregiver_alert_status']=='SUPPRESSED_OLD' for d in historical))

    def test_clock_backwards_no_duplicate(self):
        self.schedule()
        self.due()
        count = len(self.service.occurrences())
        self.now -= timedelta(hours=1)
        self.service.tick()
        self.assertEqual(len(self.service.occurrences()),count)
        self.assertEqual(self.service.coverage()[0]['reason'],'CLOCK_BACKWARD')

    def test_claim_email_once_and_bounded_retry(self):
        self.schedule()
        self.due()
        self.now += timedelta(minutes=31)
        self.service.tick()
        d = self.service.claim_alert()
        self.assertIsNotNone(d)
        self.assertIsNone(self.service.claim_alert())
        self.service.finish_alert(d['occurrence_id'],DeliveryResult(False,'FAILED_NETWORK','offline'))
        self.assertIsNone(self.service.claim_alert())
        self.now += timedelta(minutes=3)
        d = self.service.claim_alert()
        self.assertEqual(d['caregiver_alert_attempts'],2)
        self.service.finish_alert(d['occurrence_id'],DeliveryResult(True,'SMTP_ACCEPTED','ok'))
        self.now += timedelta(hours=1)
        self.assertIsNone(self.service.claim_alert())

    def test_interrupted_send_not_resent(self):
        self.schedule()
        self.due()
        self.now += timedelta(minutes=31)
        self.service.tick()
        self.service.claim_alert()
        restart = MedicationService(self.path,clock=lambda:self.now)
        self.assertEqual(restart.occurrences()[0]['caregiver_alert_status'],'DELIVERY_UNKNOWN')
        self.assertIsNone(restart.claim_alert())

    def test_email_hourly_limit(self):
        for i in range(4):
            self.schedule(patient_id=f'p{i}')
        self.due()
        self.now += timedelta(minutes=31)
        self.service.tick()
        for _ in range(3):
            d = self.service.claim_alert()
            self.assertIsNotNone(d)
            self.service.finish_alert(d['occurrence_id'],DeliveryResult(True,'SMTP_ACCEPTED','ok'))
        self.assertIsNone(self.service.claim_alert())

    def test_weekday_and_dst_policy(self):
        d = validate_schedule(dict(patient_id='p',medication_name='x',instructions_text='x',local_times=['02:30'],
                                   timezone='America/New_York',start_date='2026-03-01',weekdays=[6]))
        self.assertEqual(list(due_times(d,date(2026,3,8),date(2026,3,8))),[])
        d['local_times'] = ['01:30']
        self.assertEqual(len(list(due_times(d,date(2026,11,1),date(2026,11,1)))),1)
        self.assertEqual(list(due_times(d,date(2026,11,2),date(2026,11,2))),[])

    def test_network_blocked_at_core(self):
        e = EmailService(enabled=True,username='real@example.com',password='secret',mode='medication')
        with patch('smtplib.SMTP',side_effect=AssertionError('network attempted')) as smtp:
            self.assertEqual(e.send_medication_overdue('family@example.com','Demo','08:00','id').status,'DRY_RUN')
            self.assertEqual(e.test_connection().status,'DRY_RUN')
            smtp.assert_not_called()

    def test_imported_unittest_does_not_disable_production_email(self):
        # ML packages can import unittest in a normal production process.
        with patch.dict(os.environ,{},clear=True), patch('sys.argv',['apps/desktop/main.py']):
            self.assertFalse(EmailService(mode='medication').network_blocked())
        with patch.dict(os.environ,{},clear=True), patch('sys.argv',['unittest/__main__.py']):
            self.assertTrue(EmailService(mode='medication').network_blocked())

    def test_smtp_success_and_ambiguous_failure_using_fake_transport(self):
        e = EmailService(enabled=True,username='fake@example.com',password='fake',mode='medication')
        with patch.object(e,'network_blocked',return_value=False), patch('smtplib.SMTP') as smtp:
            smtp.return_value.send_message.return_value = {}
            self.assertEqual(e.send_medication_overdue('family@example.com','Demo','08:00','id').status,'SMTP_ACCEPTED')
            smtp.return_value.send_message.side_effect = TimeoutError()
            self.assertEqual(e.send_medication_overdue('family@example.com','Demo','08:00','id').status,'DELIVERY_UNKNOWN')

    def test_validation(self):
        for changes in [dict(local_times=['24:00']),dict(end_date='2026-01-01'),dict(weekdays=[]),dict(medication_name='')]:
            with self.assertRaises(ValueError):
                self.schedule(**changes)

    def test_pending_notification_survives_schedule_editor_and_restart(self):
        self.schedule()
        d = self.due()
        self.assertNotIn('reminded_at',d)
        restart = MedicationService(self.path,clock=lambda:self.now)
        self.assertIn(d['occurrence_id'],restart.tick())
        restart.acknowledge_notifications([d['occurrence_id']])
        self.assertEqual(restart.tick(),[])
        self.assertEqual(restart.occurrences()[0]['reminded_at'],self.now.isoformat())

    def test_edit_at_exact_due_does_not_duplicate_current_dose(self):
        s = self.schedule()
        self.due()
        self.service.save_schedule(s,s['schedule_id'])
        current = [d for d in self.service.occurrences() if d['status']=='REMINDING']
        self.assertEqual(len(current),1)

    def test_restart_preserves_accepted_email_status(self):
        self.schedule()
        self.due()
        self.now += timedelta(minutes=31)
        self.service.tick()
        d = self.service.claim_alert()
        self.service.finish_alert(d['occurrence_id'],DeliveryResult(True,'SMTP_ACCEPTED','ok'))
        self.now += timedelta(hours=3)
        restart = MedicationService(self.path,clock=lambda:self.now)
        restart.tick()
        self.assertEqual(restart.occurrences()[0]['caregiver_alert_status'],'SMTP_ACCEPTED')

    def test_retry_exhausted_after_three_pre_submission_failures(self):
        self.schedule()
        self.due()
        self.now += timedelta(minutes=31)
        self.service.tick()
        for attempt in range(1,4):
            d = self.service.claim_alert()
            self.assertEqual(d['caregiver_alert_attempts'],attempt)
            self.service.finish_alert(d['occurrence_id'],DeliveryResult(False,'FAILED_NETWORK','offline'))
            self.now += timedelta(minutes=10)
        self.now += timedelta(hours=2)
        self.assertIsNone(self.service.claim_alert())

    def test_midnight_end_date_and_timezone_revision(self):
        s = self.schedule(local_times=['23:59'],end_date='2026-10-02')
        self.now = datetime(2026,10,2,16,59,tzinfo=timezone.utc)
        self.service.tick()
        d = self.service.occurrences()[0]
        self.assertEqual(len(self.service.occurrences()),1)
        self.assertEqual(d['due_at_utc'],self.now.isoformat())
        self.now += timedelta(minutes=2)
        s['timezone'] = 'UTC'
        self.service.save_schedule(s,s['schedule_id'])
        rows = self.service.occurrences()
        self.assertEqual(rows[0]['local_due_time'],d['local_due_time'])
        self.assertEqual(rows[1]['due_at_utc'],'2026-10-02T23:59:00+00:00')

    def test_unconfigured_and_invalid_recipient_do_not_connect(self):
        with patch('smtplib.SMTP') as smtp:
            e = EmailService(enabled=False,mode='medication')
            with patch.object(e,'network_blocked',return_value=False):
                self.assertEqual(e.send_medication_overdue('care@example.com','Demo','08:00','x').status,'UNCONFIGURED')
            e = EmailService(enabled=True,username='fake@example.com',password='fake',mode='medication')
            with patch.object(e,'network_blocked',return_value=False):
                self.assertEqual(e.send_medication_overdue('bad-address','Demo','08:00','x').status,'INVALID_EMAIL')
            smtp.assert_not_called()

    def test_long_confirmation_window_reaches_overdue_before_missed(self):
        self.service.catchup_minutes = 2000
        self.schedule(overdue_minutes=1440,end_date='2026-10-02')
        self.due()
        self.now += timedelta(days=1)
        self.service.tick()
        self.assertEqual(self.service.occurrences()[0]['status'],'OVERDUE')
        self.assertIsNotNone(self.service.claim_alert())


if __name__ == '__main__':
    unittest.main()
