"""Ablate FSM/fusion on identical cached detections, with no model or network."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import mediapipe  # Windows DLL initialization before importing torch in HFD mode
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from features.fall_risk.pipeline.fall_pipeline import FallConfig, FallPipeline, FramePacket, PersonObservation


class EmptyBackend:
    def observe(self,packet):return []


def replay(path,config,classifier=False):
    core=FallPipeline(EmptyBackend(),config,run_id='ablation')
    if classifier:
        from features.fall_risk.hfd_model.hfd_classifier import HfdClassifier
        core.classifier=HfdClassifier(max_gap=config.max_observation_gap)
    trace=[]
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line)
            obs=[PersonObservation(tuple(o['bbox']),o['detector_confidence'],
                 np.array(o['keypoints']) if o['keypoints'] is not None else None,
                 evidence_source=o.get('evidence_source','pose')) for o in row['observations']]
            for observation,data in zip(obs,row['observations']):
                if data.get('coco_keypoints') is not None:observation.coco_keypoints=np.array(data['coco_keypoints'])
                if data.get('model_bbox') is not None:observation.model_bbox=data['model_bbox']
            packet=FramePacket('cached','video_file',row['index'],row['pts'],np.zeros(row['shape'],np.uint8),'ablation')
            trace.append(dict(pts=row['pts'],tracks=core.process(packet,obs)))
    return dict(events=[asdict(e) for e in core.events],check_notices=[asdict(n) for n in core.check_notices],transitions=core.transitions,audit=core.audit,
                diagnostics=list(core.diagnostics),trace=trace,config=asdict(config))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observations');parser.add_argument('--output',required=True)
    parser.add_argument('--hfd',action='store_true',help='Also compare HFD LSTM on the identical pose cache')
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    modes={'full_pose':FallConfig(partial_pose=False,shape_fallback=False),
           'partial_pose':FallConfig(shape_fallback=False),'fusion':FallConfig()}
    summary={}
    for name,config in modes.items():
        result=replay(args.observations,config)
        (out/f'{name}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        summary[name]=dict(events=result['events'],check_notices=result['check_notices'],transition_counts={state:sum(t['state']==state for t in result['transitions']) for state in ['SUSPECTED','CONFIRMING','FALL_CONFIRMED','ON_FLOOR_UNCERTAIN']})
    if args.hfd:
        result=replay(args.observations,FallConfig(),classifier=True)
        (out/'hfd.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        summary['hfd']=dict(events=result['events'],check_notices=result['check_notices'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
