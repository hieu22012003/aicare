"""Dry-run video adapter for the shared experimental FallPipeline.

The old MOG2 algorithm is preserved in experiments/fall_legacy_reference.py.
Ground truth enters the evaluator only, never the detector/tracker/FSM.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import uuid
from shared.camera.frame_source import VideoFileFrameSource
from features.fall_risk.pipeline.fall_pipeline import FallPipeline, FallConfig, FramePacket, FallEvent as TestEvent, TrackState
from features.fall_risk.pipeline.fall_evaluation import evaluate


class FallTestRunner:
    def __init__(self, video_source, ground_truth_path=None, confirm_seconds=0.8, output_dir=None, backend_factory=None):
        if not isinstance(video_source, VideoFileFrameSource) or not video_source.is_test_mode:
            raise ValueError('FallTestRunner requires a dry-run video source')
        self.source = video_source
        self.config = FallConfig(confirm_seconds=confirm_seconds)
        self.backend_factory = backend_factory
        self.output_dir = Path(output_dir or ('data/video_test_runs/' + uuid.uuid4().hex))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ground_truth_path = ground_truth_path or 'data/video_annotations/elderly_fall_v2.json'
        self.ground_truth = json.loads(Path(self.ground_truth_path).read_text(encoding='utf-8')) if Path(self.ground_truth_path).exists() else None
        if self.ground_truth and any('subject_id' not in s for s in self.ground_truth.get('subjects',[])):
            self.ground_truth = None
        self._start_session()

    def _start_session(self):
        self.run_id = uuid.uuid4().hex
        self.pipeline = FallPipeline(self.backend_factory() if self.backend_factory else None,self.config,self.run_id)
        self.mock_emails, self.trace = [], []
        self._reported = None
        self._complete = False
        self._generation = self.source.generation
        self._last_raw = None
        self._emailed = set()
        self._partial_seek = self.source.frame_idx != 0
        from features.fall_risk.pipeline.fall_evidence import VideoEvidenceRecorder
        self.evidence = VideoEvidenceRecorder(self.output_dir/self.run_id/'evidence')

    @property
    def tracks(self):
        return self.pipeline.tracks

    @property
    def events(self):
        return self.pipeline.events

    @property
    def check_notices(self):
        return self.pipeline.check_notices

    @property
    def run_output_dir(self):
        return str(self.output_dir)

    def reset(self):
        if self.trace or self.events:
            self.evidence.close()
            self.generate_evaluation_report()
        self.pipeline.close()
        self.source.restart()
        self._start_session()

    def seek(self, target_sec):
        """Replay from start through target PTS to rebuild history."""
        self.reset()
        target_sec = max(0.,min(float(target_sec),self.source.duration_seconds))
        while True:
            result = self.process_next_frame()
            if not result[0] or result[2] >= target_sec:
                return result

    def process_next_frame(self):
        if self.source.generation != self._generation:
            self.evidence.close()
            if self.trace:
                self.generate_evaluation_report()
            self.pipeline.close()
            self._start_session()
        ret,frame,pts,index,eof = self.source.read_frame()
        if not ret:
            if eof:
                self.evidence.close()
                self._complete = True
                self.pipeline.tracks.clear()
            return False,self._last_raw if eof else None,pts,dict(is_eof=eof,pts=pts,frame_idx=index,tracks=[],confirmed_events_count=len(self.events),dry_run=True)
        self._last_raw = frame.copy()
        packet = FramePacket(self.source.source_id,'video_file',index,pts,frame,self.run_id)
        tracks = self.pipeline.process(packet)
        self.evidence.feed(pts,frame,self.events)
        self.trace.append(dict(pts=pts,frame_idx=index,tracks=tracks))
        for event in self.events:
            if event.event_id not in self._emailed:
                from shared.services.email_service import EmailService
                result = EmailService().send_fall_alert('', '', '', event_id=event.event_id, mode='video_file')
                event.notification_status = result.status
                event.mock_email_content = dict(event_id=event.event_id,track_id=event.track_id,video_pts=pts,status=result.status,dispatched=False)
                self.mock_emails.append(event.mock_email_content)
                self._emailed.add(event.event_id)
        annotated = self.pipeline.render(frame,tracks)
        from shared.camera.drawing_utils import draw_vietnamese_text
        draw_vietnamese_text(annotated,f'THỬ NGHIỆM · KHÔNG GỬI EMAIL · PTS {pts:.2f}s',(10,8),font_size=18,color=(0,220,255))
        return True,annotated,pts,dict(pts=pts,frame_idx=index,total_frames=self.source.total_frames,is_eof=False,
                                     tracks=tracks,backend=getattr(self.pipeline.backend,'metadata',{}),
                                     confirmed_events_count=len(self.events),mock_emails_count=len(self.mock_emails),dry_run=True)

    def evaluation_snapshot(self):
        report = evaluate([asdict(e) for e in self.events],self.ground_truth,self.source.sha256,self.trace,self._complete and not self._partial_seek)
        report.update(run_id=self.run_id,video_metadata=dict(source_id=self.source.source_id,file_path=self.source.file_path,
                      sha256=self.source.sha256,duration_seconds=self.source.duration_seconds,total_frames=self.source.total_frames,fps=self.source.fps),
                      dry_run_safety=dict(mode='video_file',dispatch_policy='DRY_RUN',mock_emails_logged=len(self.mock_emails),evidence_isolation=str(self.output_dir)),
                      detected_events=[asdict(e) for e in self.events],mock_emails=self.mock_emails)
        report['check_notices'] = [asdict(n) for n in self.check_notices]
        report['backend'] = getattr(self.pipeline.backend, 'metadata', {'backend':'hog-mediapipe'})
        duration,unobservable = 0.,0.
        for prev,row in zip(self.trace,self.trace[1:]):
            dt = row['pts']-prev['pts']; duration += dt
            if not prev['tracks'] or any(t['state'] in ('UNOBSERVABLE','UNCERTAIN') for t in prev['tracks']):
                unobservable += dt
        report['metrics']['unobservable_frame_time_fraction'] = unobservable/duration if duration else None
        return report

    def generate_evaluation_report(self):
        revision = (len(self.trace),len(self.events),self._complete,self.run_id,
                    tuple((e.snapshot_path,e.evidence.get('clip_path')) for e in self.events))
        report = self.evaluation_snapshot()
        if revision == self._reported:
            return report
        folder = self.output_dir / self.run_id
        folder.mkdir(parents=True,exist_ok=True)
        payloads = {'events':report['detected_events'],'check_notices':report['check_notices'],
                    'diagnostics':list(self.pipeline.diagnostics),'matches':report['eval_details'],'metrics':report['metrics'],
                    'ground_truth':self.ground_truth,'trace':self.trace,'transitions':self.pipeline.transitions,'tracking_audit':self.pipeline.audit,
                    'evaluation_report':report,'session_manifest':dict(run_id=self.run_id,mode='video_file',config=asdict(self.config),
                    config_hash=hashlib.sha256(json.dumps(asdict(self.config),sort_keys=True).encode()).hexdigest(),
                    ground_truth_sha256=hashlib.sha256(Path(self.ground_truth_path).read_bytes()).hexdigest() if Path(self.ground_truth_path).exists() else None,
                    complete=self._complete,partial_seek=self._partial_seek,video_sha256=self.source.sha256)}
        for name,value in payloads.items():
            (folder/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'evaluation_report.md').write_text('# Fall regression — '+report['status']+'\n\n'+report['reason']+'\n\n```json\n'+json.dumps(report['metrics'],indent=2)+'\n```\n',encoding='utf-8')
        self._reported = revision
        return report

    def run_evaluation(self, progress_callback=None):
        while True:
            ret,_,_,tel = self.process_next_frame()
            if progress_callback:
                progress_callback(tel)
            if tel.get('is_eof'):
                break
            if not ret:
                raise RuntimeError('Cannot batch-evaluate a paused source')
        return self.generate_evaluation_report()

    def close(self):
        try:
            self.evidence.close()
        finally:
            try:self.pipeline.close()
            finally:self.source.release()
