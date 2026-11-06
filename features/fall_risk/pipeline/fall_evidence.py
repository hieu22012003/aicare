"""Bounded, source-timestamped video evidence; never sends notifications."""
from collections import deque
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np


class VideoEvidenceRecorder:
    def __init__(self,folder,pre_seconds=2.,post_seconds=2.,sample_fps=10.):
        self.folder=Path(folder)
        self.pre,self.post,self.fps=pre_seconds,post_seconds,sample_fps
        self.buffer=deque(maxlen=80)
        self.pending={};self.recorded=set();self.last_sample=None;self.last_pts=None

    def feed(self,pts,frame,events):
        self.last_pts=pts
        sample=self.last_sample is None or pts-self.last_sample>=1/self.fps-1e-6
        if sample:
            ok,encoded=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,80])
            if not ok:raise RuntimeError('Cannot encode fall evidence')
            entry=(pts,encoded.tobytes());self.last_sample=pts
            self.buffer.append(entry)
            while self.buffer and pts-self.buffer[0][0]>self.pre+.25:self.buffer.popleft()
            for pending in self.pending.values():pending['frames'].append(entry)
        for event in events:
            if event.event_id in self.recorded:continue
            self.recorded.add(event.event_id)
            token=hashlib.sha256(event.event_id.encode()).hexdigest()[:24]
            folder=self.folder/token;folder.mkdir(parents=True,exist_ok=True)
            snapshot=folder/'snapshot.jpg'
            if not cv2.imwrite(str(snapshot),frame):raise RuntimeError('Cannot save fall snapshot')
            event.snapshot_path=str(snapshot.resolve())
            self.pending[token]=dict(event=event,folder=folder,shape=frame.shape,frames=list(self.buffer))
        for token,value in list(self.pending.items()):
            if pts-value['event'].confirmed_pts>=self.post:self._finish(token)

    def _finish(self,token):
        item=self.pending.pop(token);event=item['event'];frames=item['frames']
        if not frames:return
        start=max(frames[0][0],event.confirmed_pts-self.pre)
        end=min(frames[-1][0],event.confirmed_pts+self.post)
        h,w=item['shape'][:2]
        path=item['folder']/'clip.avi'
        writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),self.fps,(w,h))
        if not writer.isOpened():raise RuntimeError('Cannot open fall clip writer')
        decoded=[(pts,cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)) for pts,data in frames]
        timeline=[]
        try:
            for pts in np.arange(start,end+1e-6,1/self.fps):
                source,image=min(decoded,key=lambda item:abs(item[0]-pts))
                valid=abs(source-pts)<=.15 and image is not None
                writer.write(image if valid else np.zeros((h,w,3),np.uint8))
                timeline.append(dict(clip_pts=float(pts-start),source_pts=source if valid else None,observable=valid))
        finally:writer.release()
        event.evidence['clip_path']=str(path.resolve())
        metadata=dict(event_id=event.event_id,track_id=event.track_id,run_id=event.run_id,
                      confirmed_pts=event.confirmed_pts,source_start_pts=start,source_end_pts=end,
                      sample_fps=self.fps,complete_post_event=end>=event.confirmed_pts+self.post-.15,
                      source_timestamp_policy='nearest sampled raw frame; gaps black, see timeline',timeline=timeline)
        (item['folder']/'timestamps.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')

    def close(self):
        for token in list(self.pending):self._finish(token)
        self.buffer.clear()
