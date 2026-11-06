import copy
from datetime import datetime,timedelta,timezone
from test_desktop import desktop,utterance,wait
from test_requests import setup
from shared.services.elder_sync_client import ElderSyncClient
from features.voice_assistant.transcript_intent.context import read_state


def bind(desktop,tmp_path):
    c,ctx,e,m,t,db=desktop
    stamp=datetime.now(timezone.utc)
    with db.connect() as conn:
        conn.execute("INSERT INTO medications(medication_id,brand_name,product_strength) VALUES ('m','Thuốc demo','1')")
        conn.execute("INSERT INTO prescriptions(prescription_id,patient_id,entered_by_user_id,prescribed_date,valid_from) VALUES ('rx','p1','d','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO prescription_items(prescription_item_id,prescription_id,medication_id,dose_amount,original_instructions,start_date) VALUES ('pi','rx','m',1,'Theo đơn','2026-01-01')")
        conn.execute("INSERT INTO medication_schedules(schedule_id,prescription_item_id,revision,local_times,days_of_week,effective_from,reminder_delay_minutes) VALUES ('sc','pi',1,'[]','[]','2026-01-01',12)")
        conn.execute('''INSERT INTO dose_occurrences(occurrence_id,schedule_id,revision,scheduled_at_utc,local_scheduled_time,grace_deadline_utc,state,medication_name_snapshot,dose_snapshot,instructions_snapshot,patient_id) VALUES ('dose','sc',1,?,?,?,'REMINDING','Thuốc demo','1 viên','Theo đơn','p1')''',((stamp-timedelta(minutes=1)).isoformat(),stamp.isoformat(),(stamp+timedelta(hours=1)).isoformat()))
    ctx['doses']=read_state(db,'p1')['doses']
    client=ElderSyncClient(db,patient_id='p1',queue_dir=tmp_path/'queue')
    c.dispatcher.window.today_todo_widget.sync_client=client
    def refresh():ctx['doses']=read_state(db,'p1')['doses'];e.append('refresh')
    c.dispatcher.window.today_todo_widget.refresh_data=refresh
    return c,ctx,e,m,t,db,client


def test_taken_only_after_approval_exact_occurrence(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path)
    utterance(c,'Tôi uống rồi')
    assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='REMINDING'
    c.confirm();wait(c)
    assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='CONFIRMED'
    event=db.fetchone('SELECT * FROM dose_events');assert 'voice_self_report' in event['metadata']
    assert 'refresh' in e;assert not t.calls
    utterance(c,'Tôi uống rồi','u1')
    assert len(db.fetchall('SELECT * FROM dose_events'))==1
    assert db.fetchone('SELECT version FROM prescriptions')['version']==1


def test_not_taken_preserves_state_and_records_response(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path);utterance(c,'Tôi chưa uống thuốc')
    assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='REMINDING'
    assert not db.fetchall('SELECT * FROM dose_events')
    assert 'not_taken' in db.fetchone("SELECT payload_minimal FROM outbox_events WHERE source='voice'")['payload_minimal']
    assert not t.calls


def test_no_dose_multiple_doses_expired_context_no_mutation(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path)
    snapshot=copy.deepcopy(ctx)
    ctx['doses']=[];utterance(c,'Tôi uống rồi');assert not c.dialogue.pending
    ctx['doses']=snapshot['doses']*2;utterance(c,'Tôi uống rồi','u2');assert not c.dialogue.pending
    ctx['doses']=snapshot['doses'];utterance(c,'Tôi uống rồi','u3')
    db.execute("UPDATE prescriptions SET version=2")
    ctx['doses']=read_state(db,'p1')['doses'];c.confirm();wait(c)
    assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='REMINDING'
    assert not db.fetchall('SELECT * FROM dose_events')


def test_snooze_existing_policy_and_cap(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path)
    for i in range(3):utterance(c,'Nhắc lại sau',str(i))
    row=db.fetchone('SELECT * FROM dose_occurrences');assert row['state']=='SNOOZED';assert row['snooze_count']==3
    interval=datetime.fromisoformat(row['snoozed_until_utc'])-datetime.fromisoformat(row['action_taken_at_utc'])
    assert interval.total_seconds()==12*60
    utterance(c,'Nhắc lại sau','four');assert db.fetchone('SELECT snooze_count FROM dose_occurrences')['snooze_count']==3
    assert len(db.fetchall('SELECT * FROM dose_events'))==3


def test_offline_medication_never_replays(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path);client.is_online=False
    utterance(c,'Tôi uống rồi');c.confirm();wait(c)
    assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='REMINDING'
    assert not client._load_queue()
    client.set_online(True);assert not db.fetchall('SELECT * FROM dose_events')


def test_medication_and_contact_both_execute_after_approval(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path);utterance(c,'Tôi uống rồi, gọi con')
    assert not t.calls;assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='REMINDING'
    c.confirm();wait(c)
    assert db.fetchone('SELECT state FROM dose_occurrences')['state']=='CONFIRMED'
    assert len(t.calls)==1;assert db.fetchone('SELECT intent FROM voice_requests')['intent']=='caregiver_contact'


def test_spoken_ten_minutes_only_changes_occurrence(desktop,tmp_path):
    c,ctx,e,m,t,db,client=bind(desktop,tmp_path);utterance(c,'Mười phút nữa nhắc lại')
    row=db.fetchone('SELECT * FROM dose_occurrences')
    interval=datetime.fromisoformat(row['snoozed_until_utc'])-datetime.fromisoformat(row['action_taken_at_utc'])
    assert interval.total_seconds()==600
    assert db.fetchone('SELECT reminder_delay_minutes FROM medication_schedules')['reminder_delay_minutes']==12
    assert db.fetchone('SELECT version FROM prescriptions')['version']==1
