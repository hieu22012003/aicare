"""Materialize rolling dose dates from active, valid prescription schedules."""
import json
import uuid
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from shared.database.outbox_helper import insert_outbox_event


def ensure_medication_occurrences(db, patient_id=None, now=None, days=7):
    now = now or datetime.now(timezone.utc)
    created = 0
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        rows = conn.fetchall(
            "SELECT s.*, pi.start_date, pi.end_date, pi.dose_amount, pi.dose_unit, "
            "pi.original_instructions, p.patient_id, p.valid_from, p.valid_to, m.brand_name "
            "FROM medication_schedules s JOIN prescription_items pi ON pi.prescription_item_id=s.prescription_item_id "
            "JOIN prescriptions p ON p.prescription_id=pi.prescription_id "
            "JOIN medications m ON m.medication_id=pi.medication_id "
            "WHERE s.is_active=1 AND pi.status='ACTIVE' AND p.status='ACTIVE' "
            "AND (? IS NULL OR p.patient_id=?)", (patient_id,patient_id))
        for row in rows:
            zone=ZoneInfo(row['timezone'])
            today=now.astimezone(zone).date()
            start=str(row['start_date'] or row['valid_from'] or '')[:10]
            end=str(row['end_date'] or row['valid_to'] or '')[:10]
            times=json.loads(row['local_times'])
            weekdays=json.loads(row['days_of_week'])
            for offset in range(days+1):
                day=today+timedelta(days=offset)
                if (start and day.isoformat()<start) or (end and day.isoformat()>end) or day.weekday() not in weekdays:
                    continue
                for value in times:
                    scheduled=datetime.combine(day,time.fromisoformat(value),tzinfo=zone)
                    utc=scheduled.astimezone(timezone.utc)
                    bounds=(row['effective_from'],row['effective_to'])
                    def instant(value):
                        result=datetime.fromisoformat(value)
                        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
                    if bounds[0] and utc<instant(bounds[0]): continue
                    if bounds[1] and utc>=instant(bounds[1]): continue
                    local=scheduled.replace(tzinfo=None).isoformat()
                    existing=conn.fetchone('SELECT occurrence_id FROM dose_occurrences WHERE schedule_id=? AND revision=? AND substr(local_scheduled_time,1,16)=?',(row['schedule_id'],row['revision'],local[:16]))
                    if existing: continue
                    oid='occ_'+uuid.uuid4().hex[:16]
                    conn.execute('INSERT INTO dose_occurrences '
                        '(occurrence_id,schedule_id,revision,scheduled_at_utc,local_scheduled_time,grace_deadline_utc,state,medication_name_snapshot,dose_snapshot,instructions_snapshot,patient_id) '
                        "VALUES (?,?,?,?,?,?,'PENDING',?,?,?,?)",
                        (oid,row['schedule_id'],row['revision'],utc.isoformat(),local,
                         (utc+timedelta(minutes=row['grace_period_minutes'])).isoformat(),row['brand_name'],
                         f"{row['dose_amount']:g} {row['dose_unit']}",row['original_instructions'],row['patient_id']))
                    insert_outbox_event(conn,row['patient_id'],'medication.dose_changed','dose_occurrence',oid,
                                        source='scheduler',payload_minimal={'occurrence_id':oid,'state':'PENDING'})
                    created+=1
    return created
