"""Archive only successfully checked portable builds; never include runtime user data."""
import argparse,copy,hashlib,json,os,zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[4]

def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def archive(package, output_dir=None):
 package=package.resolve();edition=(package/'EDITION.txt').read_text().strip()
 if not (package/'QA_REPORT.txt').is_file():raise RuntimeError('QA report missing')
 checks=json.loads((package/'QA_REPORT.txt').read_text(encoding='utf-8'))
 if not checks.get('launcher_self_test',{}).get('passed') or not checks.get('direct_mediapipe_app_local_crt') or not checks.get('app_local_crt_manifest'):
  raise RuntimeError('Current native portability QA required; old build-host QA is insufficient')
 if checks['app_local_crt_manifest']!=json.loads((package/'runtime/vc-runtime.json').read_text(encoding='utf-8')):
  raise RuntimeError('CRT changed since QA')
 if not checks.get('qa_inputs'):raise RuntimeError('QA input fingerprints missing')
 for rel,expected in checks['qa_inputs'].items():
  if sha(package/rel)!=expected:raise RuntimeError('Changed since QA: '+rel)
 for name,expected in checks['app_local_crt_manifest']['files'].items():
  if sha(package/'runtime'/name)!=expected:raise RuntimeError('CRT changed since QA: '+name)
 for rel,expected in json.loads((package/'models/checksums.json').read_text(encoding='utf-8')).items():
  if sha(package/rel)!=expected:raise RuntimeError('Model changed since QA: '+rel)
 forbidden=[];files=[]
 for parent,dirs,names in os.walk(package):
  dirs[:]=[d for d in dirs if d!='__pycache__']
  for name in names:
   p=Path(parent)/name;rel=p.relative_to(package)
   if p.suffix.lower() in {'.pyc','.pyo'}:continue
   if any(x in rel.parts for x in ('.venv','registered_faces','unknown_faces','backups')) or p.suffix.lower() in {'.sqlite3','.db','.log'} or p.name=='.env':forbidden.append(str(rel))
   if p.name!='SHA256SUMS.json':files.append(p)
 files.sort()
 print('HASH',edition,len(files),'files',flush=True)
 obsolete=[str(p.relative_to(package)) for p in files if any('faster_whisper' in part.lower() or 'faster-whisper' in part.lower() or 'ctranslate2' in part.lower() for part in p.relative_to(package).parts)]
 if obsolete:raise RuntimeError('Obsolete STT artifacts: '+str(obsolete[:10]))
 model=package/'apps/desktop/data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09'
 assert all((model/name).is_file() for name in ('encoder.int8.onnx','decoder.onnx','joiner.int8.onnx','tokens.txt'))
 if forbidden:raise RuntimeError('Forbidden files: '+str(forbidden[:10]))
 def fingerprint(p):return p.relative_to(package).as_posix(),{'size':p.stat().st_size,'sha256':sha(p)}
 with ThreadPoolExecutor(max_workers=8) as pool:manifest=dict(pool.map(fingerprint,files))
 (package/'SHA256SUMS.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 release_dir=Path(output_dir).resolve() if output_dir else ROOT/'releases'
 release_dir.mkdir(parents=True,exist_ok=True)
 target=release_dir/('AI_FOR_EXAM_'+edition+'.zip');partial=target.with_suffix('.zip.partial')
 # Reuse identical compressed records; fully verify every member hash below.
 previous_path=target if target.exists() else ROOT/'releases'/target.name
 previous=zipfile.ZipFile(previous_path) if previous_path.exists() else None
 try:
  old=json.loads(previous.read('AICare/SHA256SUMS.json')) if previous else {}
  ends={}
  if previous:
   infos=sorted(previous.infolist(),key=lambda i:i.header_offset)
   ends={i.filename:infos[n+1].header_offset if n+1<len(infos) else previous.start_dir for n,i in enumerate(infos)}
  with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
   for p in [*files,package/'SHA256SUMS.json']:
    rel=p.relative_to(package).as_posix();name='AICare/'+rel
    if previous and rel in manifest and old.get(rel)==manifest[rel] and name in previous.NameToInfo:
     original=previous.getinfo(name);entry=copy.copy(original)
     remaining=ends[name]-original.header_offset
     previous.fp.seek(original.header_offset);entry.header_offset=z.fp.tell()
     while remaining:
      data=previous.fp.read(min(1024*1024,remaining))
      if not data:raise RuntimeError('Truncated previous ZIP')
      z.fp.write(data);remaining-=len(data)
     z.filelist.append(entry);z.NameToInfo[name]=entry;z.start_dir=z.fp.tell();z._didModify=True
    else:z.write(p,name)
 finally:
  if previous:previous.close()
 print('CRC_VERIFY',edition,flush=True)
 with zipfile.ZipFile(partial) as z:
  assert len(z.namelist())==len(set(z.namelist()))
  for rel,expected in manifest.items():
   with z.open('AICare/'+rel) as member:
    actual=hashlib.file_digest(member,'sha256').hexdigest()
   if actual!=expected['sha256']:raise RuntimeError('ZIP hash mismatch: '+rel)
  assert {'AICare/AICare.exe','AICare/runtime/python.exe','AICare/QA_REPORT.txt','AICare/SHA256SUMS.json'}<=set(z.namelist())
 partial.replace(target);digest=sha(target)
 target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='ascii')
 result={'edition':edition,'file':target.name,'bytes':target.stat().st_size,'files':len(manifest)+1,'sha256':digest,'crc':'passed','all_member_sha256':'passed','native_qa':'passed','clean_windows':'not tested; see QA_REPORT.txt'}
 (release_dir/('PORTABLE_'+edition+'_VALIDATION.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('package',type=Path);args=p.parse_args();archive(args.package)
