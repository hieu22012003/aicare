"""Real decode/inference benchmark. Input audio must be approved for testing."""
import argparse
import json
import sys
import time
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.voice_assistant.local_voice.config import VoiceConfig
from features.voice_assistant.local_voice.engine import LocalSttEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', nargs='+')
    parser.add_argument('--model', default='data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09')
    parser.add_argument('--device', choices=['cpu'], default='cpu')
    parser.add_argument('--compute-type', default='int8')
    parser.add_argument('--beam-size', type=int, choices=[1, 3, 5], default=1)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--camera', type=int)
    parser.add_argument('--output', default='docs/test-reports/local_stt/benchmark.json')
    args = parser.parse_args()
    import numpy as np
    import psutil
    frames, stop, errors = [], threading.Event(), []
    def camera():
        import cv2
        cap = cv2.VideoCapture(args.camera)
        try:
            if not cap.isOpened():
                errors.append('Camera unavailable')
                return
            while not stop.is_set():
                begin = time.perf_counter()
                ok, _ = cap.read()
                if not ok:
                    errors.append('Camera read failed')
                    break
                frames.append(time.perf_counter() - begin)
        finally:
            cap.release()
    thread = None
    if args.camera is not None:
        thread = threading.Thread(target=camera)
        thread.start()
    start = time.perf_counter()
    results = []
    try:
        engine = LocalSttEngine(VoiceConfig(args.model, args.device, args.compute_type,
                                          beam_size=args.beam_size))
        cold_ms = round((time.perf_counter() - start) * 1000)
        for path in args.audio:
            audio = Path(path).read_bytes()
            for _ in range(args.repeats):
                result = engine.transcribe(audio)
                result['file'] = Path(path).name
                results.append(result)
        times = [r['inference_ms'] for r in results]
        report = dict(cold_load_ms=cold_ms, results=results,
                      p50_ms=float(np.percentile(times, 50)), p95_ms=float(np.percentile(times, 95)),
                      rss_bytes=psutil.Process().memory_info().rss,
                      camera_read_p95_ms=float(np.percentile(frames, 95) * 1000) if frames else None,
                      camera_errors=errors, camera_pipeline='capture only, no pose/face inference')
    except Exception as exc:
        report = dict(error=str(exc), device=args.device, camera_errors=errors)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=3)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if 'error' in report else 0


if __name__ == '__main__':
    raise SystemExit(main())
