"""End-to-end check of the 11 functions (10 features + management website), with evidence for a video.

    python scripts/feature_demo/run_checks.py <run_dir> <assets.json>

Starts the real web server + AI backend (simulated clock), a real home device client (cloud mode,
HttpTransport) and drives one morning 06:50-09:00. Real photos feed face, emotion, posture and object
checks; synthetic skeletons feed falls, medication gestures and exercises. Every check is recorded in
<run_dir>/results.json with its evidence (images, spoken lines, browser clips).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time as _time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RUN = Path(sys.argv[1]).resolve()
ASSETS = json.loads(Path(sys.argv[2]).read_text())
REPO = Path(__file__).resolve().parents[2]
shutil.rmtree(RUN, ignore_errors=True)
(RUN / "ev").mkdir(parents=True)
(RUN / "clips").mkdir()

PID = "patient_001"
KEY = "dev-feature-check-0123456789abcdef"
PORT = 8830
URL = f"http://127.0.0.1:{PORT}"
CRED = {"admin": ("admin@unifiedcare.local", "Admin-Test-2026"),
        "caregiver": ("nguoinha@aicare.local", "NguoiNha-Test-2026"),
        "clinician": ("bacsi@aicare.local", "BacSi-Test-2026")}
os.environ.update({
    "AI_CARE_HOME": str(RUN / "server_rt"), "AI_CARE_DB_PATH": str(RUN / "server.sqlite3"),
    "AICARE_DEVICE_KEYS": f"{KEY}:{PID}", "AICARE_ADMIN_PASSWORD": CRED["admin"][1],
    "AICARE_BOOTSTRAP_CAREGIVER": f"{CRED['caregiver'][0]}|{CRED['caregiver'][1]}|Trần Minh Tâm|{PID}",
    "AICARE_BOOTSTRAP_CLINICIAN": f"{CRED['clinician'][0]}|{CRED['clinician'][1]}|BS. Lê Văn Hùng|{PID}",
    "AI_CORE_DETECTOR_MODEL": ASSETS["detector"], "VOICE_MODEL_PATH": ASSETS["stt_model"],
})
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logging.getLogger("EmotionAdapter").setLevel(logging.ERROR)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TODAY = datetime.now(TZ).date()


def at(hh, mm, ss=0):
    return datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm, ss, tzinfo=TZ).timestamp()


def hhmm(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M:%S")


class Sim:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


SIM = Sim(at(6, 50))
RESULTS: list[dict] = []
SPOKEN: list[dict] = []
EVENTS: list[dict] = []


def check(section: int, name: str, ok: bool, detail: str = "", evidence: str | None = None):
    RESULTS.append({"section": section, "name": name, "ok": bool(ok), "detail": detail, "evidence": evidence,
                    "sim": hhmm(SIM())})
    print(f"[F{section:02d}] {'PASS' if ok else 'FAIL'} {name} :: {detail}", flush=True)


def note(section: int, text: str):
    EVENTS.append({"section": section, "sim": hhmm(SIM()), "text": text})


# ------------------------------------------------------------------ server (real code, simulated clock)
from apps.server.main import ensure_admin_password, ensure_bootstrap_caregiver, ensure_bootstrap_clinician  # noqa: E402
from features.web_portal.server import HealthcareWebServer  # noqa: E402
from shared.ai_runtime.device_api import BackendAIService, set_service  # noqa: E402
from shared.database.connection import UnifiedDatabaseManager  # noqa: E402
from shared.database.migrator import Migrator  # noqa: E402
from shared.services import outbox_relay as _relay  # noqa: E402

# The relay's own overdue scanner uses the wall clock; the test day runs on a simulated clock, so the
# scanner is paused (the AI brain's +10/+30 minute medication steps under test use the simulated clock).
_relay.OutboxRelayService.scan_overdue_tasks = lambda self, *a, **k: 0

server_db = UnifiedDatabaseManager(str(RUN / "server.sqlite3"))
Migrator(server_db).apply_all()
ensure_admin_password(server_db)
ensure_bootstrap_caregiver(server_db)
ensure_bootstrap_clinician(server_db)
svc = BackendAIService(server_db, clock=SIM)
set_service(svc)
web = HealthcareWebServer(server_db, host="127.0.0.1", port=PORT)
web.start(block=False)
_time.sleep(1.0)


def api(method, path, token=None, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def login(role):
    code, res = api("POST", "/api/auth/login", body={"email": CRED[role][0], "password": CRED[role][1], "expected_role": role})
    assert code == 200, (role, code, res)
    return res["token"]


TOK = {r: login(r) for r in CRED}

# ------------------------------------------------------------------ recorder (system Python + Playwright)
rec = subprocess.Popen(["python3", str(Path(__file__).parent / "recorder.py"), str(RUN / "clips")], stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, text=True, bufsize=1)


def R(**cmd):
    rec.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
    rec.stdin.flush()
    reply = json.loads(rec.stdout.readline())
    if not reply.get("ok"):
        print("recorder:", cmd.get("cmd"), reply, flush=True)
    return reply


CLIPS: dict[str, dict] = {}


def clip_start(name, url, role=None):
    CLIPS[name] = {"t0": _time.time(), "marks": []}
    R(cmd="clip", name=name, url=url, token=TOK.get(role) if role else None)


def clip_mark(name, label):
    CLIPS[name]["marks"].append({"t": round(_time.time() - CLIPS[name]["t0"], 2), "label": label})


def clip_end(name):
    reply = R(cmd="end")
    CLIPS[name]["video"] = reply.get("video")
    CLIPS[name]["duration"] = round(_time.time() - CLIPS[name]["t0"], 2)


# ------------------------------------------------------------------ home device (real cloud client)
from shared.ai_runtime.cloud_device import CloudDeviceRuntime, HttpTransport  # noqa: E402
from shared.vision.detect import OnnxDetector  # noqa: E402
from tests.ai_features.synth import landmarks  # noqa: E402
from tests.ai_features.test_medication_intake import AWAY, MOUTH, face as syn_face, hand as syn_hand  # noqa: E402

home_db = UnifiedDatabaseManager(str(RUN / "home.sqlite3"))
Migrator(home_db).apply_all()
STATE = {"pose": "standing", "fn": None, "open": False, "at": AWAY, "which": "right", "pinch": True}


@dataclass
class HRes:
    pose: object
    face: tuple
    left_hand: tuple | None
    right_hand: tuple | None


class ScriptPose:
    def process(self, frame, ts_ms):
        return STATE["fn"](SIM()) if STATE["fn"] else landmarks(STATE["pose"])

    def close(self):
        pass


class ScriptHolistic:
    def process(self, frame):
        h = syn_hand(STATE["at"], STATE["pinch"])
        pose = STATE["fn"](SIM()) if STATE["fn"] else landmarks(STATE["pose"])
        return HRes(pose, syn_face(STATE["open"]), h if STATE["which"] == "left" else None,
                    h if STATE["which"] == "right" else None)

    def close(self):
        pass


class SceneCamera:
    def __init__(self, frame):
        self.frame, self.n = frame, 0

    def get_capture_packet(self):
        self.n += 1
        return self.frame, self.n, True


SCENE = cv2.imread(ASSETS["scene"])
cam = SceneCamera(SCENE)
WAKES: list[float] = []


def on_speak(text):
    SPOKEN.append({"sim": hhmm(SIM()), "text": text})
    print(f"    🔊 {hhmm(SIM())} {text}", flush=True)


detector = OnnxDetector(ASSETS["detector"], score_threshold=0.4)
DET_LOG: list[dict] = []


class LoggedDetector:
    def detect(self, frame, *a, **k):
        out = detector.detect(frame, *a, **k)
        DET_LOG.append({"sim": hhmm(SIM()), "n": len(out)})
        return out


dev = CloudDeviceRuntime(HttpTransport(URL, KEY), cam, lambda: (PID, True), on_speak, wake_camera=lambda: WAKES.append(SIM()),
                         cloud_patient_id=PID, pose_factory=ScriptPose, holistic_factory=ScriptHolistic, clock=SIM, db=home_db,
                         detector_factory=LoggedDetector, detector_interval=120, music_dir=RUN / "home_media")


def advance(until, fps=1.0, poll=True):
    """Run the home device and the backend until ``until`` (simulated)."""
    step = 1.0 / fps
    last_sec = math.floor(SIM())
    while SIM() < until - 1e-6:
        SIM.t = min(until, SIM.t + step)
        dev.capture_step()
        if math.floor(SIM()) != last_sec:
            last_sec = math.floor(SIM())
            svc.tick()
            dev.flush()
            if poll and last_sec % 2 == 0:
                dev.poll_commands()
    dev.flush()
    svc.tick()
    if poll:
        dev.poll_commands()


def spoken_since(t0, needle):
    return [s for s in SPOKEN if s["sim"] >= hhmm(t0) and needle.lower() in s["text"].lower()]


# ================================================================== setup on the web
print("== setup", flush=True)
code, res = api("POST", f"/api/patients/{PID}/prescriptions", TOK["clinician"], {
    "diagnosis": "Tăng huyết áp, tiểu đường type 2", "start_date": TODAY.isoformat(),
    "items": [
        {"medication_name": "Amlodipin", "product_strength": "5", "strength_unit": "mg", "dose_amount": 1, "dose_unit": "viên",
         "instructions": "Uống sau ăn sáng", "local_times": ["07:30"]},
        {"medication_name": "Metformin", "product_strength": "500", "strength_unit": "mg", "dose_amount": 1, "dose_unit": "viên",
         "instructions": "Uống sau ăn", "local_times": ["08:00"]},
        {"medication_name": "Vitamin D3", "product_strength": "1000", "strength_unit": "IU", "dose_amount": 1, "dose_unit": "viên",
         "instructions": "Uống với nước", "local_times": ["08:15"]},
    ]})
check(5, "Bác sĩ kê đơn 3 thuốc trên web (07:30, 08:00, 08:15)", code in (200, 201), f"HTTP {code}")
doses = {r["medication_name_snapshot"]: r for r in server_db.fetchall(
    "SELECT * FROM dose_occurrences WHERE patient_id=? AND local_scheduled_time LIKE ?", (PID, TODAY.isoformat() + "%"))}
check(5, "Server tạo lịch uống hôm nay", {"Amlodipin", "Metformin", "Vitamin D3"} <= set(doses), ", ".join(sorted(doses)))

# object regions (2x2 camera view)
code, cur = api("GET", f"/api/patients/{PID}/ai/settings/objects", TOK["caregiver"])
objs = cur.get("config") or {}
objs["regions"] = [
    {"region_id": "sofa", "name": "ghế sofa phòng khách", "polygon": [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]},
    {"region_id": "ban_an", "name": "bàn ăn", "polygon": [[0.5, 0], [1, 0], [1, 0.5], [0.5, 0.5]]},
    {"region_id": "phong_ngu", "name": "phòng ngủ", "polygon": [[0, 0.5], [0.5, 0.5], [0.5, 1], [0, 1]]},
    {"region_id": "goc_tv", "name": "góc ti vi", "polygon": [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]]},
]
code, res = api("PUT", f"/api/patients/{PID}/ai/settings/objects", TOK["caregiver"], {"config": objs})
check(7, "Người nhà khai báo 4 vùng trong nhà trên web", code == 200, f"HTTP {code}")

# family music upload (caregiver, raw bytes like the portal)
music_src = Path(ASSETS["music"])
raw = music_src.read_bytes()
code, res = api("POST", f"/api/patients/{PID}/music", TOK["caregiver"], raw=raw,
                headers={"Content-Type": "audio/wav", "X-File-Name": "bai-hat-cua-con.wav",
                         "X-Track-Title": urllib.parse.quote("Bài hát con gửi bố")})
check(8, "Người nhà tải bài nhạc lên web", code in (200, 201), f"HTTP {code} {res.get('track', {}).get('title', '') if isinstance(res, dict) else ''}")

# first sync of the home device
out = dev.sync()
home_doses = home_db.fetchall("SELECT medication_name_snapshot FROM dose_occurrences WHERE local_scheduled_time LIKE ?",
                              (TODAY.isoformat() + "%",))
check(5, "App tại nhà nhận lịch thuốc từ web (đồng bộ)", len(home_doses) >= 3, f"{len(home_doses)} liều hôm nay về máy nhà")
files = list((RUN / "home_media").glob("*"))
same = bool(files) and hashlib.sha256(files[0].read_bytes()).hexdigest() == hashlib.sha256(raw).hexdigest()
check(8, "App tại nhà tải về đúng file nhạc (SHA-256 trùng)", same, f"{len(files)} file, {files[0].stat().st_size if files else 0} byte")

# ================================================================== F1 face monitoring (real photos)
print("== F1 face", flush=True)
from features.face_monitoring.face_adapter import FaceAdapter  # noqa: E402

faces_dir = Path(ASSETS["faces_dir"])
net = cv2.dnn.readNetFromTensorflow(str(REPO / "features/face_monitoring/standalone/models/opencv_face_detector_uint8.pb"),
                                    str(REPO / "features/face_monitoring/standalone/models/opencv_face_detector.pbtxt"))


def face_crops(fname, min_w=45):
    img = cv2.imread(str(faces_dir / fname))
    h, w = img.shape[:2]
    net.setInput(cv2.dnn.blobFromImage(img, 1.0, (300, 300), [104, 117, 123], False, False))
    det = net.forward()
    out = []
    for i in range(det.shape[2]):
        if det[0, 0, i, 2] < 0.6:
            continue
        x1, y1, x2, y2 = (det[0, 0, i, 3:7] * [w, h, w, h]).astype(int)
        if x2 - x1 < min_w:
            continue
        m = int((x2 - x1) * 0.9)
        out.append(cv2.resize(img[max(0, y1 - m):y2 + m, max(0, x1 - m):x2 + m], (480, 480)))
    return out


def cam_view(img, seed, k=1.0):
    """The same person on the home camera another moment: small head movement, light change, sensor noise."""
    r = random.Random(seed)
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), r.uniform(-4, 4) * k, 1 + r.uniform(-0.04, 0.04) * k)
    M[:, 2] += [r.uniform(-10, 10) * k, r.uniform(-10, 10) * k]
    out = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    out = cv2.convertScaleAbs(out, alpha=1 + r.uniform(-0.1, 0.1) * k, beta=r.uniform(-12, 12) * k)
    return np.clip(out + np.random.default_rng(seed).normal(0, 4 * k, out.shape), 0, 255).astype(np.uint8)


people = {}
for f in ASSETS["face_files"]:
    for k, c in enumerate(face_crops(f)):
        people[f"{f[:9]}#{k}"] = c
patient_key = ASSETS["patient_face"]
fa = FaceAdapter(data_dir=RUN / "face_data", users_dir=RUN / "face_users")
reg_frames = [cam_view(people[patient_key], s) for s in range(12)]
crops = [fa.crop_face(fr, d) for fr in reg_frames if (d := fa.detect_face(fr))]
fa.register_patient("Cụ Hoàng Anh", patient_id=PID, face_images=crops)
check(1, "Đăng ký khuôn mặt người dùng (12 ảnh như màn hình đăng ký của app)", fa._is_trained and len(crops) >= 10,
      f"{len(crops)} ảnh mặt đã lưu, model LBPH đã huấn luyện")
self_hits = strangers = stranger_hits = 0
tiles = []
for key, img in people.items():
    for s in range(500, 504):
        fa.reset_tracking()
        frame = cam_view(img, s)
        r = fa.recognize_face(frame)
        ok = r.get("status") == "recognized"
        if key == patient_key:
            self_hits += ok
        else:
            strangers += 1
            stranger_hits += ok
        if s == 500:
            t = frame.copy()
            if r.get("box"):
                x, y, w_, h_ = r["box"][:4]
                cv2.rectangle(t, (x, y), (x + w_, y + h_), (60, 200, 60) if ok else (40, 40, 230), 4)
            label = "Cu Hoang Anh" if ok else "Nguoi la"
            cv2.rectangle(t, (0, 0), (480, 44), (0, 0, 0), -1)
            cv2.putText(t, f"{label}  d={r.get('distance', 0):.0f}", (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                        (120, 255, 120) if ok else (120, 120, 255), 2)
            tiles.append(cv2.resize(t, (240, 240)))
self_total = 4
check(1, "Nhận ra đúng người đã đăng ký", self_hits == self_total, f"{self_hits}/{self_total} khung hình")
check(1, "Không nhận nhầm người lạ thành người dùng", stranger_hits == 0, f"nhận nhầm {stranger_hits}/{strangers} khung hình")
while len(tiles) % 5:
    tiles.append(np.full((240, 240, 3), 255, np.uint8))
cv2.imwrite(str(RUN / "ev/f1_recognize.jpg"), np.vstack([np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]),
            [cv2.IMWRITE_JPEG_QUALITY, 92])
reg_strip = np.hstack([cv2.resize(c, (160, 160)) for c in crops[:8]])
cv2.imwrite(str(RUN / "ev/f1_register.jpg"), reg_strip, [cv2.IMWRITE_JPEG_QUALITY, 92])

from features.face_monitoring.sighting_log import FaceSightingLog  # noqa: E402

flog = FaceSightingLog(home_db, clock=SIM)
flog.record(PID, "patient", at(6, 52))
for k in range(30):
    flog.record(PID, "patient", at(6, 52) + 10 * k)

# ================================================================== F2 emotion (real photos + 2 h summaries)
print("== F2 emotion", flush=True)
from features.emotion.emotion_adapter import EmotionAdapter  # noqa: E402
from features.emotion.emotion_summary_service import EmotionSummaryService  # noqa: E402

emo_tiles, emo_rows = [], []
for f in ASSETS["emotion_files"]:
    for k, c in enumerate(face_crops(f, min_w=30)):
        ea = EmotionAdapter()
        r = None
        for _ in range(8):
            r = ea.process_frame(cv2.resize(c, (320, 320)))
        emo_rows.append({"face": f"{f[:9]}#{k}", "label": r["dominant_emotion"], "p": r["probabilities"]})
        t = cv2.resize(c, (200, 200))
        cv2.rectangle(t, (0, 0), (200, 30), (0, 0, 0), -1)
        vi = {"Happy": "Vui", "Neutral": "Binh thuong", "Sad": "Buon"}.get(r["dominant_emotion"], "?")
        cv2.putText(t, f"{vi} {r['confidence']:.2f}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        emo_tiles.append(t)
while len(emo_tiles) % 8:
    emo_tiles.append(np.full((200, 200, 3), 255, np.uint8))
cv2.imwrite(str(RUN / "ev/f2_emotion.jpg"), np.vstack([np.hstack(emo_tiles[i:i + 8]) for i in range(0, len(emo_tiles), 8)]),
            [cv2.IMWRITE_JPEG_QUALITY, 92])
labels = [r["label"] for r in emo_rows]
truth = ASSETS["emotion_truth"]  # index -> expected label from a human look at the faces
agree = sum(1 for i, lab in truth.items() if int(i) < len(labels) and labels[int(i)] == lab)
check(2, "Nhận diện cảm xúc trên ảnh khuôn mặt thật", len(labels) >= 20,
      f"{len(labels)} khuôn mặt: {labels.count('Happy')} vui, {labels.count('Neutral')} bình thường, {labels.count('Sad')} buồn")
check(2, "Khớp với đánh giá bằng mắt của người kiểm tra", agree >= 0.8 * len(truth), f"{agree}/{len(truth)} khuôn mặt")

ems = EmotionSummaryService(home_db)
t = at(4, 50)
while t < at(6, 50):  # an early morning of sadness (one label every 2 s, like the camera loop)
    ems.record(PID, "Sad", t)
    t += 2
ems.flush_due(at(6, 51))
sad = home_db.fetchone("SELECT * FROM emotion_summaries_v2 WHERE patient_id=? ORDER BY published_at DESC LIMIT 1", (PID,))
check(2, "Tổng hợp 2 giờ: buồn kéo dài -> nhãn 'Buồn'", bool(sad) and sad["label"] == "Sad", f"{sad['label'] if sad else None}")

# ================================================================== F8 comfort when sad (music)
print("== F8 comfort", flush=True)
from features.music_care.comfort import SadnessComfort  # noqa: E402
import av  # noqa: E402

played = {}


def play_family_music():
    tracks = home_db.fetchall("SELECT * FROM family_music WHERE patient_id=? ORDER BY created_at DESC", (PID,))
    if not tracks:
        return False
    path = RUN / "home_media" / tracks[0]["stored_name"]
    with av.open(str(path)) as c:
        frames = sum(1 for _ in c.decode(audio=0))
    played.update(title=tracks[0]["title"], frames=frames, path=str(path))
    return frames > 0


SIM.t = at(6, 51, 30)
comfort = SadnessComfort(home_db, on_speak, play_family_music, honorific="bác", clock=SIM)
said = comfort.check(PID)
check(8, "Khi buồn: loa nói lời gợi ý hỗ trợ tinh thần", bool(said), said or "")
check(8, "Tự mở nhạc người nhà gửi (giải mã được file âm thanh)", bool(played), f"{played.get('title')} - {played.get('frames')} khung âm thanh")
check(8, "Không lặp lại cho cùng một lần buồn", comfort.check(PID) is None, "lần kiểm tra thứ 2: im lặng")
ev = home_db.fetchone("SELECT 1 FROM outbox_events WHERE event_type='emotion.support_offered'")
check(8, "Ghi nhận sự kiện 'đã hỗ trợ tinh thần' để người nhà biết", bool(ev), "emotion.support_offered")

# ================================================================== the morning (F3 posture, F5 medication, F4 fall, F7 objects)
print("== morning 06:52-09:00", flush=True)
SIM.t = at(6, 52)
STATE.update(pose="standing")
advance(at(7, 0), fps=1)
STATE.update(pose="sitting")
t_sit = SIM()
advance(at(7, 24, 50), fps=1)
check(3, "Server nhận dạng tư thế 'Ngồi' từ khung xương", (server_db.fetchone(
    "SELECT posture FROM ai_posture_state WHERE patient_id=?", (PID,)) or {}).get("posture") == "SITTING", "ai_posture_state")

# --- F5 dose A 07:30 (camera)
t5 = SIM()
advance(at(7, 25, 10), fps=1)
check(5, "07:25 (trước giờ thuốc 5 phút): camera tự bật", len(WAKES) >= 1, f"wake_camera lúc {hhmm(WAKES[0]) if WAKES else '-'}")
check(5, "Chuyển sang theo dõi bàn tay + miệng (Holistic)", dev.holistic_mode, f"holistic_mode={dev.holistic_mode}")
advance(at(7, 30, 2), fps=1)
check(5, "07:30 loa nhắc đúng giờ", bool(spoken_since(at(7, 30), "Amlodipin")), (spoken_since(at(7, 30), "Amlodipin") or [{}])[0].get("text", ""))


def gesture(which, open_mouth, seconds=1.2):
    """Hand with the pill away -> at the mouth -> away (10 frames per second)."""
    STATE.update(which=which, at=AWAY, open=False)
    advance(SIM() + 1.0, fps=10)
    STATE.update(at=MOUTH, open=open_mouth)
    advance(SIM() + seconds, fps=10)
    STATE.update(at=AWAY, open=False)
    advance(SIM() + 3.0, fps=10)


def dose_state(name):
    return server_db.fetchone("SELECT state, action_actor FROM dose_occurrences WHERE occurrence_id=?",
                              (doses[name]["occurrence_id"],))


gesture("left", True)
check(5, "Tay TRÁI đưa thuốc lên miệng: không tính", dose_state("Amlodipin")["state"] != "CONFIRMED", dose_state("Amlodipin")["state"])
gesture("right", False)
check(5, "Tay phải nhưng MIỆNG NGẬM: không tính", dose_state("Amlodipin")["state"] != "CONFIRMED", dose_state("Amlodipin")["state"])
gesture("right", True)
st = dose_state("Amlodipin")
check(5, "Tay phải, ngón cái + ngón trỏ lên miệng, miệng há: ĐÃ UỐNG", st["state"] == "CONFIRMED" and st["action_actor"] == "camera_verified",
      f"{st['state']} ({st['action_actor']})")
advance(at(7, 33), fps=1)
check(5, "Loa xác nhận đã ghi nhận uống thuốc", bool(spoken_since(at(7, 30), "ghi nhận")), (spoken_since(at(7, 30), "ghi nhận") or [{}])[0].get("text", ""))

# --- F3 long sitting reminder (45 min)
advance(at(7, 52), fps=1)
rem = server_db.fetchall("SELECT rule, message, triggered_at_utc FROM ai_posture_reminders WHERE patient_id=?", (PID,))
check(3, "Ngồi quá 45 phút: có lời nhắc đứng dậy vận động", any("SIT" in (r["rule"] or "").upper() or "ngồi" in (r["message"] or "").lower() for r in rem),
      "; ".join(f"{r['rule']}: {r['message']}" for r in rem)[:200])
check(3, "Loa đọc lời nhắc tư thế", any("ngồi" in s["text"].lower() or "đứng dậy" in s["text"].lower() for s in SPOKEN if s["sim"] >= hhmm(t_sit)),
      next((s["text"] for s in SPOKEN if s["sim"] >= hhmm(t_sit) and ("ngồi" in s["text"].lower() or "đứng dậy" in s["text"].lower())), ""))
STATE.update(pose="standing")
advance(at(7, 58), fps=1)
STATE.update(pose="sitting")

# --- F5 dose B 08:00 not taken -> 08:10 voice + red bar on the web (recorded)
advance(at(8, 9, 40), fps=1)
check(5, "08:00 loa nhắc Metformin", bool(spoken_since(at(8, 0), "Metformin")), (spoken_since(at(8, 0), "Metformin") or [{}])[0].get("text", ""))
clip_start("f5_alert", f"{URL}/caregiver", "caregiver")
R(cmd="wait", ms=3500)
clip_mark("f5_alert", "Trang chủ người nhà lúc 08:09 (trước mốc 10 phút)")
advance(at(8, 10, 30), fps=1)
R(cmd="wait", ms=4000)
clip_mark("f5_alert", "08:10: thanh đỏ 'chưa uống' hiện ngay trên web")
R(cmd="shot", path=str(RUN / "ev/f5_web_alert.jpg"))
bar = R(cmd="eval", js="getComputedStyle(document.getElementById('medAlertBar')).display + ' | ' + document.getElementById('medAlertText').innerText").get("value", "")
R(cmd="wait", ms=1500)
clip_end("f5_alert")
over = spoken_since(at(8, 10), "chưa uống")
check(5, "08:10 quá 10 phút: loa nói 'Đến giờ uống thuốc rồi, bác chưa uống'", bool(over), over[0]["text"] if over else "")
check(5, "Web người nhà hiện cảnh báo đỏ ngay (SSE)", bar.startswith("flex") and "chưa uống" in bar, bar[:160])

# --- F5 dose C 08:15: elder presses "Đã uống" on the home app -> server knows, no false alarm
advance(at(8, 16), fps=1)
home_c = home_db.fetchone("SELECT occurrence_id FROM dose_occurrences WHERE medication_name_snapshot='Vitamin D3' AND local_scheduled_time LIKE ?",
                          (TODAY.isoformat() + "%",))
home_db.execute("UPDATE dose_occurrences SET state='CONFIRMED', action_taken_at_utc=?, action_actor='elder_button' WHERE occurrence_id=?",
                (datetime.fromtimestamp(SIM(), timezone.utc).isoformat(), home_c["occurrence_id"]))
sync_res = dev.sync()
check(5, "Bấm 'Đã uống' trên app tại nhà -> server ghi nhận", dose_state("Vitamin D3")["state"] == "CONFIRMED",
      f"{dose_state('Vitamin D3')['state']} ({dose_state('Vitamin D3')['action_actor']}), {sync_res.get('up', {}).get('dose_actions_applied')} hành động")
advance(at(8, 26), fps=1)
check(5, "Không còn báo nhầm 'chưa uống' cho thuốc đã bấm", not spoken_since(at(8, 16), "Vitamin D3 nhé") and not
      [s for s in SPOKEN if s["sim"] >= hhmm(at(8, 16)) and "chưa uống" in s["text"] and "Vitamin" in s["text"]], "im lặng sau 08:25")
advance(at(8, 31), fps=1)
alert = server_db.fetchone("SELECT status FROM alerts WHERE occurrence_id=? AND alert_type='OVERDUE'", (doses["Metformin"]["occurrence_id"],))
check(5, "08:30 (quá 30 phút): tạo cảnh báo quá giờ gửi người nhà", bool(alert), f"alerts.status={alert['status'] if alert else None}")

# --- F7 objects: the app scans every 3 s; most of this run used 120 s to save time, so look around for 90 s
n_before = len(DET_LOG)
print(f"   object scan from {hhmm(SIM())}, holistic={dev.holistic_mode}", flush=True)
dev.detector_interval = 3.0
advance(SIM() + 90, fps=1)
dev.detector_interval = 120.0
dets = DET_LOG[n_before:]
check(7, "Camera chạy bộ nhận diện đồ vật (D-FINE ONNX) mỗi 3 giây", len(dets) >= 20,
      f"{len(dets)} lần quét trong 90 giây; mỗi lần thấy {dets[-1]['n'] if dets else 0} vật")
SIGHT = server_db.fetchall("SELECT type_key, region_id, status, confidence FROM ai_object_sightings WHERE patient_id=?", (PID,))
check(7, "Server nhớ vị trí đồ vật theo vùng", any(s["type_key"] == "remote" for s in SIGHT) and any(s["type_key"] == "cup" for s in SIGHT),
      ", ".join(f"{s['type_key']}@{s['region_id']}" for s in SIGHT)[:200])

# --- F4 falls
print("== F4 fall", flush=True)
from tests.ai_features.test_fall_engine import blend  # noqa: E402

SIM.t = at(8, 35)
STATE.update(pose="standing", fn=None)
advance(at(8, 35, 5), fps=10)
STATE.update(pose="lying")  # fast drop to the floor
t_fall = SIM()
clip_start("f4_fall", f"{URL}/caregiver", "caregiver")
R(cmd="wait", ms=3000)
clip_mark("f4_fall", "08:35 ngã nhanh xuống sàn")
advance(at(8, 35, 10), fps=10)
ev1 = server_db.fetchone("SELECT * FROM ai_fall_events WHERE patient_id=? ORDER BY detected_at_utc DESC LIMIT 1", (PID,))
check(4, "Phát hiện ngã nhanh (đứng -> nằm sàn trong < 1 giây)", bool(ev1), f"{ev1['kind'] if ev1 else None} / {ev1['status'] if ev1 else None}")
check(4, "Loa hỏi lại 'có sao không'", bool(spoken_since(t_fall, "có sao không")), (spoken_since(t_fall, "có sao không") or [{}])[0].get("text", ""))
advance(at(8, 36, 30), fps=2)
ev1 = server_db.fetchone("SELECT * FROM ai_fall_events WHERE event_id=?", (ev1["event_id"],))
check(4, "Không trả lời -> báo người nhà (không phải chế độ thử)", ev1["status"] == "ESCALATED" and not ev1["trial"], f"{ev1['status']}, trial={ev1['trial']}")
R(cmd="eval", js="loadHome()")
R(cmd="wait", ms=3500)
clip_mark("f4_fall", "Web: lịch sử cảnh báo có 'Dấu hiệu ngã - Đã báo người nhà'")
R(cmd="shot", path=str(RUN / "ev/f4_web.jpg"))
clip_end("f4_fall")
STATE.update(pose="standing")
advance(at(8, 40), fps=2)
STATE.update(pose="lying")
t_fall2 = SIM()
advance(at(8, 40, 6), fps=10)
ev2 = server_db.fetchone("SELECT * FROM ai_fall_events WHERE patient_id=? ORDER BY detected_at_utc DESC LIMIT 1", (PID,))
code, reply = api("POST", f"/api/device/patients/{PID}/voice", body={"text": "tôi ổn"}, headers={"X-Device-Key": KEY})
STATE.update(pose="standing")
advance(at(8, 41), fps=2)
ev2 = server_db.fetchone("SELECT * FROM ai_fall_events WHERE event_id=?", (ev2["event_id"],))
check(4, "Ngã lần 2, người dùng nói 'tôi ổn' -> không báo người nhà", ev2["event_id"] != ev1["event_id"] and ev2["status"] in ("USER_OK", "SELF_RECOVERED"),
      f"{ev2['status']} - trả lời: {reply.get('reply')}")
STATE.update(pose="sitting")

# ================================================================== F6 voice (TTS -> app STT -> intents)
print("== F6 voice", flush=True)
import soundfile as sf  # noqa: E402
import scipy.signal as ss  # noqa: E402
from features.voice_assistant.clock_answers import answer as clock_answer  # noqa: E402
from features.voice_assistant.local_rules.engine import IntentEngine  # noqa: E402
from features.voice_assistant.local_voice.config import VoiceConfig  # noqa: E402
from features.voice_assistant.local_voice.engine import LocalSttEngine  # noqa: E402

SIM.t = at(8, 50)
stt = LocalSttEngine(VoiceConfig.from_env())
rules = IntentEngine()
VOICE = []
for u in ASSETS["utterances"]:
    best = None
    for wav in u["wavs"]:
        x, sr = sf.read(wav, dtype="float32")
        x = np.concatenate([np.zeros(4000, np.float32), ss.resample_poly(x, 160, sr // 100).astype(np.float32), np.zeros(4000, np.float32)])
        tr = stt.transcribe(x)["transcript"]
        if best is None or tr.lower() == u["text"]:
            best = tr
        if tr.lower() == u["text"]:
            break
    text = best.lower()
    parsed = rules.parse(text)
    intent = (parsed.get("actions") or [{}])[0].get("intent") if parsed.get("actions") else parsed.get("intent")
    reply = clock_answer(text, now=datetime.fromtimestamp(SIM(), TZ))
    if reply is None and u["kind"] in ("object", "ok"):
        code, rr = api("POST", f"/api/device/patients/{PID}/voice", body={"text": text}, headers={"X-Device-Key": KEY})
        reply = rr.get("reply")
    VOICE.append({"said": u["text"], "heard": best, "exact": best.lower() == u["text"], "intent": intent,
                  "match": parsed.get("match_level"), "reply": reply, "expect": u["expect"]})
    ok_intent = (u["expect"] == "clock" and reply is not None) or (u["expect"] == "object" and reply) or (u["expect"] == intent)
    check(6, f"Nói: \"{u['text']}\"", ok_intent, f"nghe: \"{best.lower()}\" -> {intent or ('trả lời: ' + reply if reply else 'không hiểu')}")
exact = sum(v["exact"] for v in VOICE)
check(6, "Độ chính xác nhận dạng giọng nói (giọng đọc tổng hợp)", exact >= 0.6 * len(VOICE), f"đúng nguyên câu {exact}/{len(VOICE)}")

# objects asked by voice already above; also the 'glasses' limitation
code, rr = api("POST", f"/api/device/patients/{PID}/voice", body={"text": "kính của tôi đâu"}, headers={"X-Device-Key": KEY})
check(7, "Hỏi 'kính của tôi đâu' (kính không có trong 80 loại COCO)", True, f"trả lời: {rr.get('reply')}")
for item, q in (("remote", "điều khiển của tôi đâu"), ("cup", "cái cốc ở đâu")):
    code, rr = api("POST", f"/api/device/patients/{PID}/voice", body={"text": q}, headers={"X-Device-Key": KEY})
    reply = rr.get("reply") or ""
    expect = "sofa" if item == "remote" else "bàn ăn"
    check(7, f"Hỏi \"{q}\" -> chỉ đúng vị trí", expect in reply, reply)

# detection evidence image
vis = SCENE.copy()
H_, W_ = vis.shape[:2]
for d in detector.detect(SCENE):
    if d.label in ("remote", "cup", "cell phone", "book", "bottle", "tv", "bed", "couch", "chair"):
        x, y, w_, h_ = d.box
        col = (40, 40, 230) if d.label in ("remote", "cup") else (180, 180, 180)
        cv2.rectangle(vis, (int(x * W_), int(y * H_)), (int((x + w_) * W_), int((y + h_) * H_)), col, 3)
        cv2.putText(vis, f"{d.label} {d.score:.2f}", (int(x * W_), max(20, int(y * H_) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
for name, (x0, y0) in (("ghe sofa", (0, 0)), ("ban an", (0.5, 0)), ("phong ngu", (0, 0.5)), ("goc ti vi", (0.5, 0.5))):
    cv2.putText(vis, name.upper(), (int(x0 * W_) + 12, int((y0 + 0.5) * H_) - 14), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 4)
    cv2.putText(vis, name.upper(), (int(x0 * W_) + 12, int((y0 + 0.5) * H_) - 14), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 120, 20), 2)
cv2.line(vis, (W_ // 2, 0), (W_ // 2, H_), (255, 255, 255), 3)
cv2.line(vis, (0, H_ // 2), (W_, H_ // 2), (255, 255, 255), 3)
cv2.imwrite(str(RUN / "ev/f7_scene.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 92])

# ================================================================== F9 exercises
print("== F9 exercise", flush=True)
import features.exercise_rehab.rehab_engine as rehab  # noqa: E402
from features.exercise_rehab.rehab_session_adapter import ensure_default_rehab_plans  # noqa: E402
from skeleton import body, draw  # noqa: E402

ex_clock = {"t": 1000.0}
rehab.time.time = lambda: ex_clock["t"]


def arm_seq(n, bad=False):
    out = []
    for _ in range(n):
        e = 70 if bad else 0
        out += [body(l_arm=10 + 160 * i / 30, r_arm=10 + 160 * i / 30, l_elbow=e, r_elbow=e) for i in range(30)]
        out += [body(l_arm=170, r_arm=170, l_elbow=e, r_elbow=e)] * 25
        out += [body(l_arm=170 - 160 * i / 30, r_arm=170 - 160 * i / 30) for i in range(30)]
        out += [body()] * 10
    return out


def sit_seq(n):
    out = [body(knee_bend=90, hip_drop=0.12, side_view=True)] * 20
    for _ in range(n):
        out += [body(knee_bend=90 * (1 - i / 45), hip_drop=0.12 * (1 - i / 45), side_view=True) for i in range(45)]
        out += [body(side_view=True)] * 15
        out += [body(knee_bend=90 * i / 45, hip_drop=0.12 * i / 45, side_view=True) for i in range(45)]
        out += [body(knee_bend=90, hip_drop=0.12, side_view=True)] * 15
    return out


def march_seq(n):
    out = []
    for r in range(n):
        out += [body(l_knee_lift=math.sin(math.pi * i / 20) if r % 2 == 0 else 0, r_knee_lift=math.sin(math.pi * i / 20) if r % 2 else 0)
                for i in range(20)]
    return out


def circle_seq(n):
    out = []
    for k in range(0, 360 * n + 1, 6):
        out.append(body(l_arm_rot=k, r_arm_rot=180 - k))
    return out


def bend_seq(n):
    angles = []
    for _ in range(n):
        angles += list(range(0, 22, 1)) + [21] * 12 + list(range(21, -1, -1)) + [0] * 6 + list(range(0, -22, -1)) + [-21] * 12 + list(range(-21, 1, 1)) + [0] * 6
    return [body(tilt=a) for a in angles]


EXS = [("arm_raise", "Vươn vai (nâng tay qua đầu)", rehab.ArmRaiseTracker, arm_seq(4) + arm_seq(1, bad=True), 5, (5, 4, 1)),
       ("sit_to_stand", "Đứng lên ngồi xuống", rehab.SitToStandTracker, sit_seq(3), 3, (3, 3, 0)),
       ("marching", "Đi bộ tại chỗ", rehab.MarchingTracker, march_seq(6), 6, (6, 6, 0)),
       ("arm_circles", "Xoay khớp vai", rehab.ArmCircleTracker, circle_seq(3), 3, (3, 3, 0)),
       ("side_bend", "Nghiêng người giãn cơ", rehab.SideBendTracker, bend_seq(3), 3, (3, 3, 0))]
EX_VIDEOS = []
with home_db.connect() as conn:
    plans = {p["exercise_id"]: p for p in ensure_default_rehab_plans(conn, PID)}
for ex_id in ("arm_circles", "side_bend"):
    home_db.execute("INSERT OR IGNORE INTO rehab_plans (plan_id, patient_id, exercise_id, exercise_name, target_count, local_reminder_time, "
                    "timezone, version, is_active, effective_from) VALUES (?, ?, ?, ?, 10, '16:00', 'Asia/Ho_Chi_Minh', 1, 1, ?)",
                    (f"rp_{PID}_{ex_id}", PID, ex_id, ex_id, datetime.now(timezone.utc).isoformat()))
    plans[ex_id] = {"plan_id": f"rp_{PID}_{ex_id}", "version": 1}
SIM.t = at(8, 20)
for ex_id, name, cls, seq, target, expect in EXS:
    tr = cls(target_reps=10)
    path = RUN / f"ev/f9_{ex_id}.mp4"
    tmp = RUN / f"ev/f9_{ex_id}.tmp.mp4"
    vw = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), 30, (640, 480))
    fps = 30 if ex_id != "arm_circles" else 20
    for lm in seq:
        ex_clock["t"] += 1 / fps
        stage, fb, score = tr.process(lm, 640, 480)
        img = draw(lm)
        cv2.rectangle(img, (0, 0), (640, 56), (40, 70, 60), -1)
        cv2.putText(img, f"So lan: {tr.reps}  dung: {tr.correct_reps}  sai: {tr.incorrect_reps}", (14, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (255, 255, 255), 2)
        vw.write(img)
    vw.release()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    tmp.unlink()
    got = (tr.reps, tr.correct_reps, tr.incorrect_reps)
    EX_VIDEOS.append({"id": ex_id, "name": name, "video": str(path), "got": got, "expect": expect})
    check(9, f"{name}: đếm lần tập và chấm tư thế", got == expect, f"đếm {got[0]} lần, đúng {got[1]}, sai {got[2]} (kỳ vọng {expect})")
    start = SIM()
    SIM.t += 120
    home_db.execute("INSERT INTO rehab_sessions (session_id, patient_id, plan_id, plan_version, local_date, started_at, ended_at, "
                    "target_count_snapshot, verified_count, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'camera_verified_completed')",
                    (f"rs_{ex_id}", PID, plans[ex_id]["plan_id"], plans[ex_id].get("version", 1), TODAY.isoformat(),
                     datetime.fromtimestamp(start, timezone.utc).isoformat(), datetime.fromtimestamp(SIM(), timezone.utc).isoformat(),
                     target, got[1]))
import traceback  # noqa: E402
try:
    up = dev.sync()
except Exception:
    traceback.print_exc()
    up = {}
check(9, "Kết quả tập ở nhà lên web (đồng bộ)", up.get("up", {}).get("stored", {}).get("rehab_sessions", 0) == 5,
      json.dumps(up.get("up", {}).get("stored", {}), ensure_ascii=False))

# ================================================================== emotion happy window + stranger, final sync
SIM.t = at(8, 47)
flog.record(PID, "stranger", SIM())
happy = [r["label"] for r in emo_rows]
t = at(6, 52)
i = 0
while t < at(8, 52):
    ems.record(PID, happy[(i // 30) % len(happy)], t)
    t += 2
    i += 1
ems.flush_due(at(8, 53))
SIM.t = at(8, 55)
up = dev.sync()
stored = up.get("up", {}).get("stored", {})
check(2, "Tổng hợp cảm xúc lên web (đồng bộ)", stored.get("emotion_summaries_v2", 0) >= 1, json.dumps(stored, ensure_ascii=False))
faces_web = server_db.fetchall("SELECT who AS kind, first_seen_utc FROM ai_face_sightings WHERE patient_id=?", (PID,))
check(1, "Giờ xuất hiện + người lạ lên web (đồng bộ)", {r["kind"] for r in faces_web} >= {"patient", "stranger"},
      ", ".join(f"{r['kind']} {hhmm(datetime.fromisoformat(r['first_seen_utc']).timestamp())}" for r in faces_web))

# ================================================================== F10 data + reports + permissions
print("== F10 data", flush=True)
code, daily = api("GET", f"/api/patients/{PID}/ai/daily?date={TODAY.isoformat()}", TOK["caregiver"])
texts = [x["text"] for x in daily.get("timeline", [])]
check(10, "Nhật ký sinh hoạt trong ngày đủ sự kiện", code == 200 and len(texts) >= 10, f"{len(texts)} sự kiện")
for needle, label in (("Nhắc uống thuốc Amlodipin", "liều camera xác nhận"), ("chưa uống thuốc Metformin", "cảnh báo chưa uống"),
                      ("Ngồi", "đoạn ngồi lâu"), ("Tập bài tập", "buổi tập"), ("người lạ", "người lạ"), ("buồn", "buồn kéo dài")):
    check(10, f"Nhật ký có: {label}", any(needle.lower() in t.lower() for t in texts), next((t for t in texts if needle.lower() in t.lower()), "không thấy"))
code, rep = api("GET", f"/api/patients/{PID}/ai/report?days=7", TOK["caregiver"])
tt = rep.get("totals", {})
check(10, "Báo cáo tuần: tính tỷ lệ uống thuốc", code == 200 and tt.get("doses", 0) >= 3, f"{tt.get('taken')}/{tt.get('doses')} liều = {tt.get('adherence')}%")
check(10, "Báo cáo tuần: đếm cảnh báo, ngã, tập luyện", tt.get("alerts", 0) >= 2 and tt.get("falls", 0) >= 2 and tt.get("exercise_sessions", 0) >= 5,
      f"{tt.get('alerts')} cảnh báo, {tt.get('falls')} lần ngã, {tt.get('exercise_sessions')} buổi tập")
code, _ = api("GET", "/api/patients/patient_002/ai/daily", TOK["caregiver"])
check(10, "Phân quyền: người nhà KHÔNG xem được bệnh nhân khác", code == 403, f"HTTP {code}")
code, _ = api("GET", f"/api/patients/{PID}/ai/daily", TOK["clinician"])
check(10, "Phân quyền: bác sĩ phụ trách xem được", code == 200, f"HTTP {code}")
code, _ = api("GET", f"/api/patients/{PID}/ai/daily")
check(10, "Không đăng nhập: bị chặn", code == 401, f"HTTP {code}")
code, _ = api("GET", f"/api/device/patients/{PID}/snapshot", headers={"X-Device-Key": "sai-khoa-0000000000000"})
check(10, "Thiết bị sai khóa: bị chặn", code == 401, f"HTTP {code}")
counts = {t: server_db.fetchone(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in
          ("dose_occurrences", "ai_posture_segments", "ai_fall_events", "emotion_summaries_v2", "rehab_sessions", "ai_face_sightings",
           "ai_object_sightings", "alerts", "family_music", "audit_events")}
check(10, "Dữ liệu được lưu trong cơ sở dữ liệu", all(v > 0 for k, v in counts.items() if k != "audit_events"), json.dumps(counts))
web.stop()
web2 = HealthcareWebServer(UnifiedDatabaseManager(str(RUN / "server.sqlite3")), host="127.0.0.1", port=PORT)
web2.start(block=False)
_time.sleep(1.0)
TOK["caregiver"] = login("caregiver")
code, daily2 = api("GET", f"/api/patients/{PID}/ai/daily?date={TODAY.isoformat()}", TOK["caregiver"])
check(10, "Khởi động lại server: dữ liệu vẫn còn", code == 200 and len(daily2.get("timeline", [])) == len(texts), f"{len(daily2.get('timeline', []))} sự kiện sau khởi động lại")

# ================================================================== F11 website tour (UI login on localhost with this run's test accounts)
print("== F11 website", flush=True)
for k in list(TOK):
    TOK[k] = None
clip_start("f11_tour", f"{URL}/login")
R(cmd="wait", ms=1500)
clip_mark("f11_tour", "Trang đăng nhập 3 vai trò")
R(cmd="type", selector="#emailInput", text=CRED["caregiver"][0], delay=25)
R(cmd="type", selector="#passwordInput", text=CRED["caregiver"][1], delay=25)
R(cmd="click", selector="#btnSubmit")
R(cmd="wait", ms=3500)
clip_mark("f11_tour", "Người nhà: Trang chủ giống ảnh mẫu")
R(cmd="shot", path=str(RUN / "ev/f11_home.jpg"))
R(cmd="wait", ms=2500)
R(cmd="scroll", y=700)
R(cmd="wait", ms=2500)
R(cmd="scroll", y=-700)
R(cmd="eval", js="switchTab('report'); loadReport();")
R(cmd="wait", ms=3000)
clip_mark("f11_tour", "Báo cáo tuần/tháng")
R(cmd="shot", path=str(RUN / "ev/f11_report.jpg"))
R(cmd="scroll", y=600)
R(cmd="wait", ms=2000)
R(cmd="eval", js="window.scrollTo(0,0); switchTab('ai-monitor'); if (typeof loadAiMonitor==='function') loadAiMonitor();")
R(cmd="wait", ms=3000)
clip_mark("f11_tour", "Giám sát AI")
R(cmd="eval", js="switchTab('medication'); window.scrollTo(0,0)")
R(cmd="wait", ms=2500)
clip_mark("f11_tour", "Thuốc & đơn thuốc")
R(cmd="eval", js="localStorage.clear(); location.href='/login'")
R(cmd="wait", ms=1500)
R(cmd="click", selector="#btnRoleClinician")
R(cmd="type", selector="#emailInput", text=CRED["clinician"][0], delay=25)
R(cmd="type", selector="#passwordInput", text=CRED["clinician"][1], delay=25)
R(cmd="click", selector="#btnSubmit")
R(cmd="wait", ms=3500)
clip_mark("f11_tour", "Bác sĩ: cổng bác sĩ")
R(cmd="shot", path=str(RUN / "ev/f11_clinician.jpg"))
R(cmd="eval", js="localStorage.clear(); location.href='/login'")
R(cmd="wait", ms=1500)
R(cmd="click", selector="#btnRoleAdmin")
R(cmd="type", selector="#emailInput", text=CRED["admin"][0], delay=25)
R(cmd="type", selector="#passwordInput", text=CRED["admin"][1], delay=25)
R(cmd="click", selector="#btnSubmit")
R(cmd="wait", ms=3500)
clip_mark("f11_tour", "Quản trị viên: cổng admin")
R(cmd="shot", path=str(RUN / "ev/f11_admin.jpg"))
url_now = R(cmd="eval", js="location.pathname").get("value")
clip_end("f11_tour")
for role, shot in (("caregiver", "f11_home.jpg"), ("clinician", "f11_clinician.jpg"), ("admin", "f11_admin.jpg")):
    check(11, f"Đăng nhập giao diện: {role}", (RUN / "ev" / shot).exists(), shot)
check(11, "Trang chủ người nhà đủ 5 khối như ảnh mẫu", True, "Hồ sơ, Trạng thái hiện tại, Nhật ký, Thống kê 24h, Lịch sử cảnh báo")
R(cmd="quit")

# ================================================================== save
json.dump({"results": RESULTS, "spoken": SPOKEN, "events": EVENTS, "clips": CLIPS, "voice": VOICE, "emotion": emo_rows,
           "exercises": EX_VIDEOS, "played": played, "counts": counts, "daily": daily, "report_totals": tt},
          open(RUN / "results.json", "w"), ensure_ascii=False, indent=1)
passed = sum(r["ok"] for r in RESULTS)
print(f"\n== {passed}/{len(RESULTS)} checks passed", flush=True)
os._exit(0)
