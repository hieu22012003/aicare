"""Portable entry point for the existing AI Care web and Qt applications."""
from __future__ import annotations
import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

APP = Path(__file__).resolve().parent
PACKAGE = APP.parent
DLL_HANDLES = []
sys.dont_write_bytecode = True
from portable_native import bootstrap
bootstrap()


def configure(home):
    home.mkdir(parents=True, exist_ok=True)
    for name in ('logs','cache/matplotlib','launcher'):
        (home/name).mkdir(parents=True, exist_ok=True)
    os.environ.update(AI_CARE_HOME=str(home), AI_CARE_DB_PATH=str(home/'data/unified_care.sqlite3'),
        MPLCONFIGDIR=str(home/'cache/matplotlib'), PORTABLE_MODE='0', AI_CARE_USE_LOCALAPPDATA='0',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2',
        PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION='python')
    os.environ.pop('AI_CARE_TEST_MODE',None)
    os.environ.pop('AI_CARE_TEST_DATA_DIR',None)
    for stream in (sys.stdout,sys.stderr):
        if stream and hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8',errors='replace')
    site = PACKAGE/'runtime/Lib/site-packages'
    for rel in ('pywin32_system32','win32','win32/lib'):
        path=site/rel
        if path.is_dir():
            sys.path.append(str(path))
            if hasattr(os,'add_dll_directory') and rel!='win32/lib': DLL_HANDLES.append(os.add_dll_directory(str(path)))
    env_file=home/'.env'
    if not env_file.exists():
        template=(APP/'deploy/windows/demo.example.env').read_text(encoding='utf-8-sig')
        env_file.write_text(template,encoding='utf-8')
    # Migrate existing portable installations to the supported STT runtime.
    env_text=env_file.read_text(encoding='utf-8-sig')
    if 'faster-whisper' in env_text or 'faster_whisper' in env_text:
        import re
        for key,value in {'VOICE_MODEL_PATH':'data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09','VOICE_DEVICE':'cpu','VOICE_COMPUTE_TYPE':'int8','VOICE_BEAM_SIZE':'1'}.items():
            env_text=re.sub(r'^'+key+r'=.*$',key+'='+value,env_text,flags=re.M)
        env_file.write_text(env_text,encoding='utf-8')
    os.chdir(APP)
    return home


def verify_models():
    manifest=json.loads((PACKAGE/'models/checksums.json').read_text(encoding='utf-8'))
    for name,expected in manifest.items():
        path=PACKAGE/name
        if not path.is_file(): raise RuntimeError('Missing model: '+name)
        with path.open('rb') as handle:
            actual=hashlib.file_digest(handle,'sha256').hexdigest()
        if not hmac.compare_digest(actual,expected): raise RuntimeError('Model checksum mismatch: '+name)
    return len(manifest)


def warmup():
    # Keep the application's native import order on Windows.
    import mediapipe
    import torch
    import cv2
    import numpy as np
    pb=APP/'features/face_monitoring/standalone/models/opencv_face_detector_uint8.pb'
    txt=APP/'features/face_monitoring/standalone/models/opencv_face_detector.pbtxt'
    net=cv2.dnn.readNetFromTensorflow(np.frombuffer(pb.read_bytes(),np.uint8),np.frombuffer(txt.read_bytes(),np.uint8))
    net.setInput(cv2.dnn.blobFromImage(np.zeros((300,300,3),np.uint8),size=(300,300)))
    assert net.forward().size>0
    from features.emotion.emotion_adapter import EmotionAdapter
    emotion=EmotionAdapter()
    if emotion._model is None: raise RuntimeError('Emotion checkpoint did not load')
    if not any(getattr(emotion,attr,None) for attr in ('_face_mesh','_face_landmarker')):
        raise RuntimeError('Emotion MediaPipe graph did not load')
    emotion.process_frame(np.zeros((480,640,3),np.uint8))
    for attr in ('_face_mesh','_face_landmarker'):
        component=getattr(emotion,attr,None)
        if component: component.close()
    pose=mediapipe.solutions.pose.Pose(static_image_mode=True,model_complexity=0)
    pose.process(np.zeros((480,640,3),np.uint8));pose.close()
    from features.voice_assistant.local_voice.config import VoiceConfig
    from features.voice_assistant.local_voice.engine import LocalSttEngine
    engine=LocalSttEngine(VoiceConfig.from_env())
    speech=engine.transcribe((PACKAGE/'qa/speech.wav').read_bytes())
    if speech['status']!='completed' or not speech['transcript']:
        raise RuntimeError('STT decoding warmup failed')
    return {'face':'loaded','emotion':'loaded','pose':'loaded','stt':'loaded','numpy':np.__version__}


def desktop(args,home):
    os.environ['AI_CARE_SHUTDOWN_FILE']=str(args.shutdown_file)
    if args.desktop_test:
        os.environ.update(AI_CARE_TEST_MODE='1',AI_CARE_TEST_DATA_DIR=str(home/'desktop-qa'),VOICE_AI_ENABLED='false',QT_QPA_PLATFORM='offscreen')
        sys.argv=[str(APP/'apps/desktop/main.py'),'--test']
        import main
        from shared.camera.camera_manager import CameraManager
        CameraManager.start_camera=lambda self: None
        return main.main()
    import runpy
    sys.argv=[str(APP/'apps/desktop/main.py')]
    runpy.run_path(str(APP/'apps/desktop/main.py'),run_name='__main__')
    return 0


def supervise(args,home):
    if not args.token or not args.ready_file: raise ValueError('Launcher arguments missing')
    shutdown=threading.Event()
    token=args.token
    verify_models()
    model_status=warmup()
    from shared.database.connection import UnifiedDatabaseManager
    from shared.database.migrator import Migrator
    from shared.database.importer import DataImporter
    db=UnifiedDatabaseManager();Migrator(db).apply_all();DataImporter(db).import_all(seed_sample_data=False)
    import features.web_portal.server as server_module
    base_handler=server_module.HealthcareHttpHandler
    class PortableHandler(base_handler):
        def _authorized(self):
            return hmac.compare_digest(self.headers.get('X-AICare-Launcher',''),token)
        def do_GET(self):
            if self.path=='/_portable/health':
                if not self._authorized(): self._send_json(403,{'error':'forbidden'});return
                self._send_json(200,{'ready':True,'models':model_status,'pid':os.getpid()});return
            super().do_GET()
        def do_POST(self):
            if self.path=='/_portable/shutdown':
                if not self._authorized(): self._send_json(403,{'error':'forbidden'});return
                self._send_json(200,{'status':'stopping'});shutdown.set();return
            super().do_POST()
    server_module.HealthcareHttpHandler=PortableHandler
    server=server_module.HealthcareWebServer(db,host='127.0.0.1',port=args.port)
    server.start()
    port=server.httpd.server_port
    os.environ["WEB_PORT"]=str(port)
    os.environ["VOICE_INTENT_BACKEND_URL"]=f"http://127.0.0.1:{port}"
    signal=home/'launcher'/('shutdown-'+secrets.token_hex(8)+'.flag')
    process=None
    desktop_log=None
    ready=Path(args.ready_file)
    try:
        if not args.no_desktop:
            desktop_log=(home/'logs/desktop.log').open('a',encoding='utf-8')
            process=subprocess.Popen([sys.executable,'-I',str(APP/'portable_entry.py'),'--desktop','--home',str(home),'--shutdown-file',str(signal)],cwd=APP,stdout=desktop_log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        state={'url':f'http://127.0.0.1:{port}','pid':os.getpid(),'home':str(home)}
        temp=ready.with_suffix('.tmp');temp.write_text(json.dumps(state),encoding='utf-8');temp.replace(ready)
        while not shutdown.wait(.25):
            if process is not None and process.poll() is not None: break
    finally:
        signal.touch()
        if process:
            try: process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                import psutil
                try:
                    parent=psutil.Process(process.pid)
                    for child in parent.children(recursive=True): child.terminate()
                    parent.terminate()
                    process.wait(timeout=5)
                except (psutil.NoSuchProcess,subprocess.TimeoutExpired):
                    if process.poll() is None: process.kill()
        server.stop()
        if desktop_log: desktop_log.close()
        ready.unlink(missing_ok=True);signal.unlink(missing_ok=True)
    return 0


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',type=Path,required=True)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--token')
    parser.add_argument('--ready-file')
    parser.add_argument('--no-desktop',action='store_true')
    parser.add_argument('--desktop',action='store_true')
    parser.add_argument('--desktop-test',action='store_true')
    parser.add_argument('--shutdown-file',default='')
    args=parser.parse_args();home=configure(args.home.resolve())
    return desktop(args,home) if args.desktop or args.desktop_test else supervise(args,home)

if __name__=='__main__':
    try: sys.exit(main())
    except Exception:
        logging.exception('Portable startup failed')
        sys.exit(1)
