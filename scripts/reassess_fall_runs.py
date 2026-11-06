"""Recompute matches/metrics from immutable event files; never rerun/tune detector."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import sys
import importlib.metadata
import cv2
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from features.fall_risk.pipeline.fall_evaluation import evaluate
from features.fall_risk.pipeline.fall_pipeline import FallConfig


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


root=ROOT/'docs/test-reports/fall_detection'
gt_path=ROOT/'data/video_annotations/elderly_fall_v2.json'
gt=json.loads(gt_path.read_text(encoding='utf-8'))
for phase in ('baseline','candidate'):
    for folder in sorted(root.glob(phase+'_[0-9][0-9]')):
        if not (folder/'events.json').exists(): continue
        events=json.loads((folder/'events.json').read_text(encoding='utf-8'))
        if phase=='baseline':
            for event in events:
                x,y,w,h=event['bbox']; event['bbox']=[x/1280,y/720,w/1280,h/720]
                event['suspected_pts']=event['start_pts']
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        trace_files=list(folder.glob('*/trace.json'))
        trace=json.loads(trace_files[0].read_text(encoding='utf-8')) if trace_files else None
        result=evaluate(events,gt,manifest['video_sha256'],trace)
        result['provenance']=dict(events_sha256=sha(folder/'events.json'),ground_truth_sha256=sha(gt_path),
                                  evaluator_sha256=sha(ROOT/'features/fall_risk/pipeline/fall_evaluation.py'),annotation_status=gt['annotation_status'])
        target=folder/'reassessment'
        target.mkdir(exist_ok=True)
        for name,value in dict(metrics=result['metrics'],matches=result['eval_details'],evaluation=result,ground_truth=gt).items():
            (target/f'{name}.json').write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
versions={name:importlib.metadata.version(name) for name in ('mediapipe','opencv-contrib-python','numpy','scipy','psutil')}
mp_root=Path(importlib.metadata.distribution('mediapipe').locate_file('mediapipe'))
provenance=dict(python=sys.version,packages=versions,models={str(p.relative_to(mp_root)):sha(p) for p in mp_root.rglob('pose*.tflite')},
                hog_svm_sha256=hashlib.sha256(cv2.HOGDescriptor_getDefaultPeopleDetector().tobytes()).hexdigest(),
                pipeline_config=asdict(FallConfig()),config_hash=hashlib.sha256(json.dumps(asdict(FallConfig()),sort_keys=True).encode()).hexdigest(),
                platform=platform.platform(),cpu='Intel Core 5 210H',gpu_used=False,
                provenance_note='Supplement collected after batch; individual original manifests preserved. Detector core unchanged across candidate repetitions.')
(root/'runtime_provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
print('Recomputed from immutable events; missing spatial GT remains NOT VERIFIED')
