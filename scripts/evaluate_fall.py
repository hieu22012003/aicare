"""Reproducible offline runs. Never dispatch SMTP or write patient records."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mediapipe  # Load bundled Windows DLLs before OpenCV/psutil.
import cv2
import numpy as np
import psutil
from shared.camera.frame_source import VideoFileFrameSource


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("video")
    p.add_argument("--phase", default="after")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--legacy", action="store_true")
    p.add_argument('--backend', choices=['auto','hog','yolo','yolo-mediapipe','yolo-silhouette','onnx','hfd','hfd-pose'], default='auto')
    p.add_argument('--weights', default='features/fall_risk/hfd_model/models/yolo11n-pose.pt')
    p.add_argument('--image-size', type=int, default=640)
    p.add_argument('--ground-truth', default=None)
    p.add_argument("--output", default="docs/test-reports/fall_detection")
    a = p.parse_args()
    if a.legacy:
        from experiments.fall_legacy_reference import FallTestRunner
        from experiments.fall_legacy_source import VideoFileFrameSource
    else:
        from features.fall_risk.fall_test_runner import FallTestRunner
        from shared.camera.frame_source import VideoFileFrameSource
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    import torch
    torch.set_num_threads(4)
    code = {str(f.relative_to(ROOT)): digest(f) for folder in ("core", "features/fall_risk/hfd_model", "services", "adapters", "app")
            for f in (ROOT / folder).rglob("*.py")}
    code['scripts/hfd_pose_worker.py']=digest(ROOT/'scripts/hfd_pose_worker.py')
    config_hash = digest(ROOT / "shared/config.py")
    for index in range(a.repeats):
        run = Path(a.output) / f"{a.phase}_{index+1:02d}"
        run.mkdir(parents=True, exist_ok=False)
        source = VideoFileFrameSource(a.video)
        factory = None
        if not a.legacy and a.backend != 'auto':
            from features.fall_risk.pipeline.fall_pipeline import PersonPoseBackend
            from features.fall_risk.pipeline.fall_backends import YoloPoseBackend, ForegroundAssistedBackend, OnnxPoseBackend
            def factory():
                if a.backend in ('hfd','hfd-pose'):
                    from features.fall_risk.hfd_model.hfd_pose_backend import HfdPoseBackend
                    backend = HfdPoseBackend()
                    backend.use_hfd_classifier = a.backend == 'hfd'
                    return backend
                if a.backend == 'hog':
                    return PersonPoseBackend()
                if a.backend == 'onnx':
                    return OnnxPoseBackend(a.weights,a.image_size)
                candidate = YoloPoseBackend(a.weights, a.image_size, hybrid=a.backend == 'yolo-mediapipe')
                return ForegroundAssistedBackend(candidate) if a.backend == 'yolo-silhouette' else candidate
        kwargs = dict(output_dir=str(run))
        if not a.legacy:
            kwargs.update(backend_factory=factory,ground_truth_path=a.ground_truth)
        runner = FallTestRunner(source, **kwargs)
        manifest = dict(run_id=run.name, dataset_version="legacy-labels-unreviewed",
                        video_sha256=source.sha256, code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                        working_tree_hash=hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest(),
                        source_hashes=code, config_hash=config_hash, model_version="legacy-MOG2" if a.legacy else getattr(runner.pipeline.backend,'metadata',{'backend':'hog-mediapipe'}),
                        platform=platform.platform(), cpu=platform.processor(), cpu_logical=os.cpu_count(), gpu_used=False,
                        started_utc=datetime.now(timezone.utc).isoformat(), source_fps=source.fps,
                        metadata_frames=source.total_frames, seed=0, opencv_threads=1,
                        mode="video_file", command=sys.argv, source_pts_policy="legacy-index/fps" if a.legacy else "decoder-POS_MSEC")
        manifest['pipeline_run_id'] = getattr(runner, 'run_id', None)
        manifest['packages'] = dict(python=sys.version, opencv=cv2.__version__, mediapipe=mediapipe.__version__)
        manifest['hog_svm_sha256'] = hashlib.sha256(cv2.HOGDescriptor_getDefaultPeopleDetector().tobytes()).hexdigest()
        manifest['model_hashes'] = {str(f.relative_to(Path(mediapipe.__file__).parent)): digest(f)
                                    for f in Path(mediapipe.__file__).parent.rglob('pose*.tflite')}
        write(run / "manifest.json", manifest)
        timings, pts_values = [], []
        process = psutil.Process()
        cpu0 = sum(process.cpu_times()[:2])
        started = time.perf_counter()
        peak_rss = process.memory_info().rss
        protected = {str(f): digest(f) for folder in ("data/users", "data/fall_events", "shared") for f in Path(folder).rglob("*") if f.is_file()}
        with patch("smtplib.SMTP", side_effect=AssertionError("SMTP forbidden in offline evaluation")) as smtp, patch("smtplib.SMTP_SSL", side_effect=AssertionError("SMTP SSL forbidden")) as ssl:
            while True:
                tick = time.perf_counter()
                ok, _, pts, tel = runner.process_next_frame()
                if not ok:
                    break
                timings.append(time.perf_counter() - tick)
                pts_values.append(pts)
                peak_rss = max(peak_rss, process.memory_info().rss)
            report = runner.generate_evaluation_report()
            smtp_count = smtp.call_count + ssl.call_count
        elapsed = time.perf_counter() - started
        cpu_sec = sum(process.cpu_times()[:2]) - cpu0
        after = {str(f): digest(f) for folder in ("data/users", "data/fall_events", "shared") for f in Path(folder).rglob("*") if f.is_file()}
        perf = dict(decoded_frames=len(timings), first_pts=pts_values[0] if pts_values else None,
                    last_pts=pts_values[-1] if pts_values else None, wall_seconds=elapsed,
                    processing_fps=len(timings)/elapsed, frame_latency_p50_ms=float(np.percentile(timings, 50)*1000) if timings else None,
                    frame_latency_p95_ms=float(np.percentile(timings, 95)*1000) if timings else None,
                    cpu_seconds=cpu_sec, cpu_percent_one_core=100*cpu_sec/elapsed, peak_rss_bytes=peak_rss,
                    application_dropped_frames=0, metadata_frame_discrepancy=source.total_frames-len(timings),
                    frame_count_status="PASS" if abs(source.total_frames-len(timings))<=1 else "FAIL",
                    smtp_calls=smtp_count, protected_records_unchanged=protected==after)
        perf['resource_scope']='Parent process only; separate pose worker CPU/RSS not included'
        manifest['model_version']="legacy-MOG2" if a.legacy else getattr(runner.pipeline.backend,'metadata',{'backend':'hog-mediapipe'})
        write(run / 'manifest.json',manifest)
        write(run / "performance.json", perf)
        write(run / "events.json", [asdict(e) for e in runner.events])
        write(run / "matches.json", report.get("eval_details", []))
        write(run / "metrics.json", report["metrics"])
        write(run / "pts.json", pts_values)
        source.release()
        if hasattr(runner, "close"):
            runner.close()
        print(json.dumps(dict(run_id=run.name, events=len(runner.events), frames=len(timings), fps=round(perf["processing_fps"],2), smtp_calls=smtp_count)), flush=True)


if __name__ == "__main__":
    main()
