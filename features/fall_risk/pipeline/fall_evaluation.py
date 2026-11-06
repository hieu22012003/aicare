"""Offline, one-to-one subject/event matching; GT never enters the detector."""
from scipy.optimize import linear_sum_assignment
import numpy as np
from features.fall_risk.pipeline.fall_pipeline import iou


def subject_for(box, pts, subjects):
    candidates = []
    for subject in subjects:
        samples = [s for s in subject.get('bbox_samples',[]) if abs(s['pts_sec']-pts) <= .75]
        if samples:
            nearest = min(samples,key=lambda s:abs(s['pts_sec']-pts))
            candidates.append((iou(box,nearest['bbox']),subject['subject_id']))
    candidates.sort(reverse=True)
    if not candidates or candidates[0][0] < .20:
        return None, None
    if len(candidates)>1 and candidates[0][0]-candidates[1][0] < .10:
        return None, None
    return candidates[0][1], candidates[0][0]


def evaluate(events, gt, video_hash, trace=None, complete=True):
    base = dict(true_positives=None,false_positives=None,false_negatives=None,precision=None,recall=None,f1_score=None,
                total_detected_events=len(events),fp_per_negative_hour=None,negative_duration_sec=None,
                id_switches=None,fragmentation=None,annotation_status='NOT VERIFIED')
    if not gt or gt.get('video_metadata',{}).get('sha256') != video_hash:
        return dict(metrics=base,eval_details=[],status='NOT VERIFIED',reason='Missing GT or video hash mismatch')
    subjects = gt.get('subjects',[])
    truth = [(s['subject_id'],e) for s in subjects for e in s.get('events',[s.get('fall_event')]) if e]
    if any(not isinstance(e.get('impact_sec'), (int,float)) or not np.isfinite(e['impact_sec'])
           or not isinstance(e.get('slip_start_sec'), (int,float)) or not np.isfinite(e['slip_start_sec'])
           for _,e in truth):
        return dict(metrics=base,eval_details=[],status='NOT VERIFIED',reason='Event annotation is incomplete or non-finite')
    base['total_ground_truth_falls'] = len(truth)
    intervals = sorted(gt.get('negative_intervals',[]))
    merged = []
    for start,end in intervals:
        if end <= start:
            raise ValueError('Invalid negative interval')
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end,merged[-1][1])
        else:
            merged.append([start,end])
    duration = sum(b-a for a,b in merged)
    neg_fp = sum(any(a <= e['confirmed_pts'] < b for a,b in merged) for e in events)
    base.update(negative_duration_sec=duration,fp_per_negative_hour=neg_fp*3600/duration if duration and complete else None)
    mapping = [subject_for(e['bbox'],e['confirmed_pts'],subjects) for e in events]
    spatial_labels_present = bool(subjects) and all(s.get('bbox_samples') for s in subjects)
    # With zero predictions, every retained GT event is missed; identity mapping
    # is not needed to count FN. Annotation quality still remains NOT VERIFIED.
    ready = complete and (not events or spatial_labels_present)
    if not ready:
        return dict(metrics=base,eval_details=[dict(event_id=e['event_id'],track_id=e['track_id'],subject_id=m[0],spatial_iou=m[1],type='UNMATCHED_NOT_VERIFIED') for e,m in zip(events,mapping)],
                    status='NOT VERIFIED',reason='Missing spatial annotation or incomplete playback')
    cost = np.full((len(events),len(truth)),1e6)
    for row,(event,mapped) in enumerate(zip(events,mapping)):
        for col,(sid,actual) in enumerate(truth):
            delta = event['confirmed_pts']-actual['impact_sec']
            if mapped[0]==sid and -.5 <= delta <= 3.0:
                cost[row,col] = abs(delta)
    pairs = {}
    if cost.size:
        rows,cols = linear_sum_assignment(cost)
        pairs = {int(r):int(c) for r,c in zip(rows,cols) if cost[r,c]<1e6}
    details = []
    for row,event in enumerate(events):
        item = dict(event_id=event['event_id'],track_id=event['track_id'],subject_id=mapping[row][0],spatial_iou=mapping[row][1],
                    type='TP' if row in pairs else 'FP',detected_at_pts=event['confirmed_pts'])
        if row in pairs:
            _,actual = truth[pairs[row]]
            item.update(gt_event_id=actual['event_id'],latencies=dict(
                slip_to_confirmed=event['confirmed_pts']-actual['slip_start_sec'],
                impact_to_confirmed=event['confirmed_pts']-actual['impact_sec'],
                suspected_to_confirmed=event['confirmed_pts']-event['suspected_pts']))
        details.append(item)
    for col,(sid,actual) in enumerate(truth):
        if col not in pairs.values():
            details.append(dict(type='FN',subject_id=sid,gt_event_id=actual['event_id']))
    tp,fp,fn = len(pairs),len(events)-len(pairs),len(truth)-len(pairs)
    base.update(true_positives=tp,false_positives=fp,false_negatives=fn,
                precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
                f1_score=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                annotation_status=gt.get('annotation_status','NOT VERIFIED'))
    if trace and spatial_labels_present:
        previous, seen, fragments, switches = {},{},0,0
        for row in trace:
            for t in row['tracks']:
                sid,_ = subject_for(t['bbox'],row['pts'],subjects)
                if sid is None:
                    continue
                if sid in previous and previous[sid] != t['track_id']:
                    switches += 1
                seen.setdefault(sid,set()).add(t['track_id'])
                previous[sid] = t['track_id']
        fragments = sum(max(0,len(ids)-1) for ids in seen.values())
        base.update(id_switches=switches,fragmentation=fragments)
    return dict(metrics=base,eval_details=details,status='EVALUATED' if gt.get('annotation_status')=='ADJUDICATED' else 'NOT VERIFIED',
                reason='One-to-one subject + impact window matching; annotation quality reported separately')
