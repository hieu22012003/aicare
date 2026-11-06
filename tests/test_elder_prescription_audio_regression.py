import io, threading, wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pytest
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from features.medication.medication_schedule_sync import ensure_medication_occurrences

@pytest.fixture
def care(tmp_path):
    db=UnifiedDatabaseManager(tmp_path/'care.sqlite3')
    Migrator(db).apply_all()
    auth=AuthService(db)
    return db, ApiRouter(db,auth)

def payload(start='2026-10-23',end='2026-10-30'):
    return {'start_date':start,'end_date':end,'items':[{'medication_id':'med_001','dose_amount':1,'dose_unit':'viên','instructions':'Sau ăn sáng','times_of_day':['08:00','20:00']}]}

def test_invalid_date_never_commits_prescription_or_schedule(care):
    db,router=care
    before=db.fetchone('SELECT count(*) AS n FROM prescriptions')['n']
    status,result=router._create_prescription('patient_001','user_doc_001',payload(end='2026-10-18'))
    assert status==400 and 'kết thúc' in result['error']
    assert db.fetchone('SELECT count(*) AS n FROM prescriptions')['n']==before

def test_bad_times_never_create_empty_schedule(care):
    db,router=care
    data=payload(); data['items'][0]['times_of_day']=['abc']
    assert router._create_prescription('patient_001','user_doc_001',data)[0]==400

def test_rolling_schedule_repairs_missing_today_and_stays_bounded(care,tmp_path):
    db,router=care
    assert router._create_prescription('patient_001','user_doc_001',payload())[0]==201
    with db.connect() as c: c.execute('DELETE FROM dose_occurrences')
    now=datetime(2026,10,24,tzinfo=timezone.utc)
    assert ensure_medication_occurrences(db,'patient_001',now)==14
    assert ensure_medication_occurrences(db,'patient_001',now)==0
    assert db.fetchone("SELECT count(*) AS n FROM dose_occurrences WHERE local_scheduled_time LIKE '2026-10-24%'")['n']==2
    assert ensure_medication_occurrences(db,'patient_002',now)==0
    with db.connect() as c: c.execute("UPDATE prescriptions SET status='CANCELLED'")
    assert ensure_medication_occurrences(db,'patient_001',now+timedelta(days=1))==0

def test_invalid_existing_prescription_visible_to_elder(care,tmp_path):
    from PyQt5.QtWidgets import QApplication,QLabel
    from apps.desktop.today_todo_widget import TodayTodoWidget
    from shared.services.elder_sync_client import ElderSyncClient
    db,router=care
    assert router._create_prescription('patient_001','user_doc_001',payload())[0]==201
    with db.connect() as c:
        c.execute("UPDATE prescription_items SET end_date='2026-10-18'")
        c.execute("UPDATE prescriptions SET valid_to='2026-10-18'")
        c.execute('DELETE FROM dose_occurrences')
    app=QApplication.instance() or QApplication([])
    widget=TodayTodoWidget(db,sync_client=ElderSyncClient(db,queue_dir=tmp_path/'queue'))
    try:
        text='\n'.join(label.text() for label in widget.prescription_card.findChildren(QLabel))
        assert 'Amlor' in text and 'Cần bác sĩ sửa đơn' in text
        assert db.fetchone('SELECT count(*) AS n FROM dose_occurrences')['n']==0
    finally:
        widget._refresh_timer.stop();widget.close()

@pytest.mark.parametrize('kind',['wav','mp3'])
def test_real_decoder_outputs_audible_pcm_without_windows_codec(tmp_path,kind):
    from PyQt5.QtCore import QCoreApplication
    from features.music_care.music_playback import PcmMusicPlayer
    import av
    path=tmp_path/('tone.'+kind)
    rate=48000
    samples=np.asarray(np.sin(np.arange(4800)*2*np.pi*440/rate)*0.25,dtype=np.float32)
    if kind=='wav':
        with wave.open(str(path),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes((samples*32767).astype('<i2').tobytes())
    else:
        with av.open(str(path),'w') as c:
            stream=c.add_stream('libmp3lame',rate=rate)
            frame=av.AudioFrame.from_ndarray(samples.reshape(1,-1),format='flt',layout='mono');frame.sample_rate=rate
            for packet in stream.encode(frame): c.mux(packet)
            for packet in stream.encode(None): c.mux(packet)
    blocks=[]
    class Output:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def write(self,data):blocks.append(data.copy())
    app=QCoreApplication.instance() or QCoreApplication([])
    player=PcmMusicPlayer(output_factory=Output)
    failed=[]; player.failed.connect(failed.append)
    resume=threading.Event();resume.set()
    player._run(player._generation,str(path),threading.Event(),resume)
    assert not failed,failed
    assert blocks and sum(len(x) for x in blocks)>4000
    assert max(float(np.max(np.abs(x))) for x in blocks)>0.01
    assert all(x.shape[1]==2 for x in blocks)

def test_missing_audio_reports_failure_instead_of_silent_playing(tmp_path):
    from PyQt5.QtCore import QCoreApplication
    from features.music_care.music_playback import PcmMusicPlayer
    app=QCoreApplication.instance() or QCoreApplication([])
    player=PcmMusicPlayer(output_factory=lambda **kw:None)
    errors=[];player.failed.connect(errors.append)
    resume=threading.Event();resume.set()
    player._run(player._generation,str(tmp_path/'missing.mp3'),threading.Event(),resume)
    assert errors
