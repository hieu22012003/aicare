"""Two-hour observed-expression proportions with independent sadness alerts."""
import json
import uuid
from datetime import datetime, timezone

from shared.database.outbox_helper import insert_outbox_event

WINDOW_SECONDS = 2 * 60 * 60
SMOOTH_SECONDS = 30
MIN_OBSERVED = 30 * 60
MAX_SAMPLE_GAP = 5.0
LABELS = ('Happy', 'Neutral', 'Sad')


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class EmotionSummaryService:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def _new_state(now):
        return dict(start=now, durations=dict.fromkeys(LABELS, 0.0),
                    sample_at=now, accounted_until=now, label=None,
                    alerted=False, updated_at=now, bucket_start=now, bucket=[], recent=[], run_start=None, run_end=None)

    def record(self, patient_id, label, now=None):
        now = datetime.now(timezone.utc).timestamp() if now is None else float(now)
        label = label if label in LABELS else None
        with self.db.connect() as conn:
            # Serialize read/modify/write across the desktop and backend processes.
            conn.execute('BEGIN IMMEDIATE')
            patient = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id = ? AND status = 'ACTIVE'", (patient_id,))
            if not patient:
                return
            row = conn.fetchone('SELECT state_json FROM emotion_monitor_state_v2 WHERE patient_id = ?', (patient_id,))
            if not row and label is None:
                return
            state = json.loads(row['state_json']) if row else self._new_state(now)
            if now < state['updated_at']:
                return
            self._advance(conn, patient_id, state, now)
            state.update(label=label, sample_at=now, accounted_until=now, updated_at=now)
            conn.execute('INSERT INTO emotion_monitor_state_v2 (patient_id, state_json) VALUES (?, ?) '
                         'ON CONFLICT(patient_id) DO UPDATE SET state_json = excluded.state_json',
                         (patient_id, json.dumps(state)))

    def flush_due(self, now=None):
        """Publish finished windows even when the desktop has stopped sending frames."""
        now = datetime.now(timezone.utc).timestamp() if now is None else float(now)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.fetchall('SELECT s.* FROM emotion_monitor_state_v2 s JOIN patients p '
                                 "ON p.patient_id = s.patient_id WHERE p.status = 'ACTIVE'")
            for row in rows:
                state = json.loads(row['state_json'])
                if now < state['start'] + WINDOW_SECONDS:
                    continue
                self._advance(conn, row['patient_id'], state, now)
                state['updated_at'] = now
                conn.execute('UPDATE emotion_monitor_state_v2 SET state_json = ? WHERE patient_id = ?',
                             (json.dumps(state), row['patient_id']))

    def _advance(self, conn, patient_id, state, now):
        while True:
            end = state['start'] + WINDOW_SECONDS
            until = min(now, end)
            covered_end = min(until, state['sample_at'] + MAX_SAMPLE_GAP)
            left = max(state['accounted_until'], state['start'])
            while left < covered_end:
                bucket_end = state['bucket_start'] + SMOOTH_SECONDS
                if left >= bucket_end:
                    self._smooth(conn, patient_id, state, bucket_end, now)
                    state['bucket_start'] = state['start'] + int((left-state['start']) // SMOOTH_SECONDS)*SMOOTH_SECONDS
                    continue
                right = min(covered_end, bucket_end)
                if state['label'] in LABELS:
                    state['bucket'].append([left, right, state['label']])
                left = right
                if left >= bucket_end:
                    self._smooth(conn, patient_id, state, bucket_end, now)
                    state['bucket_start'] = bucket_end
            # Finalize expired buckets even when the camera is absent.
            if until >= state['bucket_start'] + SMOOTH_SECONDS:
                self._smooth(conn, patient_id, state, state['bucket_start'] + SMOOTH_SECONDS, now)
                state['bucket_start'] = state['start'] + int((until-state['start']) // SMOOTH_SECONDS)*SMOOTH_SECONDS
            state['accounted_until'] = max(state['accounted_until'], until)
            d = state['durations']
            if now < end:
                return
            if sum(d.values()) > 0:
                self._publish(conn, patient_id, state, 'window_summary', end, now)
            state.update(start=end, durations=dict.fromkeys(LABELS, 0.0), alerted=False, bucket_start=end, bucket=[])
            state['recent'] = [x for x in state['recent'] if x[1] > end - 1800]
            # Skip empty windows after prolonged downtime; never invent observations.
            if state['sample_at'] + MAX_SAMPLE_GAP <= end and now >= end + WINDOW_SECONDS:
                state['start'] = end + int((now - end) // WINDOW_SECONDS) * WINDOW_SECONDS
                state['bucket_start'] = state['start']

    def _smooth(self, conn, patient_id, state, until, now):
        pieces = state['bucket']
        state['bucket'] = []
        if not pieces:
            state['run_start'] = state['run_end'] = None
            return
        votes = {key: sum(b-a for a,b,label in pieces if label == key) for key in LABELS}
        winner = max(('Neutral', 'Happy', 'Sad'), key=lambda key: votes[key])
        for a, b, _ in pieces:
            state['durations'][winner] += b-a
            if winner == 'Sad':
                if state['run_end'] is None or a > state['run_end'] + 0.001:
                    state['run_start'] = a
                state['run_end'] = b
                if state['recent'] and abs(state['recent'][-1][1]-a) < 0.001:
                    state['recent'][-1][1] = b
                else:
                    state['recent'].append([a,b])
            else:
                state['run_start'] = state['run_end'] = None
        state['recent'] = [x for x in state['recent'] if x[1] > until-1800]
        continuous = (state['run_end']-state['run_start']) if state['run_start'] is not None else 0
        rolling = sum(b-max(a,until-1800) for a,b in state['recent'])
        if not state['alerted'] and (continuous >= 300 or rolling >= 600):
            state['alerted'] = True
            reason = 'sad_continuous' if continuous >= 300 else 'sad_accumulated'
            self._publish(conn, patient_id, state, reason, until, now)

    def _publish(self, conn, patient_id, state, reason, observed_until, now):
        d = dict(state['durations'])
        total = sum(d.values())
        dominant = max(('Neutral', 'Happy', 'Sad'), key=lambda key: d[key])
        label = ('Insufficient' if total < MIN_OBSERVED else
                 dominant if d[dominant] / total >= 0.6 else 'Mixed')
        if reason != 'window_summary':
            label = 'Sad'
        summary_id = 'ems_' + uuid.uuid4().hex
        conn.execute('INSERT INTO emotion_summaries_v2 '
                     '(summary_id, patient_id, window_start, window_end, observed_until, published_at, '
                     'label, dominant_label, reason, durations_json, observed_seconds, sadness_override) '
                     'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (summary_id, patient_id, iso(state['start']), iso(state['start'] + WINDOW_SECONDS),
                      iso(observed_until), iso(now), label, dominant, reason, json.dumps(d), sum(d.values()), 0))
        insert_outbox_event(conn, patient_id, 'emotion.summary_updated', 'emotion_summary',
                            summary_id, occurred_at=iso(now), source='camera',
                            payload_minimal={'summary_id': summary_id, 'reason': reason})

    def list_summaries(self, patient_id, limit=30):
        rows = self.db.fetchall('SELECT * FROM emotion_summaries_v2 WHERE patient_id = ? '
                                "AND reason = 'window_summary' ORDER BY window_start DESC LIMIT ?", (patient_id, limit))
        for row in rows:
            row['durations_seconds'] = json.loads(row.pop('durations_json'))
            row['percentages'] = {k: round(v / row['observed_seconds'] * 100, 1) if row['observed_seconds'] else 0 for k,v in row['durations_seconds'].items()}
        return rows

    def list_alerts(self, patient_id, limit=30):
        return self.db.fetchall("SELECT * FROM emotion_summaries_v2 WHERE patient_id = ? AND reason != 'window_summary' ORDER BY published_at DESC LIMIT ?", (patient_id, limit))
