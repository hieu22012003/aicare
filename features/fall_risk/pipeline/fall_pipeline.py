"""Shared experimental fall core. No patient storage, identity inference or SMTP.

HOG is a CPU reference person detector, NOT a validated fallen-person model.
All thresholds are provisional, independent of the porch regression scene.
"""
from dataclasses import asdict, dataclass, field
from collections import deque
import math
import uuid
import numpy as np
import cv2
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class FramePacket:
    source_id: str
    mode: str
    frame_index: int
    pts_sec: float
    image: np.ndarray
    run_id: str


@dataclass
class PersonObservation:
    bbox: tuple  # normalized x,y,w,h
    detector_confidence: float  # uncalibrated HOG margin, not event probability
    keypoints: object = None  # normalized image x,y,visibility (33,3)
    pts_sec: float = 0.0
    identity_uncertain: bool = False
    evidence_source: str = 'pose'

    def __post_init__(self):
        self.bbox = tuple(float(x) for x in self.bbox)
        self.detector_confidence = float(self.detector_confidence)
        self.pts_sec = float(self.pts_sec)
        if len(self.bbox) != 4 or not all(math.isfinite(x) for x in self.bbox) or self.bbox[2] <= 0 or self.bbox[3] <= 0:
            raise ValueError('Observation bbox must have finite coordinates and positive dimensions')


@dataclass(frozen=True)
class FallConfig:
    suspect_seconds: float = 0.5
    confirm_seconds: float = 0.8
    ttl_seconds: float = 1.2
    max_observation_gap: float = 0.25
    recovery_seconds: float = 1.0
    cooldown_seconds: float = 2.0
    transition_window: float = 1.0
    min_visibility: float = 0.5
    min_downward_speed: float = 0.6  # person heights / second
    max_center_distance: float = 0.8  # person heights
    ambiguity_margin: float = 0.10
    partial_pose: bool = True
    shape_fallback: bool = True
    motion_window: float = 2.0
    min_shape_confidence: float = 0.50
    min_hip_displacement: float = .20


@dataclass
class FallEvent:
    event_id: str
    run_id: str
    track_id: str
    episode: int
    suspected_pts: float
    confirmed_pts: float
    bbox: tuple
    evidence: dict
    person_identity: object = None
    notification_status: str = 'detected'
    confidence: object = None  # event probability has not been calibrated
    reason: str = 'Observed upright-to-low transition with downward motion'
    snapshot_path: object = None
    mock_email_content: object = None

    @property
    def start_pts(self):
        return self.suspected_pts

    @property
    def latency_sec(self):
        return self.confirmed_pts - self.suspected_pts


@dataclass
class CheckNotice:
    notice_id: str
    track_id: str
    pts_sec: float
    reason: str
    state: str = 'NEEDS_CHECK'
    confirmed_fall: bool = False
    person_identity: object = None
    resolved: bool = False


def iou(a, b):
    x = max(a[0], b[0]); y = max(a[1], b[1])
    w = max(0, min(a[0]+a[2], b[0]+b[2])-x)
    h = max(0, min(a[1]+a[3], b[1]+b[3])-y)
    intersection = w*h
    return intersection / max(1e-9, a[2]*a[3]+b[2]*b[3]-intersection)


class PersonPoseBackend:
    """Multi-person HOG proposals + stateless MediaPipe pose per crop.

    Stateless pose prevents previous person's landmarks leaking into another crop.
    No model download; installed OpenCV/MediaPipe assets only.
    """
    def __init__(self):
        import mediapipe as mp
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.pose = mp.solutions.pose.Pose(static_image_mode=True, model_complexity=1,
                                         smooth_landmarks=False, min_detection_confidence=0.5)

    def observe(self, packet):
        frame = packet.image
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / w)
        small = cv2.resize(frame, (round(w*scale), round(h*scale)))
        if small.shape[0] < 128 or small.shape[1] < 64:
            return []
        boxes, scores = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        candidates = []
        for box, score in zip(boxes, scores):
            x,y,bw,bh = [float(v)/scale for v in box]
            candidates.append((float(score), (x/w,y/h,bw/w,bh/h)))
        kept = []
        for score, box in sorted(candidates, reverse=True):
            if any(iou(box, other.bbox) > 0.45 for other in kept):
                continue
            x,y,bw,bh = box
            x0,y0 = max(0,int((x-bw*.15)*w)), max(0,int((y-bh*.15)*h))
            x1,y1 = min(w,int((x+bw*1.15)*w)), min(h,int((y+bh*1.15)*h))
            crop = frame[y0:y1,x0:x1]
            if not crop.size:
                continue
            result = self.pose.process(cv2.cvtColor(crop,cv2.COLOR_BGR2RGB))
            kp = None
            if result.pose_landmarks:
                kp = np.array([((p.x*(x1-x0)+x0)/w,(p.y*(y1-y0)+y0)/h,p.visibility)
                               for p in result.pose_landmarks.landmark])
            kept.append(PersonObservation(box,score,kp,packet.pts_sec))
        return kept

    def close(self):
        self.pose.close()


@dataclass
class TrackState:
    track_id: str
    bbox: tuple
    last_seen_pts: float
    observations: object = field(default_factory=lambda: deque(maxlen=180))
    velocity: tuple = (0.,0.)
    visibility_state: str = 'UNOBSERVABLE'
    fall_state: str = 'UNOBSERVABLE'
    features: dict = field(default_factory=dict)
    suspected_pts: object = None
    confirming_pts: object = None
    upright_pts: object = None
    recovery_pts: object = None
    confirmed_pts: object = None
    episode: int = 0
    episode_open: bool = False
    identity_uncertain: bool = False
    upright_height: object = None
    last_torso_pts: object = None
    baseline_hip_y: object = None
    baseline_pts: object = None
    motion_pts: object = None
    needs_check: bool = False
    notice_open: bool = False


class FallPipeline:
    def __init__(self, backend=None, config=None, run_id=None):
        if backend is None:
            from features.fall_risk.pipeline.fall_backends import create_pose_backend
            backend = create_pose_backend()
        self.backend = backend
        self.config = config or FallConfig()
        self.run_id = run_id or uuid.uuid4().hex
        self.tracks = {}
        self.events = []
        self.check_notices = []
        self.transitions = []
        self.last_pts = None
        self.next_id = 1
        self.audit = []
        self.diagnostics = deque(maxlen=1800)
        self.classifier = None
        if getattr(backend, 'use_hfd_classifier', False):
            import os
            from features.fall_risk.hfd_model.hfd_classifier import HfdClassifier
            try:
                self.classifier = HfdClassifier(os.environ.get('FALL_HFD_LSTM_WEIGHTS'),
                                                max_gap=self.config.max_observation_gap)
            except Exception:
                self.backend.close()
                raise
            self.backend.metadata.update(self.classifier.metadata)

    def _transition(self, track, state, pts, reason):
        if state != track.fall_state:
            self.transitions.append(dict(track_id=track.track_id, pts=pts, previous=track.fall_state,
                                         state=state, reason=reason, features=dict(track.features)))
            track.fall_state = state

    def _unobservable(self, track, pts, reason):
        if self.classifier is not None:
            self.classifier.reset_track(track.track_id)
        suspicious = track.fall_state in ('SUSPECTED', 'CONFIRMING')
        center = (track.bbox[0]+track.bbox[2]/2, track.bbox[1]+track.bbox[3]/2)
        interior_loss = reason == 'not observed in current frame' and len(track.observations)>=3 and track.last_torso_pts is not None and pts-track.last_seen_pts >= .3 and .05 < center[0] < .95 and .05 < center[1] < .95
        if suspicious or interior_loss:
            self._check_notice(track, pts, 'Suspicious motion lost visibility' if suspicious else 'Previously observed person lost in monitored area; cause unknown')
        track.visibility_state = 'UNOBSERVABLE'
        track.suspected_pts = track.confirming_pts = track.upright_pts = track.recovery_pts = None
        track.baseline_pts = track.motion_pts = None
        track.needs_check = track.needs_check or suspicious
        self._transition(track, 'UNOBSERVABLE', pts, reason)

    def _check_notice(self, track, pts, reason):
        track.needs_check = True
        if not track.notice_open:
            self.check_notices.append(CheckNotice(f'{self.run_id}:{track.track_id}:check:{len(self.check_notices)+1}',
                                                 track.track_id, pts, reason))
            track.notice_open = True

    def _features(self, track, obs, shape):
        c = self.config
        kp = obs.keypoints
        features = dict(torso_angle=None, aspect_ratio=obs.bbox[2]*shape[1]/max(1e-9,obs.bbox[3]*shape[0]),
                        height_ratio=None, hip_speed=None, floor_distance=None, posture='UNKNOWN', observable=False,
                        detector_confidence=obs.detector_confidence, pose_quality='NONE',
                        lower_body_observable=False, evidence_source='none', hip_y=None,
                        shape_low=False, motion_recent=False)
        height = max(1e-6, obs.bbox[3])
        reference_height = track.upright_height or height
        ratio = height/reference_height
        features['height_ratio'] = ratio

        def point(indices):
            if kp is None or np.shape(kp) != (33, 3):
                return None
            usable = [kp[i, :2] for i in indices if np.isfinite(kp[i]).all()
                      and kp[i, 2] >= c.min_visibility]
            return np.mean(usable, axis=0) if usable else None

        shoulder, hip = point((11,12)), point((23,24))
        knee, ankle = point((25,26)), point((27,28))
        full_pose = kp is not None and np.shape(kp) == (33,3) and all(
            np.isfinite(kp[i]).all() and kp[i,2] >= c.min_visibility
            for i in (11,12,23,24,25,26,27,28))
        torso = shoulder is not None and hip is not None
        features['pose_quality'] = 'FULL' if full_pose else 'TORSO' if torso else 'NONE'
        features['lower_body_observable'] = knee is not None and ankle is not None
        shape_low = ratio < .65 and features['aspect_ratio'] >= 1.0
        features['shape_low'] = shape_low
        features['motion_recent'] = track.motion_pts is not None and obs.pts_sec-track.motion_pts <= c.motion_window
        if c.shape_fallback and obs.evidence_source == 'silhouette' and track.observations:
            previous = track.observations[-1]
            dt = obs.pts_sec-previous.pts_sec
            contraction = (previous.bbox[3]-height)/reference_height/dt if 0 < dt <= c.max_observation_gap else None
            features['height_contraction_speed'] = contraction
            if contraction is not None and contraction >= c.min_downward_speed and shape_low:
                track.motion_pts = obs.pts_sec
                features['motion_recent'] = True
        if not torso or (not c.partial_pose and not full_pose):
            # Bbox support requires a recently measured torso and a genuine size change;
            # detections alone never become a confirmed fall from a lying startup.
            if c.shape_fallback and shape_low and obs.detector_confidence >= c.min_shape_confidence and track.last_torso_pts is not None and obs.pts_sec-track.last_torso_pts <= c.motion_window:
                features.update(posture='LOW', observable=True, evidence_source=obs.evidence_source if obs.evidence_source == 'silhouette' else 'shape')
            return features
        track.last_torso_pts = obs.pts_sec
        dx,dy = (hip-shoulder)*np.array([shape[1],shape[0]])
        angle = math.degrees(math.atan2(abs(dx), max(1e-9,abs(dy))))
        standing_leg = ankle is not None and (ankle[1]-hip[1])/height > .30
        sitting = knee is not None and angle < 35 and abs(knee[1]-hip[1])/height < .18
        bending = angle > 35 and standing_leg and not shape_low
        upright = angle < 30 and not sitting and (standing_leg or abs(hip[1]-shoulder[1])/height >= .20)
        speed = None
        if track.observations:
            prev = track.observations[-1]
            dt = obs.pts_sec-prev.pts_sec
            if prev.keypoints is not None and 0 < dt <= c.max_observation_gap:
                visible = [prev.keypoints[i,1] for i in (23,24) if np.isfinite(prev.keypoints[i]).all() and prev.keypoints[i,2] >= c.min_visibility]
                if visible:
                    speed = float((hip[1]-np.mean(visible))/dt/reference_height)
        if speed is not None and speed >= c.min_downward_speed:
            track.motion_pts = obs.pts_sec
        displacement = None
        if track.baseline_pts is not None and 0 < obs.pts_sec-track.baseline_pts <= c.motion_window:
            displacement = float((hip[1]-track.baseline_hip_y)/reference_height)
            if angle > 55 and displacement >= c.min_hip_displacement:
                track.motion_pts = obs.pts_sec
        features['hip_displacement'] = displacement
        posture = 'LOW' if (angle > 55 and not bending) or shape_low else 'UPRIGHT' if upright else 'BENDING' if bending else 'SITTING' if sitting else 'UNCERTAIN'
        if posture in ('UPRIGHT', 'SITTING') and (speed is None or abs(speed)<.15 or track.baseline_pts is None or obs.pts_sec-track.baseline_pts>c.motion_window):
            # Recent measured scale, not all-time max: approaching/receding changes size.
            track.upright_height = height
            track.baseline_hip_y, track.baseline_pts = float(hip[1]), obs.pts_sec
        features.update(torso_angle=float(angle), hip_speed=speed, hip_y=float(hip[1]),
                        posture=posture, observable=True, evidence_source='pose',
                        motion_recent=track.motion_pts is not None and obs.pts_sec-track.motion_pts <= c.motion_window)
        return features

    def _step(self, track, obs, shape):
        if self.classifier is not None:
            return self._step_hfd(track, obs, shape)
        pts = obs.pts_sec; c = self.config
        track.features = self._features(track,obs,shape)
        track.observations.append(obs)
        f = track.features
        if not f['observable'] or obs.identity_uncertain:
            self._unobservable(track,pts,'missing joints or ambiguous association')
            return
        track.visibility_state = 'OBSERVED'
        if track.episode_open:
            if f['posture'] == 'UPRIGHT':
                if track.recovery_pts is None:
                    track.recovery_pts = pts
                if pts-track.recovery_pts >= c.recovery_seconds:
                    self._transition(track,'COOLDOWN',pts,'observed recovery')
                    if pts-track.confirmed_pts >= c.cooldown_seconds:
                        track.episode_open = False
                        track.suspected_pts = track.confirming_pts = None
                        self._transition(track,'RECOVERED',pts,'episode ended')
            else:
                track.recovery_pts = None
                self._transition(track,'FALL_CONFIRMED',pts,'same episode; no duplicate')
            return
        if f['posture'] in ('UPRIGHT', 'SITTING'):
            track.upright_pts = pts
            if track.needs_check:
                if track.recovery_pts is None:
                    track.recovery_pts = pts
                if pts-track.recovery_pts >= c.recovery_seconds:
                    track.needs_check = False
                    track.notice_open = False
                    for notice in self.check_notices:
                        if notice.track_id == track.track_id:
                            notice.resolved = True
            else:
                track.recovery_pts = None
        else:
            track.recovery_pts = None
        low = f['posture'] == 'LOW'
        if track.fall_state in ('SUSPECTED','CONFIRMING'):
            if not low:
                track.suspected_pts = track.confirming_pts = None
                self._transition(track,'NORMAL' if f['posture'] != 'UNCERTAIN' else 'UNCERTAIN',pts,'low posture interrupted')
            elif track.fall_state == 'SUSPECTED' and pts-track.suspected_pts >= c.suspect_seconds:
                track.confirming_pts = pts
                self._transition(track,'CONFIRMING',pts,'continuous low observations')
            elif track.fall_state == 'CONFIRMING' and pts-track.confirming_pts >= c.confirm_seconds:
                track.episode += 1
                track.episode_open = True
                track.confirmed_pts = pts
                self._transition(track,'FALL_CONFIRMED',pts,'continuous confirmation interval')
                self.events.append(FallEvent(f'{self.run_id}:{track.track_id}:{track.episode}',self.run_id,track.track_id,
                                             track.episode,track.suspected_pts,pts,track.bbox,dict(f)))
            return
        transition = track.upright_pts is not None and pts-track.upright_pts <= c.motion_window
        if low and transition and f['motion_recent']:
            track.suspected_pts = pts
            self._transition(track,'SUSPECTED',pts,'upright-to-low with downward hip motion')
        else:
            if low:
                self._check_notice(track, pts, 'Low/on-floor posture without a verified fall transition')
            self._transition(track,'ON_FLOOR_UNCERTAIN' if low else 'UNCERTAIN' if f['posture']=='UNCERTAIN' else 'NORMAL',pts,'no observed fall transition')

    def _step_hfd(self, track, obs, shape):
        """LSTM temporal policy; scores are uncalibrated, never patient identity."""
        pts, c = obs.pts_sec, self.config
        if not hasattr(obs,'model_bbox'):
            x,y,w,h=obs.bbox
            obs.model_bbox=((x*shape[1],y*shape[0]),((x+w)*shape[1],(y+h)*shape[0]))
        track.features = self._features(track, obs, shape)
        track.observations.append(obs)
        prediction = self.classifier.observe(track.track_id, obs)
        track.features['hfd'] = prediction
        f = track.features
        if not prediction['valid'] or obs.identity_uncertain:
            self._unobservable(track, pts, prediction.get('reason', 'Invalid HFD evidence'))
            return
        track.visibility_state = 'OBSERVED'
        angle = abs(prediction['angle_vertical'])
        upright = angle < math.pi/4
        candidate = prediction['ready'] and prediction['class_id'] == 0 and prediction['fall_score'] >= .5 and not upright
        if upright:
            track.upright_pts = pts
        if track.episode_open:
            if upright and prediction['class_id'] != 0:
                if track.recovery_pts is None:track.recovery_pts = pts
                if pts-track.recovery_pts >= c.recovery_seconds and pts-track.confirmed_pts >= c.cooldown_seconds:
                    track.episode_open = False
                    track.suspected_pts = track.confirming_pts = None
                    self._transition(track,'RECOVERED',pts,'HFD observed recovery')
            else:
                track.recovery_pts = None
            return
        if upright:
            track.suspected_pts = track.confirming_pts = None
            if track.needs_check:
                if track.recovery_pts is None:track.recovery_pts = pts
                if pts-track.recovery_pts >= c.recovery_seconds:
                    track.needs_check = track.notice_open = False
                    for notice in self.check_notices:
                        if notice.track_id == track.track_id:notice.resolved=True
            self._transition(track,'NORMAL' if prediction['ready'] else 'WARMING_UP',pts,prediction['reason'])
            return
        track.recovery_pts = None
        if not prediction['ready']:
            self._transition(track,'WARMING_UP',pts,'HFD recurrent warm-up')
            return
        if candidate and (track.suspected_pts is not None or track.upright_pts is not None and pts-track.upright_pts <= c.motion_window):
            if track.suspected_pts is None:
                track.suspected_pts = pts
                self._transition(track,'SUSPECTED',pts,'HFD fall class after observed upright posture')
            elif track.confirming_pts is None and pts-track.suspected_pts >= c.suspect_seconds:
                track.confirming_pts = pts
                self._transition(track,'CONFIRMING',pts,'Persistent HFD fall evidence')
            elif track.confirming_pts is not None and pts-track.confirming_pts >= c.confirm_seconds:
                track.episode += 1;track.episode_open=True;track.confirmed_pts=pts
                self._transition(track,'FALL_CONFIRMED',pts,'HFD continuous temporal confirmation')
                self.events.append(FallEvent(f'{self.run_id}:{track.track_id}:{track.episode}', self.run_id,
                    track.track_id,track.episode,track.suspected_pts,pts,track.bbox,dict(f),
                    reason='HFD recurrent fall class after observed upright posture; trial-only'))
        else:
            track.suspected_pts = track.confirming_pts = None
            if candidate or f['posture']=='LOW':
                self._check_notice(track,pts,'Low/HFD fall-like posture without a confirmed temporal transition')
                self._transition(track,'ON_FLOOR_UNCERTAIN',pts,'Unverified fall transition')
            else:
                self._transition(track,'NORMAL',pts,'HFD non-fall class')

    def process(self, packet, observations=None):
        pts = packet.pts_sec
        if packet.run_id != self.run_id:
            raise ValueError('Frame run_id does not match pipeline')
        if not math.isfinite(pts) or pts < 0 or (self.last_pts is not None and pts <= self.last_pts):
            raise ValueError('PTS must increase; restart/seek requires a new pipeline')
        self.last_pts = pts
        obs = self.backend.observe(packet) if observations is None else observations
        obs = sorted(obs,key=lambda o: o.bbox)
        for o in obs:
            o.pts_sec = pts
        for tid,t in list(self.tracks.items()):
            if pts-t.last_seen_pts > self.config.ttl_seconds:
                self.audit.append(dict(pts=pts,track_id=tid,type='LOST'))
                del self.tracks[tid]
        tracks = list(self.tracks.values())
        costs = np.full((len(tracks),len(obs)),1e6)
        for row,t in enumerate(tracks):
            dt = pts-t.last_seen_pts
            predicted = (t.bbox[0]+t.velocity[0]*dt,t.bbox[1]+t.velocity[1]*dt,*t.bbox[2:])
            for col,o in enumerate(obs):
                ac = np.array(predicted[:2])+np.array(predicted[2:])/2
                bc = np.array(o.bbox[:2])+np.array(o.bbox[2:])/2
                dist = np.linalg.norm(ac-bc)/max(t.bbox[3],o.bbox[3],.01)
                if self.classifier is not None and t.observations:
                    previous=t.observations[-1].keypoints
                    current=o.keypoints
                    if previous is not None and current is not None and np.shape(previous)==np.shape(current)==(33,3):
                        indices=[k for k in (11,12,23,24) if np.isfinite(previous[k]).all()
                                 and np.isfinite(current[k]).all() and min(previous[k,2],current[k,2])>=.15]
                        if indices:
                            pose_distance=float(np.mean(np.linalg.norm(previous[indices,:2]-current[indices,:2],axis=1)))/max(t.bbox[3],o.bbox[3],t.upright_height or 0.,.01)
                            if pose_distance<=self.config.max_center_distance:
                                costs[row,col]=.65*pose_distance+.20*min(dist,self.config.max_center_distance)+.15*(1-iou(predicted,o.bbox))
                                continue
                if dist <= self.config.max_center_distance:
                    costs[row,col] = .55*(1-iou(predicted,o.bbox))+.40*dist+.05/(1+max(0,o.detector_confidence))
        matched_t, matched_o = set(),set()
        if costs.size:
            rows,cols = linear_sum_assignment(costs)
            for row,col in zip(rows,cols):
                if costs[row,col] >= 1e6:
                    continue
                alternatives = [v for j,v in enumerate(costs[row]) if j != col] + [v for i,v in enumerate(costs[:,col]) if i != row]
                if alternatives and min(alternatives)-costs[row,col] < self.config.ambiguity_margin:
                    obs[col].identity_uncertain = True
                    self.audit.append(dict(pts=pts,track_id=tracks[row].track_id,type='identity_uncertain'))
                    continue
                t,o = tracks[row],obs[col]
                dt = pts-t.last_seen_pts
                if dt > self.config.max_observation_gap:
                    self._unobservable(t,pts,'observation gap; cancel confirmation')
                # Track center motion; bbox top/left jumps when posture changes.
                delta = [(o.bbox[k]+o.bbox[k+2]/2-t.bbox[k]-t.bbox[k+2]/2)/dt for k in (0,1)]
                t.velocity = tuple(.7*t.velocity[k]+.3*delta[k] for k in (0,1))
                t.bbox = o.bbox; t.last_seen_pts = pts
                t.identity_uncertain = o.identity_uncertain
                self._step(t,o,packet.image.shape)
                matched_t.add(t.track_id); matched_o.add(col)
        for t in tracks:
            if t.track_id not in matched_t:
                self._unobservable(t,pts,'not observed in current frame')
        for col,o in enumerate(obs):
            if col not in matched_o:
                tid = f'T{self.next_id}'; self.next_id += 1
                t = TrackState(tid,o.bbox,pts,identity_uncertain=o.identity_uncertain)
                self.tracks[tid] = t
                self._step(t,o,packet.image.shape)
        self.diagnostics.append(dict(pts=pts, frame_index=packet.frame_index,
                                     detections=len(obs), tracks=[dict(track_id=t.track_id,
                                     state=t.fall_state, features=dict(t.features)) for t in self.tracks.values()
                                     if t.last_seen_pts == pts]))
        if self.classifier is not None:
            self.classifier.retain(self.tracks)
        return self.summaries(pts)

    def summaries(self, pts):
        return [dict(track_id=t.track_id,bbox=t.bbox,state=t.fall_state,posture=t.fall_state,
                     ar=t.features.get('aspect_ratio'),cy=t.bbox[1]+t.bbox[3]/2,
                     is_fallen=t.fall_state=='FALL_CONFIRMED',person_identity=None,
                     identity_uncertain=t.identity_uncertain,features=t.features,needs_check=t.needs_check)
                for t in self.tracks.values() if t.last_seen_pts == pts]

    def render(self, frame, summaries):
        from shared.camera.drawing_utils import draw_vietnamese_text
        out = frame.copy(); h,w = out.shape[:2]
        for t in summaries:
            x,y,bw,bh = t['bbox']; x,y,bw,bh = int(x*w),int(y*h),int(bw*w),int(bh*h)
            color = (0,0,230) if t['is_fallen'] else (0,180,240) if t['state'] in ('SUSPECTED','CONFIRMING') else (180,180,180)
            cv2.rectangle(out,(x,y),(x+bw,y+bh),color,2)
            draw_vietnamese_text(out,f"Track {t['track_id']} · {t['state']}",(x,max(0,y-24)),font_size=16,color=color)
        return out

    def close(self):
        if self.classifier is not None:
            self.classifier.close()
        if hasattr(self.backend,'close'):
            self.backend.close()
