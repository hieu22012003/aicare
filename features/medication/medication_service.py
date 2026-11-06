"""Durable medication reminders. Confirmation is self-report, never clinical evidence."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utcnow():
    return datetime.now(UTC)


def stamp(value):
    if value.tzinfo is None:
        raise ValueError("Clock must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def validate_schedule(data):
    d = dict(data)
    for key in ('patient_id', 'medication_name', 'instructions_text'):
        if not isinstance(d.get(key), str) or not d[key].strip():
            raise ValueError(f"{key}: không được để trống")
    ZoneInfo(d['timezone'])
    start = date.fromisoformat(d['start_date'])
    if d.get('end_date') and date.fromisoformat(d['end_date']) < start:
        raise ValueError('Ngày kết thúc phải sau ngày bắt đầu')
    times = d.get('local_times', [])
    if not times or any(time.fromisoformat(t).strftime('%H:%M') != t for t in times):
        raise ValueError('Giờ phải có dạng HH:MM')
    d['local_times'] = sorted(set(times))
    days = d.get('weekdays', list(range(7)))
    if not days or any(type(x) is not int or x not in range(7) for x in days):
        raise ValueError('Chọn ít nhất một ngày trong tuần')
    d['weekdays'] = sorted(set(days))
    for key, default in [('reminder_delay_minutes', 10), ('overdue_minutes', 60)]:
        d[key] = int(d.get(key, default))
        if not 1 <= d[key] <= 1440:
            raise ValueError(f'{key}: phải từ 1 đến 1440 phút')
    d.setdefault('active', True)
    d.setdefault('caregiver_alert_enabled', False)
    return d


def due_times(d, first, last):
    """Skip nonexistent DST times; use first fold once for ambiguous local times."""
    zone = ZoneInfo(d['timezone'])
    day = max(first, date.fromisoformat(d['start_date']))
    last = min(last, date.fromisoformat(d['end_date'])) if d.get('end_date') else last
    while day <= last:
        if day.weekday() in d['weekdays']:
            for hhmm in d['local_times']:
                local = datetime.combine(day, time.fromisoformat(hhmm), zone)
                due = local.astimezone(UTC)
                if due.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None):
                    yield due, local.isoformat()
        day += timedelta(days=1)


class MedicationService:
    def __init__(self, path: Path, clock=utcnow, catchup_minutes=60, alert_limit=3):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.catchup_minutes = max(0, int(catchup_minutes))
        self.alert_limit = max(1, int(alert_limit))
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS schedules(id TEXT, revision INTEGER, data TEXT,
                    PRIMARY KEY(id, revision));
                CREATE TABLE IF NOT EXISTS doses(id TEXT PRIMARY KEY, schedule_id TEXT,
                    revision INTEGER, due TEXT, data TEXT,
                    UNIQUE(schedule_id, revision, due));
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, dose_id TEXT,
                    at TEXT, actor TEXT, action TEXT);
                CREATE TABLE IF NOT EXISTS coverage(start TEXT, end TEXT, reason TEXT);
                PRAGMA user_version=1;
            ''')
            # An interrupted SMTP submission is ambiguous: never automatically resend.
            for row in db.execute('SELECT id,data FROM doses').fetchall():
                d = json.loads(row['data'])
                if d.get('caregiver_alert_status') == 'SENDING':
                    d['caregiver_alert_status'] = 'DELIVERY_UNKNOWN'
                    self._write(db, d)
        self.started = False

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def schedules(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM schedules s WHERE revision=(SELECT MAX(revision) FROM schedules WHERE id=s.id)')]

    def save_schedule(self, data, schedule_id=None):
        d = validate_schedule(data)
        now = self.clock()
        self.tick()  # settle past occurrences using the old revision first
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT MAX(revision) FROM schedules WHERE id=?', (schedule_id,)).fetchone()[0] or 0
            d.update(schedule_id=schedule_id or uuid.uuid4().hex, revision=old+1,
                     effective_at=stamp(now), updated_at=stamp(now))
            # Preserve history and cancel only future occurrences from superseded revisions.
            for row in db.execute('SELECT data FROM doses WHERE schedule_id=? AND due>?', (d['schedule_id'], stamp(now))).fetchall():
                occurrence = json.loads(row[0])
                if occurrence['status'] == 'PENDING':
                    occurrence['status'] = 'CANCELLED'
                    self._write(db, occurrence)
            db.execute('INSERT INTO schedules VALUES(?,?,?)', (d['schedule_id'], d['revision'], json.dumps(d)))
        self.tick()
        return d

    def _write(self, db, d):
        db.execute('UPDATE doses SET data=? WHERE id=?', (json.dumps(d), d['occurrence_id']))

    def occurrences(self, patient_id=None):
        with self.connect() as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM doses ORDER BY due')]
        return [d for d in rows if patient_id is None or d['patient_id'] == patient_id]

    def tick(self):
        now = self.clock()
        now_s = stamp(now)
        reminders = []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            last = db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
            previous = datetime.fromisoformat(last[0]) if last else now
            gap = (now-previous).total_seconds() > 90
            if gap or now < previous:
                db.execute('INSERT INTO coverage VALUES(?,?,?)', (stamp(previous), now_s, 'DOWNTIME' if gap else 'CLOCK_BACKWARD'))
            schedules = [json.loads(r[0]) for r in db.execute('SELECT data FROM schedules s WHERE revision=(SELECT MAX(revision) FROM schedules WHERE id=s.id)')]
            for s in schedules:
                if not s['active']:
                    continue
                effective = datetime.fromisoformat(s['effective_at'])
                zone = ZoneInfo(s['timezone'])
                # All historical days since the last heartbeat are recovered; future preview is seven days.
                first = max(effective, min(previous, now)).astimezone(zone).date()
                for due, local in due_times(s, first, now.astimezone(zone).date()+timedelta(days=7)):
                    if due < effective or (s['revision'] > 1 and due == effective):
                        continue
                    d = dict(s, occurrence_id=f"{s['schedule_id']}:{s['revision']}:{stamp(due)}",
                             schedule_revision=s['revision'],
                             due_at_utc=stamp(due), local_due_time=local, status='PENDING',
                             caregiver_alert_status='PENDING' if s['caregiver_alert_enabled'] else 'DISABLED',
                             caregiver_alert_attempts=0)
                    db.execute('INSERT OR IGNORE INTO doses VALUES(?,?,?,?,?)', (d['occurrence_id'], s['schedule_id'], s['revision'], stamp(due), json.dumps(d)))
            for row in db.execute('SELECT data FROM doses WHERE due<=?', (now_s,)).fetchall():
                d = json.loads(row[0])
                if d['status'] in ('CONFIRMED','SKIPPED','MISSED','CANCELLED'):
                    continue
                due = datetime.fromisoformat(d['due_at_utc'])
                age = (now-due).total_seconds()/60
                if gap and due > previous:
                    d['coverage'] = 'Không giám sát trong khoảng này'
                if (age > self.catchup_minutes and (gap or not self.started)
                        and d['caregiver_alert_status'] in ('PENDING','FAILED_NETWORK')):
                    d['caregiver_alert_status'] = 'SUPPRESSED_OLD'
                # Even a 24-hour confirmation window must reach OVERDUE first.
                if age >= max(1440, d['overdue_minutes'] + 60):
                    d.update(status='MISSED', missed_at=now_s, notification_pending=False)
                elif age >= d['overdue_minutes']:
                    if d['status'] != 'OVERDUE':
                        d['notification_pending'] = True
                    d['status'] = 'OVERDUE'
                elif d['status'] == 'PENDING' or (d['status'] == 'SNOOZED' and d['snoozed_until'] <= now_s):
                    d.update(status='REMINDING', notification_pending=True)
                if d.get('notification_pending'):
                    reminders.append(d['occurrence_id'])
                self._write(db, d)
            # Never move the generation watermark backwards when the system clock changes.
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('heartbeat', max(stamp(previous), now_s)))
        self.started = True
        return reminders

    def acknowledge_notifications(self, occurrence_ids):
        """Only the UI can attest that it has actually displayed a reminder."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for occurrence_id in occurrence_ids:
                row = db.execute('SELECT data FROM doses WHERE id=?', (occurrence_id,)).fetchone()
                if row:
                    d = json.loads(row[0])
                    d.update(notification_pending=False, reminded_at=stamp(self.clock()))
                    self._write(db,d)

    def act(self, occurrence_id, action, actor='Người dùng'):
        if action not in ('CONFIRMED','SNOOZED','SKIPPED'):
            raise ValueError('Thao tác không hợp lệ')
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM doses WHERE id=?', (occurrence_id,)).fetchone()
            if not row:
                raise ValueError('Không tìm thấy lần nhắc')
            d = json.loads(row[0])
            if d['status'] == action and action != 'SNOOZED':
                return d
            if d['status'] in ('CONFIRMED','SKIPPED','CANCELLED','PENDING'):
                raise ValueError('Lần nhắc này chưa đến giờ hoặc đã kết thúc')
            if action == 'SNOOZED' and now >= datetime.fromisoformat(d['due_at_utc'])+timedelta(minutes=d['overdue_minutes']):
                raise ValueError('Đã quá hạn; không thể tiếp tục hoãn')
            d.update(status=action, action_actor=actor, action_at=stamp(now), notification_pending=False)
            d[{'CONFIRMED':'confirmed_at','SKIPPED':'skipped_at','SNOOZED':'snoozed_at'}[action]] = stamp(now)
            if action == 'SNOOZED':
                d['snoozed_until'] = stamp(now+timedelta(minutes=d['reminder_delay_minutes']))
            self._write(db, d)
            db.execute('INSERT INTO actions(dose_id,at,actor,action) VALUES(?,?,?,?)', (occurrence_id, stamp(now), actor, action))
        return d

    def claim_alert(self):
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rate = db.execute("SELECT value FROM meta WHERE key='alert_rate'").fetchone()
            sent = json.loads(rate[0]) if rate else []
            sent = [t for t in sent if datetime.fromisoformat(t) > now-timedelta(hours=1)]
            if len(sent) >= self.alert_limit:
                return None
            for row in db.execute('SELECT data FROM doses ORDER BY due').fetchall():
                d = json.loads(row[0])
                if d['status'] != 'OVERDUE' or d['caregiver_alert_status'] not in ('PENDING','FAILED_NETWORK'):
                    continue
                if d['caregiver_alert_attempts'] >= 3 or d.get('retry_at', '') > stamp(now):
                    continue
                d['caregiver_alert_status'] = 'SENDING'
                d['caregiver_alert_attempts'] += 1
                self._write(db, d)
                sent.append(stamp(now))
                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('alert_rate', json.dumps(sent)))
                return d

    def finish_alert(self, occurrence_id, result):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            d = json.loads(db.execute('SELECT data FROM doses WHERE id=?', (occurrence_id,)).fetchone()[0])
            d.update(caregiver_alert_status=result.status, last_error='' if result.success else result.message,
                     retry_at=stamp(self.clock()+timedelta(minutes=2**d['caregiver_alert_attempts'])))
            self._write(db, d)

    def coverage(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM coverage ORDER BY rowid DESC LIMIT 20')]

    def shutdown(self):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('clean_shutdown', stamp(self.clock())))
