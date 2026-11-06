"""Assemble the PT and HP portable folders, preserving their existing UI variants."""
import argparse, hashlib, importlib.metadata, json, os, shutil, sys, zipfile
from pathlib import Path
from datetime import datetime,timezone
from patch_mediapipe import patch as patch_mediapipe
ROOT=Path(__file__).resolve().parents[4]
CACHE=ROOT/'runtime/portable-build-cache'
COMMON=('shared/config.py','apps/desktop/main.py','features/face_monitoring/face_adapter.py','features/music_care/cognitive_games/database/seed_data.py')

def sha(path):
 with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()

def assemble(edition,output,version):
 source=ROOT/'releases/setup'/('AI_FOR_EXAM_'+edition+'.zip')
 if not source.exists():
  original=ROOT/'releases'/source.name
  if not original.is_file():raise RuntimeError('Missing UI source archive: '+str(original))
  source.parent.mkdir(exist_ok=True);shutil.copy2(original,source)
 dest=output/edition/'AICare'
 if dest.exists():raise RuntimeError('Refusing to overwrite '+str(dest))
 dest.mkdir(parents=True)
 app=dest/'app';app.mkdir()
 with zipfile.ZipFile(source) as z:
  for info in z.infolist():
   rel=Path(info.filename).relative_to('AICareDemo')
   if info.is_dir() or not rel.parts:continue
   if rel.parts[0] not in {'app','adapters','core','database','web','services','modules','config','models'} and str(rel) not in {'apps/desktop/main.py','shared/config.py'}:continue
   if any(part in {'tests','docs','__pycache__'} for part in rel.parts):continue
   if any('faster_whisper' in part.lower() or 'faster-whisper' in part.lower() or 'ctranslate2' in part.lower() for part in rel.parts):continue
   target=apps/desktop/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(info.filename))
 for rel in COMMON:shutil.copy2(ROOT/rel,apps/desktop/rel)
 # Overlay the current STT implementation instead of the archived Whisper source.
 shutil.copytree(ROOT/'features/voice_assistant/local_voice',app/'features/voice_assistant/local_voice',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
 shutil.copy2(ROOT/'deploy/windows/demo.example.env',app/'deploy/windows/demo.example.env')
 shutil.copy2(ROOT/'models/manifest.json',app/'models/manifest.json')
 shutil.copy2(ROOT/'features/voice_assistant/voice_ai/asr/provider.py',app/'features/voice_assistant/voice_ai/asr/provider.py')
 shutil.copy2(ROOT/'apps/desktop/local_voice_widget.py',app/'apps/desktop/local_voice_widget.py')
 dashboard=app/'apps/desktop/dashboard.py'
 source=dashboard.read_text(encoding='utf-8')
 source=source.replace('if self.current_patient_id != r_id:', 'if str(self.current_patient_id) != str(r_id):')
 source=source.replace('        elif f_status == "unknown":\n            self._invalidate_voice_context()', '        elif f_status == "unknown":\n            if getattr(self, "_previous_voice_face_status", None) != "unknown":\n                self._invalidate_voice_context()')
 source=source.replace('        # 2. Rehabilitation Metrics', '        self._previous_voice_face_status = f_status\n\n        # 2. Rehabilitation Metrics')
 dashboard.write_text(source,encoding='utf-8')
 shutil.copy2(ROOT/'deploy/windows/packaging/windows_portable/portable_entry.py',app/'portable_entry.py')
 shutil.copy2(ROOT/'deploy/windows/packaging/windows_portable/portable_native.py',app/'portable_native.py')
 def copy_runtime(source,target):
  try:os.link(source,target)
  except OSError:shutil.copy2(source,target)
  return target
 shutil.copytree(CACHE/'runtime',dest/'runtime',copy_function=copy_runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.pyo','include','*.lib'))
 # Atomic replacement preserves any older release sharing runtime hard links.
 for original in (CACHE/'vc-runtime').iterdir():
  target=dest/'runtime'/original.name
  temporary=target.with_suffix(target.suffix+'.tmp')
  shutil.copy2(original,temporary);temporary.replace(target)
 if not (dest/'runtime/vc-runtime.json').is_file():raise RuntimeError('App-local CRT missing')
 patch_mediapipe(dest)
 (dest/'qa').mkdir()
 shutil.copy2(ROOT/'deploy/windows/packaging/windows_portable/qa.py',dest/'qa/acceptance.py')
 shutil.copy2(ROOT/'deploy/windows/packaging/windows_portable/security_regression.py',dest/'qa/security_regression.py')
 shutil.copy2(ROOT/'deploy/windows/packaging/windows_portable/clean_machine_test.ps1',dest/'qa/clean_machine_test.ps1')
 shutil.copy2(ROOT/'data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09/test_wavs/0.wav',dest/'qa/speech.wav')
 obsolete=[p for p in (dest/'runtime/Lib/site-packages').iterdir() if any(p.name.lower().startswith(n) for n in ('faster_whisper','ctranslate2'))]
 if obsolete:raise RuntimeError('Obsolete STT packages in build cache: '+str(obsolete))
 # This lightweight legacy pose asset is requested by AI Care's own model_complexity=0.
 lite=Path('Lib/site-packages/mediapipe/modules/pose_landmark/pose_landmark_lite.tflite')
 if not (dest/'runtime'/lite).exists():
  shutil.copy2(ROOT/'.venv'/lite,dest/'runtime'/lite)
 manifest=json.loads((ROOT/'models/manifest.json').read_text(encoding='utf-8'))
 checksums={}
 for model in manifest['models']:
  original=ROOT/model['target_path'];target=apps/desktop/model['target_path']
  if sha(original)!=model['sha256']:raise RuntimeError('Invalid model source: '+str(original))
  target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
  checksums[target.relative_to(dest).as_posix()]=sha(target)
 checksums[(Path('runtime')/lite).as_posix()]=sha(dest/'runtime'/lite)
 (dest/'models').mkdir();(dest/'models/checksums.json').write_text(json.dumps(checksums,indent=2),encoding='utf-8')
 shutil.copy2(CACHE/'AICare.exe',dest/'AICare.exe')
 (dest/'EDITION.txt').write_text(edition,encoding='ascii')
 (dest/'prerequisites').mkdir();shutil.copy2(CACHE/'vc_redist.x64.exe',dest/'prerequisites/vc_redist.x64.exe')
 distributions=list(importlib.metadata.distributions(path=[str(dest/'runtime/Lib/site-packages')]))
 lock={d.metadata['Name']:d.version for d in distributions}
 (dest/'runtime-lock.json').write_text(json.dumps(lock,indent=2,sort_keys=True),encoding='utf-8')
 licenses=dest/'licenses';licenses.mkdir()
 shutil.copy2(dest/'runtime/LICENSE.txt',licenses/'Python-LICENSE.txt')
 for dist in distributions:
  dist_path=Path(dist._path);label=dist.metadata['Name']
  for p in dist_path.rglob('*'):
   if p.is_file() and ('license' in p.name.lower() or 'copying' in p.name.lower() or 'notice' in p.name.lower()):
    t=licenses/label/p.relative_to(dist_path);t.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,t)
  (licenses/(label+'-METADATA.txt')).write_text(dist.read_text('METADATA') or '',encoding='utf-8')
 shutil.copy2(ROOT/'models/manifest.json',licenses/'AI-model-sources.json')
 version_info={'product':'AI Care & Rehabilitation Assistant','version':version,'edition':edition,'platform':'Windows 10/11 x64','python':'3.12.10 embeddable AMD64','build_utc':datetime.now(timezone.utc).isoformat(),'sources':json.loads((CACHE/'sources.json').read_text(encoding='utf-8-sig')),'data_directory':'%LOCALAPPDATA%/AICareDemo/'+edition,'models':'Existing AI Care face/emotion/pose models and Vietnamese Zipformer 30M INT8 CPU'}
 (dest/'VERSION.json').write_text(json.dumps(version_info,indent=2),encoding='utf-8')
 text=f'''AI CARE — PORTABLE {edition}\nWindows 10/11 64-bit | {version}\n\n1. Giải nén toàn bộ ZIP vào thư mục có quyền đọc/chạy.\n2. Chạy AICare.exe. Không cần deploy/windows/Setup.cmd, Python hay tải model.\n3. Launcher mở ứng dụng người cao tuổi và cổng web khi model đã sẵn sàng.\n4. Đóng launcher để dừng an toàn; không chỉ sao chép riêng EXE.\n\nDữ liệu mới: %LOCALAPPDATA%\\AICareDemo\\{edition} (có nút mở dữ liệu/log).\nDừng ứng dụng trước khi sao lưu toàn bộ thư mục dữ liệu. Hai giao diện dùng thư mục dữ liệu riêng.\nKhông kèm hồ sơ, ảnh mặt, nhạc người dùng hay lịch sử của máy phát triển. CSDL mới có dữ liệu mẫu theo migration hiện có của AI Care.\n\nCho phép camera/micro trong Windows. Cần loa/tai nghe để nghe nhạc.\nVC++ runtime được đóng gói cạnh Python; không cần cài prerequisite hay quyền quản trị. Nếu báo thiếu/hỏng DLL, giải nén lại toàn bộ bản ZIP đã sửa. Cần Windows 10/11 x64 và trình duyệt mặc định của Windows.\nTính năng email/cloud tùy chọn cần mạng và cấu hình riêng; các luồng chính dùng model cục bộ.\n\nĐăng nhập quản trị mặc định: admin / admin123.\nĐăng ký hồ sơ mới trên ứng dụng desktop.\n\nXem QA_REPORT.txt để biết các kiểm tra thực tế và giới hạn; chưa kiểm chứng trên Windows sạch.\n'''
 (dest/'HUONG_DAN.txt').write_text(text,encoding='utf-8-sig')
 (dest/'CHANGELOG.txt').write_text('Đóng gói runtime riêng, launcher, kiểm tra model và tách dữ liệu. Giữ UI '+edition+' và các luồng nghiệp vụ hiện có.\n',encoding='utf-8-sig')
 print('ASSEMBLED',dest,flush=True)
 return dest

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--edition',choices=['PT','HP','both'],default='both');p.add_argument('--output',default='releases/portable-nativefix');p.add_argument('--version',default='6.0.0-nativefix.2');args=p.parse_args()
 for edition in ['PT','HP'] if args.edition=='both' else [args.edition]:assemble(edition,(ROOT/args.output).resolve(),args.version)
