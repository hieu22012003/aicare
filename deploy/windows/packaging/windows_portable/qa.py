"""Portable acceptance checks using the bundled interpreter and real launcher."""
import argparse,json,os,socket,subprocess,time,urllib.request,urllib.error,uuid
import hashlib,platform
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]

def run_qa(package,home=None,report=None):
 package=package.resolve();edition=(package/'EDITION.txt').read_text().strip()
 home=(home or ROOT/'runtime/portable-qa'/(edition+'-'+uuid.uuid4().hex[:8])).resolve();home.mkdir(parents=True,exist_ok=True)
 env=os.environ.copy();env.update(PYTHONHOME='Z:/invalid-python-home',PYTHONPATH='Z:/invalid-python-path',AI_CARE_DATA_HOME=str(home),PYTHONUTF8='1')
 (home/'temp').mkdir(exist_ok=True)
 env.update(TEMP=str(home/'temp'),TMP=str(home/'temp'),MPLCONFIGDIR=str(home/'matplotlib'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
 windows=os.environ['SystemRoot']
 env['PATH']=os.pathsep.join([str(package/'runtime'),str(Path(windows)/'System32'),windows])
 for key in ('QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QML2_IMPORT_PATH','VIRTUAL_ENV','CONDA_PREFIX'):
  env.pop(key,None)
 python=package/'runtime/python.exe';checks={}
 def run(args,timeout=240):
  print('QA',edition,Path(args[0]).name,args[2][:80] if len(args)>2 else '',flush=True)
  p=subprocess.Popen(args,cwd=package,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  try:
   output,_=p.communicate(timeout=timeout)
  except subprocess.TimeoutExpired:
   # Killing only the launcher leaves its backend holding the stdout pipe open.
   if os.name=='nt':subprocess.run(['taskkill.exe','/PID',str(p.pid),'/T','/F'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   else:p.kill()
   try:p.communicate(timeout=10)
   except subprocess.TimeoutExpired:pass
   raise RuntimeError('QA timeout: '+str(args[0]))
  if p.returncode:raise RuntimeError(str(args[0])+' exit '+str(p.returncode)+'\n'+output[-7000:])
  return output
 checks['security_regression']=run([str(python),'-I',str(package/'qa/security_regression.py')]).strip()
 checks['isolated_runtime']=run([str(python),'-I','-c',"import portable_entry; import sys,mediapipe,torch,cv2,numpy,av,sounddevice,onnxruntime; from PyQt5 import QtCore; assert all('.venv' not in p for p in sys.path); from portable_native import audit_loaded; import json; print(json.dumps(audit_loaded()))"]).strip()
 for order in ('mediapipe,torch,cv2','torch,mediapipe,cv2','cv2,mediapipe,torch'):
  checks['native_order_'+order]=run([str(python),'-I','-c',"import portable_entry; import "+order+"; from portable_native import audit_loaded; import json; print(json.dumps(audit_loaded()))"]).strip()
 # Also test a fresh direct import before the application bootstrap.
 checks['direct_mediapipe_app_local_crt']=run([str(python),'-I','-c',"import mediapipe; from portable_native import audit_loaded; import json; print(json.dumps(audit_loaded()))"]).strip()
 sample=package/'qa/speech.wav'
 code="import importlib.util; assert importlib.util.find_spec('faster_whisper') is None; assert importlib.util.find_spec('ctranslate2') is None; import portable_entry; from features.voice_assistant.local_voice.config import VoiceConfig; from features.voice_assistant.local_voice.engine import LocalSttEngine; e=LocalSttEngine(VoiceConfig(str(portable_entry.APP/'data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09'))); r=e.transcribe(open("+repr(str(sample))+",'rb').read()); assert 'HỖ TRỢ' in r['transcript']; import json; print(json.dumps(r,ensure_ascii=True))"
 checks['zipformer_real_speech_and_no_whisper']=run([str(python),'-I','-c',code]).strip()
 run([str(package/'AICare.exe'),'--self-test'])
 self_report=json.loads((home/'logs/launcher-self-test.json').read_text(encoding='utf-8-sig'))
 assert self_report['passed'];checks['launcher_self_test']=self_report
 run([str(python),'-I',str(package/'apps/desktop/portable_entry.py'),'--desktop-test','--home',str(home/'desktop')])
 checks['qt_desktop_offscreen']='passed: constructed dashboard and updated UI, camera disabled'
 # Provision only the disposable QA database; never ship a default credential.
 qa_password=uuid.uuid4().hex
 qa_database=home/'api/data/unified_care.sqlite3'
 code="import portable_entry; from shared.database.connection import UnifiedDatabaseManager; from shared.database.migrator import Migrator; from shared.services.credential_security import hash_password; d=UnifiedDatabaseManager("+repr(str(qa_database))+"); Migrator(d).apply_all(); d.execute(\"UPDATE users SET password_hash=? WHERE user_id='user_admin_001'\",(hash_password("+repr(qa_password)+"),))"
 run([str(python),'-I','-c',code])
 token=uuid.uuid4().hex; ready=home/'api-ready.json';ready.unlink(missing_ok=True)
 log=(home/'logs/api-qa.log').open('w',encoding='utf-8')
 process=subprocess.Popen([str(python),'-I',str(package/'apps/desktop/portable_entry.py'),'--no-desktop','--home',str(home/'api'),'--token',token,'--ready-file',str(ready),'--port','0'],cwd=package,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 try:
  for _ in range(720):
   if ready.exists():break
   if process.poll() is not None:raise RuntimeError('API startup failed; see '+str(home/'logs/api-qa.log'))
   time.sleep(.25)
  assert ready.exists(),'API ready timeout'
  base=json.loads(ready.read_text())['url']
  def fetch(path,method='GET',payload=None,headers=None):
   req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,method=method,headers=headers or {})
   with urllib.request.urlopen(req,timeout=15) as r:return r.status,r.read(),r.headers
  for path in ('/health','/login','/caregiver','/clinician','/admin'):
   assert fetch(path)[0]==200
  if edition=='PT': assert 'text/css' in fetch('/assets/portal-theme.css')[2]['Content-Type']
  result=json.loads(fetch('/api/auth/login','POST',{'email':'admin','password':qa_password},{'Content-Type':'application/json'})[1]);assert result['token']
  try:fetch('/api/auth/quick-accounts');raise AssertionError('Account discovery was exposed')
  except urllib.error.HTTPError as exc:assert exc.code==404
  checks['no_public_account_discovery']='passed: quick accounts removed'
  status,body,_=fetch('/_portable/health',headers={'X-AICare-Launcher':token});assert json.loads(body)['ready']
  for path in ('/_portable/health','/_portable/shutdown'):
   try:fetch(path,'GET' if path.endswith('health') else 'POST');raise AssertionError('Control accepted no token')
   except urllib.error.HTTPError as exc:assert exc.code==403
  fetch('/_portable/shutdown','POST',headers={'X-AICare-Launcher':token})
  assert process.wait(timeout=40)==0
  checks['http_auth_and_shutdown']='passed: health, 4 pages, admin login, token control rejection and graceful exit'
 finally:
  if process.poll() is None:process.terminate();process.wait(timeout=15)
  log.close()
 # Test the real verifier using a disposable manifest; package may be read-only.
 checksum=package/'models/checksums.json'
 value=json.loads(checksum.read_bytes());first=next(iter(value))
 tampered=home/'tampered';(tampered/'models').mkdir(parents=True,exist_ok=True)
 (tampered/'models/checksums.json').write_text(json.dumps({str(package/first):'0'*64}),encoding='utf-8')
 code='import portable_entry; from pathlib import Path; portable_entry.PACKAGE=Path('+repr(str(tampered))+'); portable_entry.verify_models()'
 probe=subprocess.run([str(python),'-I','-c',code],cwd=package,env=env,capture_output=True,timeout=30)
 assert probe.returncode!=0 and b'checksum mismatch' in probe.stderr
 checks['tampered_model_manifest']='rejected'
 checks['app_local_crt_manifest']=json.loads((package/'runtime/vc-runtime.json').read_text(encoding='utf-8'))
 checks['path_isolation']=env['PATH']
 checks['python_environment_isolation']='PYTHONHOME/PYTHONPATH intentionally invalid; bundled interpreter and modules passed'
 checks['limits']='Build-host Windows only; physical camera/microphone, second clean Windows machine and long-duration use not tested.'
 checks['tested_utc']=datetime.now(timezone.utc).isoformat()
 checks['tested_os']=platform.platform()
 checks['qa_inputs']={rel:hashlib.sha256((package/rel).read_bytes()).hexdigest() for rel in ('apps/desktop/web/server.py','apps/desktop/services/patient_sync_service.py','qa/security_regression.py','AICare.exe','apps/desktop/portable_entry.py','apps/desktop/portable_native.py','apps/desktop/services/local_voice/engine.py','runtime/Lib/site-packages/mediapipe/python/solution_base.py','PORTABLE_COMPAT.json','runtime/vc-runtime.json','models/checksums.json','qa/acceptance.py','qa/speech.wav')}
 (report or package/'QA_REPORT.txt').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'edition':edition,'passed':True,'report':str(report or package/'QA_REPORT.txt'),'checks':list(checks)},ensure_ascii=False,indent=2),flush=True)
 return checks

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('package',type=Path);p.add_argument('--home',type=Path);p.add_argument('--report',type=Path);args=p.parse_args();run_qa(args.package,args.home,args.report)
