"""Hand-to-mouth intake detector over MediaPipe Holistic landmarks.

One intake = a sequence, not a frame:
  (optional) pinch: thumb and index tips together (holding a pill)
  contact: a fingertip near the mouth for >= hold_seconds (rejected if it lasts > 5 s: phone, chin rest)
  mouth open: during the contact or within open_slack_seconds after it
  drink (booster): a cup at the mouth, or a second contact with the head tilted back, within 60 s
Verdict: VERIFIED when contact + mouth open + (drink, or pinch and pill-place context); otherwise
LIKELY, reported when the drink window ends. LIKELY never counts as taken on its own.

Pure and deterministic: time comes from the frames.
"""

from __future__ import annotations

import math
from collections import deque

from features.medication.intake_monitoring.domain import (
    Hand,
    HolisticFrame,
    IntakeConfig,
    IntakeObservation,
    IntakeVerdict,
    Point,
)

# MediaPipe face mesh indices
LIP_UPPER_INNER, LIP_LOWER_INNER, MOUTH_LEFT, MOUTH_RIGHT = 13, 14, 61, 291
FACE_LEFT, FACE_RIGHT, FOREHEAD, NOSE_TIP, CHIN = 234, 454, 10, 1, 152
# MediaPipe hand indices
THUMB_TIP, INDEX_TIP, INDEX_MCP, PINKY_MCP = 4, 8, 5, 17


def _d(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def mouth_center(face) -> Point:
    u, lo = face[LIP_UPPER_INNER], face[LIP_LOWER_INNER]
    return ((u[0] + lo[0]) / 2, (u[1] + lo[1]) / 2)


def face_width(face) -> float:
    return _d(face[FACE_LEFT], face[FACE_RIGHT])


def mouth_open_ratio(face) -> float:
    width = _d(face[MOUTH_LEFT], face[MOUTH_RIGHT])
    return _d(face[LIP_UPPER_INNER], face[LIP_LOWER_INNER]) / width if width > 0 else 0.0


def nose_ratio(face) -> float:
    """Nose height within forehead→chin. Tilting the head back moves the nose up: the ratio drops."""
    span = face[CHIN][1] - face[FOREHEAD][1]
    return (face[NOSE_TIP][1] - face[FOREHEAD][1]) / span if span > 0 else 0.5


def pinch_ratio(hand) -> float:
    width = _d(hand[INDEX_MCP], hand[PINKY_MCP])
    return _d(hand[THUMB_TIP], hand[INDEX_TIP]) / width if width > 0 else 9.9


def mouth_gap(hand, face) -> float:
    m = mouth_center(face)
    fw = face_width(face)
    return min(_d(hand[THUMB_TIP], m), _d(hand[INDEX_TIP], m)) / fw if fw > 0 else 9.9


class IntakeDetector:
    def __init__(self, config: IntakeConfig | None = None) -> None:
        self.config = config or IntakeConfig()
        self.reset()

    def reset(self) -> None:
        self._pinch_at: float | None = None
        self._context_at: float | None = None
        self._contact_start: float | None = None
        self._contact_end: float | None = None  # set while waiting open_slack after the hand left
        self._contact_hand: str | None = None
        self._rejected = False
        self._open_at: float | None = None
        self._candidate: IntakeObservation | None = None
        self._tilt_base: deque = deque(maxlen=60)
        self._last_ts = 0.0

    def _hands(self, f: HolisticFrame) -> list[tuple[str, tuple]]:
        out = []
        if f.right_hand and self.config.hand in (Hand.ANY, Hand.RIGHT):
            out.append(("right", f.right_hand))
        if f.left_hand and self.config.hand in (Hand.ANY, Hand.LEFT):
            out.append(("left", f.left_hand))
        return out

    def feed(self, f: HolisticFrame) -> list[IntakeObservation]:
        cfg = self.config
        events: list[IntakeObservation] = []
        if f.ts < self._last_ts:
            return events
        self._last_ts = f.ts
        events += self._expire(f.ts)
        if not f.identity_ok or f.face is None:
            if self._contact_start is not None and self._contact_end is None:
                self._contact_end = f.ts
            events += self._finish(f.ts)
            return events

        if f.near_pillbox:
            self._context_at = f.ts
        opened = mouth_open_ratio(f.face) >= cfg.mouth_open_ratio
        nose = nose_ratio(f.face)

        best_hand, best_gap = None, 9.9
        for name, pts in self._hands(f):
            if pinch_ratio(pts) <= cfg.pinch_ratio:
                self._pinch_at = f.ts
            gap = mouth_gap(pts, f.face)
            if gap < best_gap:
                best_hand, best_gap = name, gap
        near = best_hand is not None and best_gap <= cfg.mouth_distance_ratio

        if near:
            if self._contact_end is not None:  # back at the mouth during the slack: close the last contact first
                events += self._finish(f.ts, force=True)
            if self._contact_start is None:
                self._contact_start, self._contact_hand = f.ts, best_hand
                self._rejected, self._open_at = False, None
            if f.phone_in_hand or f.ts - self._contact_start > cfg.long_hold_reject_seconds:
                self._rejected = True
            if opened and self._open_at is None:
                self._open_at = f.ts
            # A second contact with the head tilted back while a candidate waits = drinking from a glass.
            if self._candidate is not None and f.ts - self._contact_start >= cfg.hold_seconds and self._tilted(nose):
                events += self._promote(f.ts, "second_contact_tilt")
        else:
            self._tilt_base.append(nose)
            if self._contact_start is not None:
                if self._contact_end is None:
                    self._contact_end = f.ts
                if opened and self._open_at is None and f.ts - self._contact_end <= cfg.open_slack_seconds:
                    self._open_at = f.ts
                events += self._finish(f.ts)

        if f.cup_near_mouth and self._candidate is not None:
            events += self._promote(f.ts, "cup")
        return events

    def _tilted(self, now_ratio: float) -> bool:
        if len(self._tilt_base) < 5:
            return False
        base = sorted(self._tilt_base)[len(self._tilt_base) // 2]
        return base - now_ratio >= self.config.head_tilt_drop

    def _finish(self, ts: float, force: bool = False) -> list[IntakeObservation]:
        """Close a contact once the mouth-open slack has passed (or the mouth already opened)."""
        cfg = self.config
        if self._contact_start is None or self._contact_end is None:
            return []
        if not force and self._open_at is None and ts - self._contact_end <= cfg.open_slack_seconds:
            return []  # still waiting for the mouth to open
        start, end, hand = self._contact_start, self._contact_end, self._contact_hand
        ok = end - start >= cfg.hold_seconds and not self._rejected and self._open_at is not None
        open_at = self._open_at
        self._contact_start = self._contact_end = self._contact_hand = self._open_at = None
        self._rejected = False
        if not ok or self._candidate is not None:
            return []
        pinch = self._pinch_at if self._pinch_at is not None and 0 <= start - self._pinch_at <= cfg.pinch_recent_seconds else None
        if pinch is None and self._pinch_at is not None and start <= self._pinch_at <= end:
            pinch = self._pinch_at  # pinch seen while bringing the pill in
        ctx = self._context_at if self._context_at is not None and start - self._context_at <= cfg.context_window_seconds else None
        obs = IntakeObservation(ts=ts, verdict=IntakeVerdict.LIKELY, hand=hand or "?", contact_at=start,
                                mouth_open_at=open_at, pinch_at=pinch, context_at=ctx)
        if pinch is not None and ctx is not None:
            obs.verdict = IntakeVerdict.VERIFIED
            obs.score, obs.steps = self._score(obs), self._steps(obs, "pill_context")
            return [obs]
        self._candidate = obs
        return []

    def _promote(self, ts: float, how: str) -> list[IntakeObservation]:
        obs = self._candidate
        if obs is None:
            return []
        self._candidate = None
        obs.verdict, obs.drink_at, obs.ts = IntakeVerdict.VERIFIED, ts, ts
        obs.score, obs.steps = self._score(obs), self._steps(obs, how)
        return [obs]

    def _expire(self, ts: float) -> list[IntakeObservation]:
        obs = self._candidate
        if obs is None or ts - obs.contact_at < self.config.drink_window_seconds:
            return []
        self._candidate = None
        obs.ts, obs.score, obs.steps = ts, self._score(obs), self._steps(obs, None)
        return [obs]

    def flush(self, ts: float) -> list[IntakeObservation]:
        """End of the drink window or of the monitoring session: a waiting candidate is LIKELY."""
        if self._contact_start is not None and self._contact_end is None:
            self._contact_end = ts
        events = self._finish(ts, force=True) if self._contact_start is not None else []
        if self._candidate is None:
            return events
        obs, self._candidate = self._candidate, None
        obs.ts, obs.score, obs.steps = ts, self._score(obs), self._steps(obs, None)
        return events + [obs]

    @staticmethod
    def _score(o: IntakeObservation) -> float:
        s = 0.5 + 0.15 * (o.pinch_at is not None) + 0.25 * (o.drink_at is not None) + 0.1 * (o.context_at is not None)
        return round(min(1.0, s), 2)

    @staticmethod
    def _steps(o: IntakeObservation, drink: str | None) -> dict:
        return {
            "pinch_at": o.pinch_at, "contact_at": o.contact_at, "mouth_open_at": o.mouth_open_at,
            "drink_at": o.drink_at, "drink_evidence": drink, "context_at": o.context_at, "hand": o.hand,
        }
