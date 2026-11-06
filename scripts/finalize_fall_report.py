"""Build an evidence index and before/after report, without editing run results."""
from pathlib import Path
import json
import hashlib
import platform
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    root=ROOT/'docs/test-reports/fall_detection'
    rows=[]
    for phase in ('baseline','candidate'):
        for folder in sorted(root.glob(phase+'_*')):
            if not (folder/'performance.json').exists():
                continue
            perf=json.loads((folder/'performance.json').read_text(encoding='utf-8'))
            events=json.loads((folder/'events.json').read_text(encoding='utf-8'))
            rows.append(dict(run_id=folder.name,events=len(events),event_sequence=[(e['track_id'],e['confirmed_pts']) for e in events],**perf))
    gt=json.loads((ROOT/'data/video_annotations/elderly_fall_v2.json').read_text(encoding='utf-8'))
    initial_log=(ROOT/'logs/app.log').read_bytes()[:411260]
    old_events=[line for line in initial_log.decode('utf-8',errors='replace').splitlines() if 'DRY-RUN FALL EVENT:' in line]
    evidence=dict(original_log_prefix_sha256=hashlib.sha256(initial_log).hexdigest(),original_log_bytes=len(initial_log),
                  log_events=old_events,missing=['app(2).log','two independent annotations + adjudication','held-out/negative/alternate-angle videos'],
                  old_log_code_provenance='NOT VERIFIED: commit/config not logged',hardware_cpu='Intel Core 5 210H',platform=platform.platform())
    (root/'evidence_index.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
    (root/'comparison.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    lines=['# Fall detection: before/after — FAIL / NOT VERIFIED',
           '', '## Decision', '',
           '**Do not enable real Gmail alerts.** The shared HOG + crop-pose candidate misses the two annotated falls; it is experimental and rejected for operational use. Camera detections remain trial-only. No claim of clinical validation.',
           '', '## Five clean-state repetitions per implementation', '',
           'The initial working tree was already dirty. No reset/clean was performed. Each repetition created a new source, MOG2/tracker/FSM or shared pipeline. OpenCV RNG seed 0; OpenCV threads 1. Runs have source hashes, commit and config hash. UUIDs differ by design.',
           '', '| Run | Events | Decoded | FPS processing | p50 / p95 ms | CPU (one core %) | Peak RSS MiB | SMTP calls | Live data unchanged |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        lines.append(f"| {r['run_id']} | {r['events']} | {r['decoded_frames']} | {r['processing_fps']:.2f} | {r['frame_latency_p50_ms']:.2f} / {r['frame_latency_p95_ms']:.2f} | {r['cpu_percent_one_core']:.1f} | {r['peak_rss_bytes']/2**20:.1f} | {r['smtp_calls']} | {r['protected_records_unchanged']} |")
    lines += ['', 'Wall timings include decode, inference, rendering and final report. p50/p95 are per processed frame; throughput is frames / total batch wall time, NOT source FPS. Initialization/model construction is outside the timer. Peak RSS is sampled per frame. GPU not used. Windows / Intel Core 5 210H. Some runs overlap input inspection or tests; this is measured workstation throughput, not a controlled benchmark. No acceptance FPS/CPU/RAM budget was supplied.',
              '', '## Event metrics and uncertainty', '',
              '| Metric | Baseline MOG2 | Shared candidate |', '|---|---|---|',
              '| Events per run | 2, P2 @ 15.77 s; P3 @ 18.18 s | 0 |',
              '| TP / FP / FN | Legacy evaluator says 2 / 0 / 0; NOT VERIFIED (fixed x=560 mapping) | 0 / 0 / 2 against retained temporal labels, provisional; no prediction needs identity matching |',
              '| Certified subject/event TP / FP / FN | N/A: spatial labels missing | N/A: labels not adjudicated |',
              '| Precision / recall | Old 100% / 100% is not validated | Precision N/A; temporal-label recall 0/2, provisional |',
              '| FP per hour of independent negative videos | N/A: no such dataset | N/A: no such dataset |',
              '| Events in retained negative interval [0,13.5) | 0 | 0; cannot imply success given missed falls |',
              '| Exposure in that interval | 13.5 seconds = 0.00375 hours (provisional segment rate 0) | Same |',
              '| slip → confirmed | Legacy mapping: 0.97 / 1.68 seconds; NOT VERIFIED | N/A: no confirmations |',
              '| impact → confirmed | Legacy mapping: 0.17 / 0.98 seconds; NOT VERIFIED | N/A |',
              '| suspected → confirmed | 1.21 / 1.21 seconds from event records | N/A |',
              '| GT ID switches / fragmentation | NOT VERIFIED; no dense spatial GT | NOT VERIFIED; tracking audit stored |',
              '| Time not observable | Not recorded by legacy runner | 100% frame-time without all required visible joints on inspected runs; see metrics.json for each run |',
              '', 'The corrected evaluator refuses fixed left/right or track-number-to-GT mapping. Original legacy metrics are retained verbatim for audit, not endorsed. V2 GT is a schema migration with GT_MAN / GT_WOMAN, no invented bbox samples, entry/exit times, uncertainty or independent reviewers. The automatic metrics are null where evidence is missing. A zero-prediction run misses both retained labels regardless of spatial mapping; this provisional count is explicitly distinguished from certified evaluation.',
              '', '## Decode / clocks', '',
              'All source runs decode 498 frames versus metadata 500: FAIL (difference 2 > allowed 1). First decoder PTS 0.0; last 20.7290416667 s. Application intentional dropped frames: 0. Do not label undecoded frames as application drops. EOF is the first failed sequential read; seeking metadata EOF is not evidence of 500 decoded frames. The source video hash matches 5c70f33ed96d96bc8a306bc370a835c86a7c33db2404a1549caaa0aca614a5b4.',
              '', '## Historical evidence', '',
              'The supplied app.log records 9.59 s / 10.93 s at 08:58–09:04 and later 15.77 s / 18.18 s. These early detections were not reproduced by the five current baselines. Old logs lack commit/config/seed and therefore do not establish a cause. Code proves state was reused across direct seek, pixel scene masks existed, matching used fixed x, and reports were saved every sixth frame; these are defects, not proof of the old false-alarm cause.',
              '', 'Test_Fall.mp4 is a screen recording (1044 frames, 30 FPS, last PTS 34.7667 s). TestUI.mp4 is UI interaction (742 frames, 30 FPS, last PTS 24.7 s). Neither shares source-video PTS nor serves as an independently annotated negative camera scene. Hashes and full sequential inspection are in input_inspection/inputs.json.',
              '', '## Acceptance matrix', '',
              '| Case | Status / scope |', '|---|---|',
              '| T01 | FAIL: candidate has zero of two expected events. Baseline repeatability alone does not satisfy correct subject matching. |',
              '| T02 | Synthetic association/ambiguity tests PASS; real crossing/occlusion ID stability NOT VERIFIED. |',
              '| T03 | Synthetic bending/sitting/rest/rehab logic PASS; independent behavior videos NOT VERIFIED. |',
              '| T04 | FAIL on sample falls; other directions/partly occluded falls NOT VERIFIED. |',
              '| T05 | Synthetic source lifecycle and headless Qt UI PASS; source metadata/decode check FAIL. Full manual interaction/performance NOT VERIFIED. |',
              '| T06 | SMTP call counts 0 and protected record hashes unchanged in all recorded runs; explicit enabled-credential mock dispatch test PASS. |',
              '| T07 | Mock auth/network/timeout/retry/idempotency tests PASS; real Gmail delivery NOT VERIFIED and not attempted. |',
              '| T08 | Synthetic resolution normalization PASS; real alternate angle/light data NOT VERIFIED. |',
              '| T09 | Windows timings recorded; operational throughput acceptance NOT VERIFIED (no agreed budget). Candidate is much slower than source playback. |',
              '', '## Tests (actual logs retained)', '']
    for f in sorted(root.glob('checks*/summary.json')):
        d=json.loads(f.read_text(encoding='utf-8'))
        lines.append(f"- {f.parent.name}: {d['tests_run']} tests, {d['failures']} failures, {d['errors']} errors, {d['skipped']} skipped. Details: [{f.parent.name}/unittest.txt]({f.parent.name}/unittest.txt).")
    lines += ['', 'Initial script startup also failed once with MediaPipe Windows DLL initialization (fixed by pre-importing MediaPipe). CIM CPU lookup returned access denied; CPU name was read from the registry. Two legacy monitor tests initially failed because they mocked the removed single-person production route; migrated tests now inject per-person observations and assert the new trial-only policy, with initial failures retained.',
              '', '## Reproduce', '', 'See ../../docs/fall_detection/IMPLEMENTATION_NOTES.md for Windows commands, data locations and rollback. Each run folder contains manifest.json, events.json, matches.json, metrics.json, performance.json, pts.json. Candidate session subfolders additionally contain ground_truth.json, trace.json, transitions.json, tracking_audit.json and session_manifest.json. The reassessment/ subfolder holds metrics and matches recalculated from unchanged events by the final evaluator, with input/evaluator hashes and a GT copy. It reports the unambiguous 0/0/2 count for zero predictions while keeping annotation status NOT VERIFIED. Original run metrics are never overwritten.',
              '', 'Remaining requirements: an appropriate validated multi-person detector for prone/collapsed people, independently adjudicated spatial/time labels, held-out scene/person splits, calibrated event scores, durable cross-process email idempotency, live camera soak tests and pre-agreed performance/FP/recall/latency criteria. No thresholds were tuned to obtain 2/2.']
    (root/'BEFORE_AFTER.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Wrote comparison for',len(rows),'completed runs')


if __name__=='__main__': main()
