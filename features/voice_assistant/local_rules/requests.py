"""Persistent assistance extension using the unified DB and existing outbox/SMTP transport.
SMTP cannot guarantee exactly-once after process death: uncertain claims stay unknown,
never automatically resend. No state here means clinician acknowledgement.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from shared.database.outbox_helper import insert_outbox_event


def now():
    return datetime.now(timezone.utc)


class RequestService:
    def __init__(self, db, transport=None, retention_days=30, request_ttl_seconds=1800, retry_seconds=5, retry_max=3):
        self.db, self.transport, self.retention_days = db, transport, retention_days
        self.request_ttl_seconds, self.retry_seconds, self.retry_max = request_ttl_seconds, retry_seconds, retry_max

    def resolve(self, patient, roles):
        if not patient:
            raise ValueError('Hãy chọn hồ sơ; chưa thể gửi. Dùng liên hệ thủ công nếu cần gấp.')
        if len(roles)>1:
            unique = {}
            for role in roles:
                try:
                    for recipient in self.resolve(patient,[role]):
                        unique[recipient['user_id']] = recipient
                except ValueError:
                    continue
            if not unique:
                raise ValueError('Chưa có người nhận hợp lệ; hãy dùng liên hệ thủ công ngay.')
            return list(unique.values())
        stamp = now().isoformat()
        found = {}
        with self.db.connect() as conn:
            if not conn.fetchone("SELECT patient_id FROM patients WHERE patient_id=? AND status='ACTIVE'", (patient,)):
                raise ValueError('Hồ sơ không hợp lệ')
            for role in roles:
                if role == 'doctor':
                    rows = conn.fetchall('''SELECT u.user_id, u.email, u.full_name FROM care_team r
                        JOIN clinicians c ON r.clinician_id=c.clinician_id JOIN users u ON c.user_id=u.user_id
                        WHERE r.patient_id=? AND r.is_active=1 AND r.consent_status='GRANTED'
                        AND r.valid_from<=? AND r.valid_to>=? AND u.is_active=1
                        ORDER BY CASE WHEN r.role_description LIKE '%phụ trách%' THEN 0 ELSE 1 END, r.care_team_id''', (patient,stamp,stamp))
                    if not rows:
                        raise ValueError('Chưa có một bác sĩ phụ trách xác định; hãy dùng liên hệ thủ công.')
                    rows = rows[:1]
                else:
                    rows = conn.fetchall('''SELECT u.user_id, u.email, u.full_name, r.notification_priority FROM patient_caregivers r
                        JOIN caregivers c ON r.caregiver_id=c.caregiver_id JOIN users u ON c.user_id=u.user_id
                        WHERE r.patient_id=? AND r.is_active=1 AND r.consent_status='GRANTED'
                        AND r.valid_from<=? AND r.valid_to>=? AND u.is_active=1
                        ORDER BY r.is_primary DESC, r.notification_priority ASC, r.patient_caregiver_id ASC''', (patient,stamp,stamp))
                    if not rows:
                        raise ValueError('Chưa có người chăm sóc mặc định xác định; hãy dùng liên hệ thủ công.')
                    rows = rows[:1]
                row = rows[0]
                if not row['email']:
                    raise ValueError('Người nhận chưa có email; hãy dùng liên hệ thủ công.')
                found[row['user_id']] = {k:row[k] for k in ('user_id','email','full_name')}
        return list(found.values())

    def manual_contacts(self, patient):
        """Read configured verified telephone contacts; never launch a call."""
        from zoneinfo import ZoneInfo
        if not patient:
            raise ValueError('Hãy chọn hồ sơ để xem liên hệ đã cấu hình.')
        with self.db.connect() as conn:
            profile=conn.fetchone("SELECT timezone FROM patients WHERE patient_id=? AND status='ACTIVE'",(patient,))
            if not profile:
                raise ValueError('Hồ sơ không hợp lệ')
            rows=conn.fetchall('SELECT contact_name, phone, on_duty_start, on_duty_end FROM emergency_contacts WHERE patient_id=? AND is_active=1 AND is_verified=1 ORDER BY priority',(patient,))
        stamp=now().astimezone(ZoneInfo(profile['timezone'])).strftime('%H:%M')
        contacts=[]
        for row in rows:
            start,end=row['on_duty_start'],row['on_duty_end']
            on_duty = start<=stamp<=end if start<=end else stamp>=start or stamp<=end
            if on_duty:
                contacts.append(row)
        return contacts

    def list_for_recipient(self, patient, user_id):
        from features.web_portal.auth import AuthService
        allowed, _ = AuthService(self.db).check_patient_access(user_id,patient)
        if not allowed:
            raise PermissionError('Không có quyền xem hồ sơ')
        with self.db.connect() as conn:
            rows=conn.fetchall('''SELECT r.request_id, r.intent, r.original_text, r.priority, r.status,
                r.created_at, d.status AS delivery_status, d.transport_status
                FROM voice_requests r JOIN voice_deliveries d USING(request_id)
                WHERE r.patient_id=? AND d.recipient_id=? ORDER BY r.created_at DESC LIMIT 50''',(patient,user_id))
        for row in rows:
            if row['delivery_status']=='sending':
                row['delivery_status']='unknown'
        return rows

    def transition(self, request_id, patient, user_id, target):
        from features.web_portal.auth import AuthService
        allowed, _ = AuthService(self.db).check_patient_access(user_id,patient)
        if not allowed:
            raise PermissionError('Không còn quyền truy cập')
        if target not in ('acknowledged','resolved'):
            raise ValueError('Trạng thái không hợp lệ')
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            request=conn.fetchone('SELECT r.* FROM voice_requests r JOIN voice_deliveries d USING(request_id) WHERE request_id=? AND r.patient_id=? AND d.recipient_id=?',(request_id,patient,user_id))
            if not request:
                raise PermissionError('Chỉ người nhận được chỉ định mới được tiếp nhận')
            if request['expires_at']<now().isoformat() and request['status']=='open':
                raise ValueError('Yêu cầu đã hết hạn')
            if request['status']=='cancelled':
                raise ValueError('Yêu cầu đã hủy')
            if request['status']==target:
                return
            if target=='resolved' and request['status']!='acknowledged':
                raise ValueError('Cần tiếp nhận trước khi giải quyết')
            if request['status']=='resolved':
                raise ValueError('Yêu cầu đã giải quyết')
            conn.execute('UPDATE voice_requests SET status=? WHERE request_id=?',(target,request_id))
            self._event(conn,patient,request_id,target,user_id)

    def create(self, decision, action, recipients):
        # Resolve again immediately before persistence; no stale recipient snapshot.
        current = self.resolve(decision['patient_id'], action['roles'])
        if current != recipients:
            raise ValueError('Người nhận đã đổi; cần xác nhận lại.')
        uid = decision['utterance_id']
        if not uid:
            raise ValueError('Thiếu final utterance ID')
        identity = action['intent']
        request_id = 'voice_' + hashlib.sha256((uid+'|'+identity).encode()).hexdigest()[:32]
        stamp = now()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.fetchone('SELECT * FROM voice_requests WHERE request_id=?', (request_id,))
            if existing:
                if existing['patient_id'] != decision['patient_id']:
                    raise ValueError('Utterance ID đã thuộc hồ sơ khác')
                return request_id
            conn.execute('''INSERT INTO voice_requests VALUES (?,?,?,?,?,?,?,?,?,'voice','open',?,?)''',
                         (request_id,decision['patient_id'],uid,identity,action['intent'],decision['original_text'],
                          action['priority'],json.dumps(dict(decision['context_refs'],recipient_roles=action['roles'])),json.dumps(decision['rule_ids']),
                          stamp.isoformat(),(stamp+timedelta(seconds=self.request_ttl_seconds)).isoformat()))
            for r in recipients:
                conn.execute("INSERT INTO voice_deliveries(request_id,recipient_id,recipient_email,status) VALUES (?,?,?,'queued')",
                             (request_id,r['user_id'],r['email']))
            self._event(conn, decision['patient_id'], request_id, 'requested')
        return request_id

    def _event(self, conn, patient, request_id, event, actor_id=None):
        insert_outbox_event(conn, patient, 'voice.request_'+event, 'voice_request', request_id,
                            actor_id=actor_id, source='recipient_portal' if actor_id else 'voice',
                            payload_minimal={'request_id':request_id})

    def cancel(self, request_id, patient):
        with self.db.connect() as conn:
            row = conn.fetchone('SELECT * FROM voice_requests WHERE request_id=? AND patient_id=?', (request_id,patient))
            if not row:
                raise ValueError('Yêu cầu không thuộc hồ sơ')
            conn.execute("UPDATE voice_requests SET status='cancelled' WHERE request_id=?", (request_id,))
            conn.execute("UPDATE voice_deliveries SET status='cancelled' WHERE request_id=? AND status IN ('queued','failed')",(request_id,))
            # Keep original transcript and sent delivery evidence; publish correction.
            self._event(conn, patient, request_id, 'cancelled')

    def deliver(self, online=True, retry_failed=False, force_retry=False, request_id=None):
        if not online or not self.transport:
            return
        target_request_id = request_id
        # A persisted sending claim from a dead process is not safe to retry.
        with self.db.connect() as conn:
            rows = conn.fetchall('''SELECT d.*, r.patient_id, r.original_text, r.intent, r.priority, r.created_at, r.expires_at, r.context_refs, r.status AS request_status
                                  FROM voice_deliveries d JOIN voice_requests r USING(request_id)
                                  WHERE d.status IN ('queued','failed')''')
        for row in rows:
            if target_request_id and row['request_id'] != target_request_id:
                continue
            if row['status'] == 'failed':
                if not retry_failed or row['attempts']>=self.retry_max:
                    continue
                if row['transport_status'] not in ('FAILED_NETWORK','UNCONFIGURED') and not force_retry:
                    continue
                if row['next_attempt_at'] and row['next_attempt_at']>now().isoformat() and not force_retry:
                    continue
            req_id, recipient = row['request_id'], row['recipient_id']
            roles = json.loads(row['context_refs']).get('recipient_roles', [])
            if not roles:
                continue  # Never guess the roles of a legacy/unreviewed persisted request.
            try:
                mapped = self.resolve(row['patient_id'], roles)
                allowed = any(r['user_id']==recipient and r['email']==row['recipient_email'] for r in mapped)
            except ValueError:
                allowed = False
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                live = conn.fetchone('SELECT * FROM voice_requests WHERE request_id=?', (req_id,))
                if live['status'] != 'open' or live['expires_at'] < now().isoformat():
                    conn.execute("UPDATE voice_deliveries SET status='cancelled' WHERE request_id=? AND recipient_id=? AND status IN ('queued','failed')",(req_id,recipient))
                    continue
                if not allowed:
                    conn.execute("UPDATE voice_deliveries SET status='failed', transport_status='mapping_changed' WHERE request_id=? AND recipient_id=?",(req_id,recipient))
                    continue
                claim = conn.execute("UPDATE voice_deliveries SET status='sending', attempts=attempts+1 WHERE request_id=? AND recipient_id=? AND status=?",(req_id,recipient,row['status']))
                if claim.rowcount != 1:
                    continue
            try:
                result = self.transport.send_voice_request(row['recipient_email'], row['intent'],
                    row['original_text'], row['created_at'], req_id+'_'+recipient, row['priority'], json.loads(row['context_refs']))
                code = result.status
                status = 'sent' if result.success and code=='SMTP_ACCEPTED' else 'unknown' if code=='DELIVERY_UNKNOWN' else 'failed'
            except Exception:
                status, code = 'unknown', 'TRANSPORT_EXCEPTION'
            with self.db.connect() as conn:
                retry_at = (now()+timedelta(seconds=self.retry_seconds * (2 ** min(row['attempts'],6)))).isoformat()
                conn.execute('UPDATE voice_deliveries SET status=?,transport_status=?,next_attempt_at=? WHERE request_id=? AND recipient_id=?', (status,code,retry_at,req_id,recipient))
                self._event(conn,row['patient_id'],req_id,'delivery_updated')

    def status(self, request_id):
        with self.db.connect() as conn:
            r = conn.fetchone('SELECT * FROM voice_requests WHERE request_id=?',(request_id,))
            deliveries = conn.fetchall('SELECT d.recipient_id,u.full_name AS recipient_name,d.status,d.transport_status FROM voice_deliveries d JOIN users u ON u.user_id=d.recipient_id WHERE d.request_id=?',(request_id,))
        for d in deliveries:
            if d['status']=='sending':
                d['status']='unknown'  # Never claim sent before transport records its result.
        return {'request':r, 'deliveries':deliveries}

    def prune(self):
        # Minimum transcript retention; audit outbox contains IDs only.
        cutoff = (now()-timedelta(days=self.retention_days)).isoformat()
        with self.db.connect() as conn:
            conn.execute("UPDATE voice_requests SET original_text='[đã hết hạn lưu]' WHERE created_at<?",(cutoff,))
