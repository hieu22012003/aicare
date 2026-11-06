"""Measure foreground support on source frames with frozen detector observations."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import mediapipe
import cv2
from shared.camera.frame_source import VideoFileFrameSource
from features.fall_risk.pipeline.fall_pipeline import FramePacket, PersonObservation
from features.fall_risk.pipeline.fall_backends import ForegroundAssistedBackend


class CachedBackend:
    def __init__(self,path):
        self.rows={}
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            row=json.loads(line);self.rows[row['index']]=row
        self.metadata=dict(observations_sha256=__import__('hashlib').sha256(Path(path).read_bytes()).hexdigest())
    def observe(self,packet):
        row=self.rows[packet.frame_index]
        if abs(row['pts']-packet.pts_sec)>1e-6:
            raise ValueError('Cached PTS does not match source')
        return [PersonObservation(tuple(o['bbox']),o['detector_confidence'],
                np.array(o['keypoints']) if o['keypoints'] is not None else None)
                for o in row['observations']]
    def close(self):pass


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('video');p.add_argument('cache');p.add_argument('--output',required=True)
    args=p.parse_args();out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    source=VideoFileFrameSource(args.video);backend=ForegroundAssistedBackend(CachedBackend(args.cache))
    cv2.setNumThreads(1)
    try:
        with out.open('w',encoding='utf-8') as stream:
            while True:
                ok,frame,pts,index,eof=source.read_frame()
                if not ok:break
                obs=backend.observe(FramePacket(source.source_id,'video_file',index,pts,frame,'support'))
                stream.write(json.dumps(dict(pts=pts,index=index,shape=list(frame.shape),observations=[
                    dict(bbox=[float(x) for x in o.bbox],detector_confidence=float(o.detector_confidence),
                         keypoints=o.keypoints.tolist() if o.keypoints is not None else None,evidence_source=o.evidence_source)
                    for o in obs]))+'\n')
    finally:backend.close();source.release()


if __name__=='__main__':main()
