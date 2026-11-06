"""OpenPifPaf worker adapter. Camera/UI remain owned by the host application."""
import hashlib
import json
import os
from pathlib import Path
import queue
import struct
import subprocess
import threading

import numpy as np

ROOT = Path(__file__).resolve().parents[3]


class HfdPoseBackend:
    def __init__(self, python=None, weights=None, resolution=None, timeout=60):
        self.timeout = timeout
        self.python = Path(python or os.environ.get('FALL_HFD_PYTHON', ROOT/'.cache/hfd-py39/Scripts/python.exe')).resolve()
        self.weights = Path(weights or os.environ.get('FALL_HFD_POSE_WEIGHTS', ROOT/'features/fall_risk/hfd_model/models/shufflenetv2k16.pkl')).resolve()
        if not self.python.is_file() or not self.weights.is_file():
            raise FileNotFoundError('HFD requires local worker Python and OpenPifPaf weights; run scripts/setup_hfd.ps1')
        if weights is None and not os.environ.get('FALL_HFD_POSE_WEIGHTS'):
            expected='f90ed364912c3295c93e9e8c9054652ea9fdcc669accc73db65258c87237c7f5'
            if hashlib.sha256(self.weights.read_bytes()).hexdigest()!=expected:
                raise ValueError('Default HFD OpenPifPaf checkpoint hash mismatch')
        self.resolution = float(resolution or os.environ.get('FALL_HFD_RESOLUTION', '.4'))
        if not 0 < self.resolution <= 2:
            raise ValueError('FALL_HFD_RESOLUTION must be in (0,2]')
        self.responses = queue.Queue(maxsize=2)
        self.lock = threading.Lock()
        self.closed = False
        logdir = ROOT/'.cache/hfd-worker'; logdir.mkdir(parents=True, exist_ok=True)
        self.log = (logdir/'stderr.log').open('ab')
        env = dict(os.environ, OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
        self.process = subprocess.Popen([str(self.python), '-u', str(ROOT/'scripts/hfd_pose_worker.py'),
                                         '--weights', str(self.weights), '--resolution', str(self.resolution)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, env=env,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            ready = self._response()
            if ready.get('type') != 'ready':
                raise RuntimeError(f'HFD worker failed: {ready}')
            self.metadata = dict(backend='hfd-openpifpaf-worker', weights=self.weights.name,
                                 sha256=hashlib.sha256(self.weights.read_bytes()).hexdigest(),
                                 resolution=self.resolution, worker=ready.get('runtime'), device='cpu')
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                value = json.loads(line)
                self.responses.put(value, timeout=self.timeout)
        except Exception as error:
            try:self.responses.put_nowait({'error': str(error)})
            except queue.Full:pass
        finally:
            try:self.responses.put_nowait({'error': 'HFD worker exited'})
            except queue.Full:pass

    def _response(self):
        try:result = self.responses.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError('HFD pose inference timed out; see .cache/hfd-worker/stderr.log')
        if 'error' in result:
            self.close()
            raise RuntimeError(result['error'])
        return result

    def observe(self, packet):
        from features.fall_risk.pipeline.fall_backends import coco_to_mediapipe
        from features.fall_risk.pipeline.fall_pipeline import PersonObservation
        image = np.ascontiguousarray(packet.image)
        if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('HFD expects uint8 BGR HWC frame')
        header = dict(shape=list(image.shape), source_id=packet.source_id, run_id=packet.run_id,
                      frame_index=packet.frame_index, pts_sec=packet.pts_sec)
        encoded = json.dumps(header).encode('utf-8')
        with self.lock:
            if self.closed:raise RuntimeError('HFD backend is closed')
            try:
                self.process.stdin.write(struct.pack('<I',len(encoded))+encoded+image.tobytes())
                self.process.stdin.flush()
                result = self._response()
            except OSError:
                self.close()
                raise
        if any(result.get(k) != header[k] for k in ('source_id','run_id','frame_index','pts_sec')):
            self.close()
            raise RuntimeError('HFD worker response identity does not match request')
        self.last_timing=result.get('timing',{})
        self.metadata['last_network_backend']=self.last_timing.get('network_backend','pytorch')
        if self.metadata['last_network_backend']=='onnx' and 'onnx_exports' not in self.metadata:
            self.metadata['onnx_exports']=[dict(filename=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                                          for p in self.weights.parent.glob(self.weights.stem+'-*.onnx')]
        found=[]
        for item in result['observations']:
            points=np.asarray(item['keypoints'],dtype=float)
            obs=PersonObservation(item['bbox'], item['score'], coco_to_mediapipe(points), packet.pts_sec)
            obs.coco_keypoints = np.asarray(item['model_keypoints'],dtype=float)
            obs.model_bbox = tuple(item['model_bbox'])
            found.append(obs)
        return found

    def close(self):
        if self.closed:return
        self.closed=True
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        for stream in (self.process.stdin,self.process.stdout):
            if stream:stream.close()
        if threading.current_thread() != self.reader:self.reader.join(timeout=2)
        self.log.close()
