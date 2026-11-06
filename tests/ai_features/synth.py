"""Synthetic MediaPipe-style landmarks for tests (image 640x480, normalized coordinates)."""

from __future__ import annotations

from ai_core.posture_activity_monitoring.domain import Identity, Observation
from ai_core.shared.pose.landmarks import NUM_LANDMARKS, Landmark

# (shoulder, hip, knee, ankle) midpoints for each pose, normalized coordinates.
POSES: dict[str, tuple[tuple[float, float], ...]] = {
    "standing": ((0.50, 0.30), (0.50, 0.55), (0.50, 0.72), (0.50, 0.90)),
    # side view: thigh horizontal, knee bent ~90°
    "sitting": ((0.50, 0.35), (0.50, 0.60), (0.65, 0.62), (0.65, 0.85)),
    # facing the camera: thigh foreshortened, legs look straight
    "sitting_front": ((0.50, 0.35), (0.50, 0.60), (0.50, 0.66), (0.50, 0.85)),
    "lying": ((0.30, 0.70), (0.55, 0.72), (0.72, 0.73), (0.88, 0.74)),
    "bending": ((0.62, 0.45), (0.50, 0.55), (0.50, 0.72), (0.50, 0.90)),
}


def landmarks(kind: str, dx: float = 0.0, dy: float = 0.0, visibility: float = 0.95) -> tuple[Landmark, ...]:
    shoulder, hip, knee, ankle = POSES[kind]
    pts = [Landmark(shoulder[0] + dx, shoulder[1] - 0.08 + dy, 0.0, visibility)] * NUM_LANDMARKS  # head-ish filler
    pts = list(pts)

    def put(left: int, right: int, xy: tuple[float, float], spread: float = 0.04) -> None:
        pts[left] = Landmark(xy[0] - spread + dx, xy[1] + dy, 0.0, visibility)
        pts[right] = Landmark(xy[0] + spread + dx, xy[1] + dy, 0.0, visibility)

    if kind == "lying":
        # body along x: spread left/right points vertically instead
        for (left, right), xy in zip(((11, 12), (23, 24), (25, 26), (27, 28)), (shoulder, hip, knee, ankle)):
            pts[left] = Landmark(xy[0] + dx, xy[1] - 0.02 + dy, 0.0, visibility)
            pts[right] = Landmark(xy[0] + dx, xy[1] + 0.02 + dy, 0.0, visibility)
        pts[0] = Landmark(shoulder[0] - 0.08 + dx, shoulder[1] + dy, 0.0, visibility)
    else:
        put(11, 12, shoulder)
        put(23, 24, hip)
        put(25, 26, knee)
        put(27, 28, ankle)
    return tuple(pts)


def obs(ts: float, kind: str | None, dx: float = 0.0, dy: float = 0.0, identity: Identity = Identity.PATIENT) -> Observation:
    return Observation(ts=ts, landmarks=landmarks(kind, dx, dy) if kind else None, identity=identity)


def stream(engine, start: float, seconds: float, kind: str | None, fps: float = 2.0, walk_speed: float = 0.0):
    """Feed ``seconds`` of observations; ``walk_speed`` moves the person (normalized x per second)."""
    events = []
    n = int(seconds * fps)
    for i in range(n):
        t = start + i / fps
        dx = ((walk_speed * (t - start)) % 0.6) - 0.3 if walk_speed else 0.0
        events += engine.ingest(obs(t, kind, dx=dx))
    return events, start + n / fps
