"""Local-only fall observation backends; no downloads during application startup."""
import hashlib
import os
from pathlib import Path

import cv2
import numpy as np

COCO_TO_MEDIAPIPE = {0: 0, 1: 2, 2: 5, 3: 7, 4: 8, 5: 11, 6: 12,
                     7: 13, 8: 14, 9: 15, 10: 16, 11: 23, 12: 24,
                     13: 25, 14: 26, 15: 27, 16: 28}
ROOT = Path(__file__).resolve().parents[3]


class ForegroundAssistedBackend:
    """Measured foreground supports previously detected people, never creates people.

    This is experimental shape evidence, not a calibrated person detector. No scene
    ROI is used. Global camera motion, ambiguous nearby objects and stale seeds are
    rejected. Missing points are never carried forward as measured anatomy.
    """
    def __init__(self, detector, support_seconds=2.0):
        self.detector = detector
        self.background = cv2.createBackgroundSubtractorMOG2(history=400, varThreshold=25,
                                                            detectShadows=False)
        self.seeds = []
        self.last_pts = None
        self.support_seconds = support_seconds
        self.metadata = dict(backend='foreground-assisted', detector=getattr(detector, 'metadata', {}),
                             support_seconds=support_seconds, scene_roi=None)

    def observe(self, packet):
        from features.fall_risk.pipeline.fall_pipeline import PersonObservation, iou
        pts = packet.pts_sec
        if self.last_pts is not None and pts <= self.last_pts:
            raise ValueError('Foreground backend requires increasing PTS')
        mask = self.background.apply(packet.image)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3,3),np.uint8))
        mask = cv2.dilate(mask, np.ones((5,5),np.uint8))
        h,w = packet.image.shape[:2]
        candidates = []
        for contour in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            if cv2.contourArea(contour) < max(100, h*w*.0003):
                continue
            x,y,bw,bh = cv2.boundingRect(contour)
            candidates.append((x/w,y/h,bw/w,bh/h))
        detected = self.detector.observe(packet)
        seeds = [s for s in self.seeds if pts-s['detected_pts'] <= self.support_seconds]
        used = set()
        for obs in detected:
            choices = [(iou(obs.bbox,s['bbox']),i) for i,s in enumerate(seeds) if i not in used]
            best = max(choices, default=(0,None))
            if best[0] > .15:
                s=seeds[best[1]];s.update(bbox=obs.bbox,pts=pts,detected_pts=pts,score=obs.detector_confidence)
                used.add(best[1])
            else:
                seeds.append(dict(bbox=obs.bbox,pts=pts,detected_pts=pts,score=obs.detector_confidence))
                used.add(len(seeds)-1)
        output = list(detected)
        moving_camera = np.count_nonzero(mask)/(h*w) > .35
        if not moving_camera and self.last_pts is not None and pts-self.last_pts <= .25:
            consumed = set()
            for index,seed in enumerate(seeds):
                if index in used or seed['score'] < .5:
                    continue
                a=seed['bbox'];ac=np.array(a[:2])+np.array(a[2:])/2
                choices=[]
                for ci,b in enumerate(candidates):
                    if ci in consumed or any(iou(b,o.bbox)>.15 for o in detected):
                        continue
                    bc=np.array(b[:2])+np.array(b[2:])/2
                    distance=np.linalg.norm(ac-bc)/max(a[3],.01)
                    area_ratio=b[2]*b[3]/max(a[2]*a[3],1e-6)
                    if distance <= .6 and .15 <= area_ratio <= 1.8 and iou(a,b) >= .1:
                        choices.append((iou(a,b)-.2*distance,ci,b))
                choices.sort(reverse=True)
                if not choices or len(choices)>1 and choices[0][0]-choices[1][0]<.1:
                    continue
                _,ci,b=choices[0];consumed.add(ci)
                output.append(PersonObservation(b,seed['score'],None,pts,evidence_source='silhouette'))
                seed.update(bbox=b,pts=pts)
        self.seeds=seeds;self.last_pts=pts
        return output

    def close(self):
        self.detector.close()


def coco_to_mediapipe(points):
    """Map real observations only; unmeasured joints retain zero visibility."""
    points = np.asarray(points, dtype=float)
    if points.shape != (17, 3):
        raise ValueError('Expected COCO keypoints with shape (17, 3)')
    result = np.zeros((33, 3), dtype=float)
    for source, target in COCO_TO_MEDIAPIPE.items():
        if np.isfinite(points[source]).all():
            result[target] = points[source]
    return result


class OnnxPoseBackend:
    """CPU YOLO11 pose inference with dynamic letterbox and explicit COCO NMS.

    No Ultralytics/PyTorch import is needed in this backend. Accepts only the
    exported single-class 17-keypoint contract, not arbitrary ONNX architectures.
    """
    def __init__(self, weights, image_size=640, confidence=.25):
        path=Path(weights).resolve()
        if not path.is_file():raise FileNotFoundError(f'Local ONNX weights missing: {path}')
        if image_size < 32 or image_size % 32:raise ValueError('image_size must be a positive multiple of 32')
        import onnxruntime as ort
        options=ort.SessionOptions()
        options.intra_op_num_threads=int(os.environ.get('FALL_INFERENCE_THREADS','4'))
        options.inter_op_num_threads=1
        if options.intra_op_num_threads < 1:raise ValueError('FALL_INFERENCE_THREADS must be positive')
        self.session=ort.InferenceSession(str(path),options,providers=['CPUExecutionProvider'])
        self.input=self.session.get_inputs()[0]
        self.image_size,self.confidence=image_size,confidence
        self.metadata=dict(backend='onnx-yolo11-pose',weights=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           image_size=image_size,confidence=confidence,device='cpu',keypoint_layout='mediapipe-33-mapped-coco-17')

    def observe(self,packet):
        from features.fall_risk.pipeline.fall_pipeline import PersonObservation
        h,w=packet.image.shape[:2];scale=min(self.image_size/h,self.image_size/w)
        rw,rh=round(w*scale),round(h*scale)
        dynamic=not isinstance(self.input.shape[2],int) or not isinstance(self.input.shape[3],int)
        dw,dh=self.image_size-rw,self.image_size-rh
        if dynamic:dw,dh=dw%32,dh%32
        left,top=round(dw/2-.1),round(dh/2-.1)
        image=cv2.resize(packet.image,(rw,rh))
        image=cv2.copyMakeBorder(image,top,round(dh/2+.1),left,round(dw/2+.1),cv2.BORDER_CONSTANT,value=(114,114,114))
        tensor=np.ascontiguousarray(image[:,:,::-1].transpose(2,0,1)[None],dtype=np.float32)/255.
        output=self.session.run(None,{self.input.name:tensor})[0]
        if output.ndim!=3 or output.shape[0]!=1 or output.shape[1]!=56:
            raise ValueError(f'Unsupported ONNX pose output shape: {output.shape}; expected (1,56,N)')
        detections=output[0].T
        detections=detections[np.isfinite(detections).all(axis=1)&(detections[:,4]>=self.confidence)]
        if not len(detections):return []
        boxes=detections[:,:4].copy()
        boxes[:,:2]-=boxes[:,2:]/2
        keep=cv2.dnn.NMSBoxes(boxes.tolist(),detections[:,4].tolist(),self.confidence,.7)
        observations=[]
        for index in np.asarray(keep).reshape(-1):
            row=detections[index];x,y,bw,bh=boxes[index]
            x0,y0=max(0.,(x-left)/scale),max(0.,(y-top)/scale)
            x1,y1=min(w,(x+bw-left)/scale),min(h,(y+bh-top)/scale)
            if x1<=x0 or y1<=y0:continue
            points=row[5:].reshape(17,3).copy()
            points[:,:2]=(points[:,:2]-[left,top])/scale/[w,h]
            observations.append(PersonObservation((x0/w,y0/h,(x1-x0)/w,(y1-y0)/h),row[4],coco_to_mediapipe(points),packet.pts_sec))
        return observations

    def close(self):
        self.session=None


class YoloPoseBackend:
    """Multi-person COCO pose, mapped to the pipeline's named landmark contract."""
    def __init__(self, weights, image_size=640, confidence=0.25, device='cpu', hybrid=False):
        path = Path(weights).resolve()
        if not path.is_file():
            raise FileNotFoundError(f'Local fall pose weights missing: {path}')
        cache = ROOT / '.cache' / 'ultralytics'
        cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault('YOLO_CONFIG_DIR', str(cache))
        os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
        from ultralytics import YOLO
        import torch
        threads = int(os.environ.get('FALL_INFERENCE_THREADS', '4'))
        if threads < 1:
            raise ValueError('FALL_INFERENCE_THREADS must be positive')
        torch.set_num_threads(threads)
        self.model = YOLO(str(path), task='pose')
        self.image_size, self.confidence, self.device = image_size, confidence, device
        self.pose = None
        if hybrid:
            import mediapipe as mp
            self.pose = mp.solutions.pose.Pose(static_image_mode=True, model_complexity=1,
                                              min_detection_confidence=0.5)
        self.metadata = dict(backend='yolo-mediapipe' if hybrid else 'yolo-pose',
                             weights=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                             image_size=image_size, confidence=confidence, device=device,
                             keypoint_layout='mediapipe-33-mapped-coco-17')

    def observe(self, packet):
        from features.fall_risk.pipeline.fall_pipeline import PersonObservation
        h, w = packet.image.shape[:2]
        result = self.model.predict(packet.image, imgsz=self.image_size, conf=self.confidence,
                                    device=self.device, classes=[0], verbose=False)[0]
        observations = []
        if result.boxes is None or result.keypoints is None:
            return observations
        boxes = result.boxes.xyxy.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        points = result.keypoints.data.cpu().numpy()
        for box, score, kp in zip(boxes, scores, points):
            x0, y0, x1, y1 = np.clip(box, [0, 0, 0, 0], [w, h, w, h])
            if x1 <= x0 or y1 <= y0:
                continue
            normalized = kp.copy()
            normalized[:, :2] /= [w, h]
            mapped = coco_to_mediapipe(normalized)
            if self.pose is not None:
                # Detector proposals remain from YOLO; compare pose extraction separately.
                bw, bh = x1-x0, y1-y0
                cx0, cy0 = max(0, int(x0-.15*bw)), max(0, int(y0-.15*bh))
                cx1, cy1 = min(w, int(x1+.15*bw)), min(h, int(y1+.15*bh))
                crop = packet.image[cy0:cy1, cx0:cx1]
                pose = self.pose.process(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                mapped = None if not pose.pose_landmarks else np.array([
                    ((p.x*(cx1-cx0)+cx0)/w, (p.y*(cy1-cy0)+cy0)/h, p.visibility)
                    for p in pose.pose_landmarks.landmark])
            observations.append(PersonObservation(((x0/w), (y0/h), (x1-x0)/w, (y1-y0)/h),
                                                  float(score), mapped, packet.pts_sec))
        return observations

    def close(self):
        if self.pose is not None:
            self.pose.close()
            self.pose = None


def create_pose_backend():
    """Explicit backend selection; auto uses installed local weights, else HOG."""
    choice = os.environ.get('FALL_POSE_BACKEND', 'auto').lower()
    if choice not in ('auto', 'hog', 'yolo', 'yolo-mediapipe', 'yolo-silhouette', 'onnx', 'hfd', 'hfd-pose'):
        raise ValueError(f'Unsupported FALL_POSE_BACKEND: {choice}')
    if choice in ('hfd', 'hfd-pose'):
        from features.fall_risk.hfd_model.hfd_pose_backend import HfdPoseBackend
        backend = HfdPoseBackend()
        backend.use_hfd_classifier = choice == 'hfd'
        return backend
    configured = os.environ.get('FALL_POSE_WEIGHTS')
    import importlib.util
    use_onnx = choice == 'onnx' or choice == 'auto' and importlib.util.find_spec('onnxruntime') is not None
    suffix = '.onnx' if use_onnx else '.pt'
    # Frozen resource location is resolved only when needed.
    path = Path(configured) if configured else ROOT / ('features/fall_risk/hfd_model/models/yolo11n-pose'+suffix)
    if not path.is_file() and not configured:
        import sys
        path = Path(getattr(sys, '_MEIPASS', ROOT)) / ('features/fall_risk/hfd_model/models/yolo11n-pose'+suffix)
    if choice == 'auto' and not path.is_file() and not configured and use_onnx:
        path = path.with_suffix('.pt')
    if choice == 'hog' or (choice == 'auto' and not path.is_file()):
        from features.fall_risk.pipeline.fall_pipeline import PersonPoseBackend
        return PersonPoseBackend()
    if choice == 'onnx' or choice == 'auto' and path.suffix == '.onnx':
        return OnnxPoseBackend(path, image_size=int(os.environ.get('FALL_POSE_IMAGE_SIZE','640')))
    if choice == 'auto':
        import importlib.util
        if importlib.util.find_spec('ultralytics') is None:
            from features.fall_risk.pipeline.fall_pipeline import PersonPoseBackend
            backend = PersonPoseBackend()
            backend.metadata = {'backend':'hog-mediapipe','degraded_reason':'Optional ultralytics package missing'}
            return backend
    backend = YoloPoseBackend(path, image_size=int(os.environ.get('FALL_POSE_IMAGE_SIZE', '640')),
                           device=os.environ.get('FALL_POSE_DEVICE', 'cpu'),
                           hybrid=choice == 'yolo-mediapipe')
    return ForegroundAssistedBackend(backend) if choice == 'yolo-silhouette' else backend
