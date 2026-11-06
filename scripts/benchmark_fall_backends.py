"""Replay source video and cache measured observations for fair offline ablation."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mediapipe  # Windows DLL order
import cv2
import numpy as np
import torch
from shared.camera.frame_source import VideoFileFrameSource
from features.fall_risk.pipeline.fall_pipeline import FallPipeline, FramePacket, PersonPoseBackend
from features.fall_risk.pipeline.fall_backends import YoloPoseBackend, ForegroundAssistedBackend, OnnxPoseBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video')
    parser.add_argument('--backend', choices=['hog', 'yolo', 'yolo-mediapipe', 'yolo-silhouette','onnx','hfd','hfd-pose'], required=True)
    parser.add_argument('--weights', default='features/fall_risk/hfd_model/models/yolo11n-pose.pt')
    parser.add_argument('--image-size', type=int, default=640)
    parser.add_argument('--output', required=True)
    parser.add_argument('--stride', type=int, default=1)
    args = parser.parse_args()
    if args.stride < 1:
        parser.error('stride must be >= 1')
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    source = VideoFileFrameSource(args.video)
    if args.backend in ('hfd','hfd-pose'):
        from features.fall_risk.hfd_model.hfd_pose_backend import HfdPoseBackend
        backend=HfdPoseBackend()
        backend.use_hfd_classifier=args.backend=='hfd'
    else:
        backend = PersonPoseBackend() if args.backend == 'hog' else YoloPoseBackend(
            args.weights, args.image_size, hybrid=args.backend == 'yolo-mediapipe') if args.backend != 'onnx' else OnnxPoseBackend(args.weights,args.image_size)
    if args.backend == 'yolo-silhouette':
        backend = ForegroundAssistedBackend(backend)
    pipeline = FallPipeline(backend=backend, run_id='diagnostic')
    meta = dict(video_sha256=source.sha256, source=str(Path(args.video).resolve()),
                backend=getattr(backend, 'metadata', {'backend': 'hog-mediapipe'}),
                stride=args.stride, baseline_sha256=hashlib.sha256((ROOT/'features/fall_risk/pipeline/fall_pipeline.py').read_bytes()).hexdigest())
    (out/'manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    latencies, counts, full_pose, torso_pose, decoded = [], [], 0, 0, 0
    start = time.perf_counter()
    try:
        with (out/'observations.jsonl').open('w', encoding='utf-8') as stream:
            while True:
                ok, frame, pts, index, eof = source.read_frame()
                if not ok:
                    break
                decoded += 1
                if (decoded-1) % args.stride:
                    continue
                packet = FramePacket(source.source_id, 'video_file', index, pts, frame, 'diagnostic')
                tick = time.perf_counter()
                observations = backend.observe(packet)
                latency = time.perf_counter()-tick
                latencies.append(latency); counts.append(len(observations))
                for obs in observations:
                    kp = obs.keypoints
                    if kp is not None:
                        full_pose += int(min(kp[i,2] for i in (11,12,23,24,25,26,27,28)) >= .5)
                        torso_pose += int(min(kp[i,2] for i in (11,12,23,24)) >= .5)
                tracks = pipeline.process(packet, observations)
                serialized = [dict(bbox=[float(x) for x in o.bbox], detector_confidence=float(o.detector_confidence),
                                   keypoints=o.keypoints.tolist() if o.keypoints is not None else None,
                                   evidence_source=o.evidence_source,
                                   coco_keypoints=o.coco_keypoints.tolist() if hasattr(o,'coco_keypoints') else None,
                                   model_bbox=o.model_bbox if hasattr(o,'model_bbox') else None)
                              for o in observations]
                stream.write(json.dumps(dict(pts=pts, index=index, shape=list(frame.shape), latency_ms=latency*1000,
                                             backend_timing=getattr(backend,'last_timing',{}),
                                             observations=serialized, baseline_tracks=tracks))+'\n')
                if decoded % 100 == 0:
                    print(f'{decoded} frames; PTS {pts:.2f}; detections {len(observations)}', flush=True)
    finally:
        pipeline.close(); source.release()
    elapsed = time.perf_counter()-start
    report = dict(decoded_frames=decoded, inferred_frames=len(counts), observations=sum(counts),
                  frames_with_person=sum(c>0 for c in counts), all_joints_usable=full_pose, torso_usable=torso_pose,
                  inference_fps=len(counts)/sum(latencies) if latencies else None, wall_seconds=elapsed,
                  latency_p50_ms=float(np.percentile(latencies,50)*1000) if latencies else None,
                  latency_p95_ms=float(np.percentile(latencies,95)*1000) if latencies else None,
                  baseline_events=[asdict(e) for e in pipeline.events], status='DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION')
    report['check_notices'] = [asdict(n) for n in pipeline.check_notices]
    (out/'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
