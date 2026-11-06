"""ObjectMemory: remembers where each object was last seen and answers "đồ của tôi đâu?".

Pure and deterministic: time comes from the frames, no I/O. One instance per patient.

Pipeline per detection frame
  1. map detector labels to catalog types, identify registered items by embedding similarity
  2. match each detection to a track (same item key + camera, overlapping box)
  3. a track becomes a *sighting* after ``stable_hits`` detections within ``stable_window_seconds``
     (one stray detection never moves an object in memory)
  4. a confirmed track that is missing from ``gone_misses`` clear scans of its place becomes
     HOLDING (a wrist was near it shortly before: probably carried) or GONE. A person standing
     in front of the object (occlusion) or a scan that did not cover its region never counts.
"""

from __future__ import annotations

import uuid
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from ai_core.object_finder.domain import (
    DEFAULT_CATALOG,
    DetectionFrame,
    DetectionIn,
    ItemType,
    ObjectConfig,
    Region,
    RegisteredItem,
    Sighting,
    SightingStatus,
    normalize_vi,
)
from ai_core.shared.detect.embedder import cosine
from ai_core.shared.zones import point_in_polygon

Box = tuple[float, float, float, float]


def _iou(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _center(b: Box) -> tuple[float, float]:
    return (b[0] + b[2] / 2, b[1] + b[3] / 2)


def _inside(p: tuple[float, float], b: Box) -> bool:
    return b[0] <= p[0] <= b[0] + b[2] and b[1] <= p[1] <= b[1] + b[3]


# ------------------------------------------------------------------ events


@dataclass(frozen=True, slots=True)
class SightingChanged:
    sighting: Sighting
    change: str  # confirmed | moved | status | refreshed


ObjectEvent = SightingChanged


@dataclass(slots=True)
class WhereAnswer:
    query_id: str
    query: str
    asked_at: float
    status: str  # found | holding | gone | not_seen | unknown_item
    text: str
    item_key: str | None = None
    item_name: str | None = None
    sighting: Sighting | None = None
    others: list[Sighting] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0


# ------------------------------------------------------------------ tracks


@dataclass(slots=True)
class _Track:
    item_key: str
    type_key: str
    camera_index: int
    box: Box
    hits: deque  # timestamps of recent detections
    scores: deque
    region_id: str | None
    sighting: Sighting | None = None  # set once confirmed
    misses: int = 0
    persisted_at: float = 0.0


@dataclass(frozen=True, slots=True)
class _Target:
    item_key: str | None  # registered item id; None = any object of the type
    type_key: str
    name: str


def history_key(s: Sighting) -> str:
    """Registered items exist once; unregistered objects are remembered per place (two cups, two places)."""
    if not s.item_key.startswith("type:"):
        return s.item_key
    if s.region_id:
        return f"{s.item_key}@{s.region_id}"
    cx, cy = _center(s.box)
    return f"{s.item_key}@{s.camera_index}:{int(cx * 10)},{int(cy * 10)}"


class ObjectMemory:
    REFRESH_SECONDS = 60.0  # how often an unchanged sighting's last_seen is persisted
    MATCH_IOU = 0.3
    MATCH_DIST = 0.05

    def __init__(
        self,
        patient_id: str,
        config: ObjectConfig | None = None,
        regions: list[Region] | None = None,
        items: list[RegisteredItem] | None = None,
        catalog: tuple[ItemType, ...] = DEFAULT_CATALOG,
    ) -> None:
        self.patient_id = patient_id
        self.config = config or ObjectConfig()
        self.regions: list[Region] = list(regions or [])
        self.items: dict[str, RegisteredItem] = {i.item_id: i for i in (items or [])}
        self.catalog = {t.key: t for t in catalog}
        self._label_map = {label: t.key for t in catalog for label in t.detector_labels}
        self._tracks: list[_Track] = []
        self._history: dict[str, Sighting] = {}  # latest sighting per history_key (incl. gone/holding)
        self._place_counts: dict[str, Counter] = {}
        self._wrists: deque = deque()  # (ts, camera, x, y)
        self._persons: dict[int, tuple[float, list[Box]]] = {}
        self.last_ts = 0.0

    # -------------------------------------------------------------- config

    @property
    def tz(self) -> str:
        return self.config.timezone

    def set_config(self, config: ObjectConfig) -> None:
        self.config = config

    def set_regions(self, regions: list[Region]) -> None:
        self.regions = list(regions)

    def upsert_item(self, item: RegisteredItem) -> None:
        self.items[item.item_id] = item

    def remove_item(self, item_id: str) -> None:
        self.items.pop(item_id, None)
        self._tracks = [t for t in self._tracks if t.item_key != item_id]
        self._history = {k: s for k, s in self._history.items() if s.item_key != item_id}

    def restore(self, sightings: list[Sighting], place_counts: dict[str, dict[str, int]]) -> None:
        """Load the latest sightings (newest first or any order) after a restart."""
        for s in sorted(sightings, key=lambda s: s.last_seen):
            self._history[history_key(s)] = s
        for s in self._history.values():
            if s.status == SightingStatus.SEEN:
                self._tracks.append(
                    _Track(s.item_key, s.type_key, s.camera_index, s.box, deque([s.last_seen]), deque([s.confidence]),
                           s.region_id, s, 0, s.last_seen)
                )
        self._place_counts = {k: Counter(v) for k, v in place_counts.items()}

    # -------------------------------------------------------------- helpers

    def region_at(self, point: tuple[float, float], camera_index: int) -> Region | None:
        for r in self.regions:
            if r.camera_index == camera_index and point_in_polygon(point, r.polygon):
                return r
        return None

    def region_name(self, region_id: str | None) -> str | None:
        return next((r.name for r in self.regions if r.region_id == region_id), None)

    def place_text(self, s: Sighting) -> str:
        name = self.region_name(s.region_id)
        if name:
            return name
        cx, cy = _center(s.box)
        horiz = "bên trái" if cx < 0.34 else "bên phải" if cx > 0.66 else "chính giữa"
        vert = "phía trên" if cy < 0.34 else "phía dưới" if cy > 0.66 else ""
        where = " ".join(p for p in (horiz, vert) if p)
        return f"{where} khung hình camera {s.camera_index + 1}"

    def item_name(self, item_key: str) -> str:
        if item_key.startswith("type:"):
            t = self.catalog.get(item_key[5:])
            return t.name if t else item_key[5:]
        item = self.items.get(item_key)
        return item.name if item else item_key

    def _identify(self, type_key: str, det: DetectionIn) -> tuple[str, float]:
        """Registered item id when the embedding matches one, else ``type:<key>``."""
        best, best_sim = None, 0.0
        if det.embedding is not None:
            for item in self.items.values():
                if item.type_key != type_key or not item.embeddings:
                    continue
                if item.embedder_id and det.embedder_id and item.embedder_id != det.embedder_id:
                    continue
                sim = max(cosine(det.embedding, e) for e in item.embeddings)
                if sim > best_sim:
                    best, best_sim = item.item_id, sim
        if best is not None and best_sim >= self.config.match_threshold:
            return best, best_sim
        # Only one registered item of this type and no way to compare: still not assumed (could be a guest's).
        return f"type:{type_key}", 0.0

    def needs_embedding(self, label: str) -> bool:
        type_key = self._label_map.get(label)
        return type_key is not None and any(i.type_key == type_key and i.embeddings for i in self.items.values())

    @property
    def wanted_labels(self) -> set[str]:
        return set(self._label_map)

    # -------------------------------------------------------------- inputs

    def note_wrists(self, ts: float, camera_index: int, wrists: list[tuple[float, float]]) -> None:
        for x, y in wrists:
            self._wrists.append((ts, camera_index, x, y))
        horizon = ts - max(10.0, self.config.hand_recent_seconds * 2)
        while self._wrists and self._wrists[0][0] < horizon:
            self._wrists.popleft()

    def note_persons(self, ts: float, camera_index: int, boxes: list[Box]) -> None:
        self._persons[camera_index] = (ts, list(boxes))

    def _hand_near(self, ts: float, camera_index: int, box: Box) -> bool:
        cfg = self.config
        cx, cy = _center(box)
        reach = cfg.hand_near_distance + max(box[2], box[3]) / 2
        for wts, cam, x, y in self._wrists:
            if cam == camera_index and ts - cfg.hand_recent_seconds <= wts <= ts:
                if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= reach:
                    return True
        return False

    def _occluded(self, ts: float, camera_index: int, box: Box, frame_persons: list[Box]) -> bool:
        persons = list(frame_persons)
        seen = self._persons.get(camera_index)
        if seen is not None and ts - seen[0] <= 2.0:
            persons += seen[1]
        c = _center(box)
        return any(_inside(c, p) or _iou(box, p) > 0.0 for p in persons)

    def _scanned(self, track: _Track, frame: DetectionFrame) -> bool:
        if frame.scanned_regions is None:
            return True
        return track.region_id is not None and track.region_id in frame.scanned_regions

    def ingest(self, frame: DetectionFrame) -> list[ObjectEvent]:
        cfg = self.config
        ts = frame.ts
        self.last_ts = max(self.last_ts, ts)
        events: list[ObjectEvent] = []
        persons = list(frame.person_boxes)
        matched: set[int] = set()

        for det in frame.detections:
            if det.label == "person":
                persons.append(det.box)
                continue
            type_key = self._label_map.get(det.label)
            if type_key is None:
                continue
            item_key, sim = self._identify(type_key, det)
            conf = det.score if item_key.startswith("type:") else min(det.score, sim)
            track = self._match(item_key, frame.camera_index, det.box, matched)
            if track is None:
                region = self.region_at(det.center, frame.camera_index)
                track = _Track(item_key, type_key, frame.camera_index, det.box, deque(), deque(),
                               region.region_id if region else None)
                self._tracks.append(track)
            matched.add(id(track))
            track.box = det.box
            track.hits.append(ts)
            track.scores.append(conf)
            track.misses = 0
            while track.hits and track.hits[0] < ts - cfg.stable_window_seconds:
                track.hits.popleft()
                track.scores.popleft()
            events += self._update_track(track, ts)

        # Misses: confirmed tracks on this camera not seen in a scan that covered them.
        for track in self._tracks:
            if id(track) in matched or track.camera_index != frame.camera_index or track.sighting is None \
                    or track.sighting.status != SightingStatus.SEEN:
                continue
            if not self._scanned(track, frame) or self._occluded(ts, frame.camera_index, track.box, persons):
                continue
            track.misses += 1
            if track.misses >= cfg.gone_misses:
                status = SightingStatus.HOLDING if self._hand_near(ts, track.camera_index, track.box) else SightingStatus.GONE
                s = replace(track.sighting, status=status, status_at=ts)
                track.sighting = s
                self._history[history_key(s)] = s
                events.append(SightingChanged(s, "status"))

        # Drop finished tracks: gone/holding ones (kept in history) and unconfirmed stale candidates.
        self._tracks = [
            t for t in self._tracks
            if (t.sighting is not None and t.sighting.status == SightingStatus.SEEN)
            or (t.sighting is None and t.hits and t.hits[-1] >= ts - cfg.stable_window_seconds)
        ]
        return events

    def _match(self, item_key: str, camera_index: int, box: Box, taken: set[int]) -> _Track | None:
        best, best_score = None, 0.0
        c = _center(box)
        for t in self._tracks:
            if t.item_key != item_key or t.camera_index != camera_index or id(t) in taken:
                continue
            if t.sighting is not None and t.sighting.status != SightingStatus.SEEN:
                continue
            tc = _center(t.box)
            dist = ((c[0] - tc[0]) ** 2 + (c[1] - tc[1]) ** 2) ** 0.5
            score = max(_iou(box, t.box), 1.0 - dist / self.MATCH_DIST if dist < self.MATCH_DIST else 0.0)
            if score >= self.MATCH_IOU and score > best_score:
                best, best_score = t, score
        return best

    def _update_track(self, track: _Track, ts: float) -> list[ObjectEvent]:
        cfg = self.config
        events: list[ObjectEvent] = []
        conf = round(max(track.scores) if track.scores else 0.0, 3)
        if track.sighting is None:
            if len(track.hits) < cfg.stable_hits:
                return events
            s = Sighting(
                sighting_id=uuid.uuid4().hex,
                patient_id=self.patient_id,
                item_key=track.item_key,
                type_key=track.type_key,
                camera_index=track.camera_index,
                region_id=track.region_id,
                box=tuple(round(v, 4) for v in track.box),
                first_seen=track.hits[0],
                last_seen=ts,
                confidence=conf,
            )
            prev = self._history.get(history_key(s))
            change = "confirmed"
            if not track.item_key.startswith("type:"):
                # A registered item exists once: an older place is superseded.
                for other in self._tracks:
                    if other is not track and other.item_key == track.item_key and other.sighting is not None \
                            and other.sighting.status == SightingStatus.SEEN:
                        moved = replace(other.sighting, status=SightingStatus.GONE, status_at=ts)
                        other.sighting = moved
                        events.append(SightingChanged(moved, "status"))
                if prev is not None and prev.region_id != s.region_id:
                    change = "moved"
            track.sighting = s
            track.persisted_at = ts
            self._history[history_key(s)] = s
            if s.region_id:
                self._place_counts.setdefault(track.item_key, Counter())[s.region_id] += 1
            events.append(SightingChanged(s, change))
            return events
        s = replace(track.sighting, last_seen=ts, box=tuple(round(v, 4) for v in track.box), confidence=max(conf, track.sighting.confidence))
        track.sighting = s
        self._history[history_key(s)] = s
        if ts - track.persisted_at >= self.REFRESH_SECONDS:
            track.persisted_at = ts
            events.append(SightingChanged(s, "refreshed"))
        return events

    def mark_not_there(self, sighting_id: str, ts: float) -> Sighting | None:
        """Caregiver/elder feedback: the object is not where we said."""
        for t in self._tracks:
            if t.sighting is not None and t.sighting.sighting_id == sighting_id:
                s = replace(t.sighting, status=SightingStatus.GONE, status_at=ts)
                t.sighting = s
                self._history[history_key(s)] = s
                self._tracks.remove(t)
                return s
        for key, s in self._history.items():
            if s.sighting_id == sighting_id and s.status == SightingStatus.SEEN:
                s = replace(s, status=SightingStatus.GONE, status_at=ts)
                self._history[key] = s
                return s
        return None

    # -------------------------------------------------------------- queries

    def resolve(self, query: str) -> _Target | None:
        q = f" {normalize_vi(query)} "
        best: tuple[int, _Target] | None = None

        def consider(phrase: str, target: _Target, bonus: int) -> None:
            nonlocal best
            p = normalize_vi(phrase)
            if p and f" {p} " in q:
                score = len(p) + bonus
                if best is None or score > best[0]:
                    best = (score, target)

        for item in self.items.values():
            target = _Target(item.item_id, item.type_key, item.name)
            for phrase in (item.name, *item.synonyms):
                consider(phrase, target, 100)
        if best is not None:
            # "kính của ông" vs "kính của bà": prefer the registered item whose owner is mentioned.
            owned = [i for i in self.items.values() if i.owner and f" {normalize_vi(i.owner)} " in q
                     and i.type_key == best[1].type_key]
            if len(owned) == 1:
                return _Target(owned[0].item_id, owned[0].type_key, owned[0].name)
            return best[1]
        for t in self.catalog.values():
            for phrase in (t.name, *t.synonyms):
                consider(phrase, _Target(None, t.key, t.name), 0)
        if best is None:
            return None
        target = best[1]
        # A generic word ("kính") with exactly one registered item of that type means that item.
        regs = [i for i in self.items.values() if i.type_key == target.type_key]
        if len(regs) == 1:
            return _Target(regs[0].item_id, regs[0].type_key, regs[0].name)
        owned = [i for i in regs if i.owner and f" {normalize_vi(i.owner)} " in q]
        if len(owned) == 1:
            return _Target(owned[0].item_id, owned[0].type_key, owned[0].name)
        return target

    def _clock(self, ts: float, now: float) -> str:
        z = ZoneInfo(self.tz)
        d, n = datetime.fromtimestamp(ts, z), datetime.fromtimestamp(now, z)
        hm = d.strftime("%H:%M")
        if d.date() == n.date():
            return f"lúc {hm}"
        if (n.date() - d.date()).days == 1:
            return f"lúc {hm} hôm qua"
        return f"lúc {hm} ngày {d.strftime('%d/%m')}"

    @staticmethod
    def _ago(seconds: float) -> str:
        """Rounded for listening: '35 phút', '1 tiếng 20 phút', '2 tiếng', '3 ngày'."""
        minutes = round(seconds / 60)
        if minutes < 60:
            return f"{max(1, minutes)} phút"
        minutes = round(minutes / 10) * 10
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours} tiếng" + (f" {minutes} phút" if minutes and hours < 3 else "")
        return f"{round(hours / 24)} ngày"

    def suggestions(self, item_key: str | None, type_key: str, exclude: str | None = None, n: int = 2) -> list[str]:
        counts: Counter = Counter()
        keys = [item_key] if item_key else list({s.item_key for s in self._history.values() if s.type_key == type_key})
        if item_key:
            keys.append(f"type:{type_key}")
        for k in keys:
            counts.update(self._place_counts.get(k, {}))
        names = []
        for region_id, _ in counts.most_common():
            if region_id == exclude:
                continue
            name = self.region_name(region_id)
            if name and name not in names:
                names.append(name)
            if len(names) >= n:
                break
        return names

    def answer(self, query: str, now: float) -> WhereAnswer:
        cfg = self.config
        qid = uuid.uuid4().hex
        target = self.resolve(query)
        if target is None:
            names = ", ".join(t.name for t in list(self.catalog.values())[:6])
            return WhereAnswer(qid, query, now, "unknown_item",
                               f"Cháu chưa rõ bác đang tìm đồ gì. Bác nói tên đồ vật giúp cháu nhé, ví dụ: {names}.")

        if target.item_key:
            own = [s for s in self._history.values() if s.item_key == target.item_key]
            generic = [s for s in self._history.values() if s.item_key == f"type:{target.type_key}"]
        else:
            own = [s for s in self._history.values() if s.type_key == target.type_key]
            generic = []
        # Objects still in place first, then the most recent.
        rank = lambda s: (s.status == SightingStatus.SEEN, s.last_seen)  # noqa: E731
        own.sort(key=rank, reverse=True)
        generic.sort(key=rank, reverse=True)

        name = target.name
        base = dict(query_id=qid, query=query, asked_at=now, item_key=target.item_key or f"type:{target.type_key}", item_name=name)

        sighting, unsure = (own[0], False) if own else (generic[0], True) if generic else (None, False)
        if own and generic and generic[0].status == SightingStatus.SEEN and own[0].status != SightingStatus.SEEN \
                and generic[0].last_seen > own[0].last_seen:
            sighting, unsure = generic[0], True

        if sighting is None:
            sugg = self.suggestions(target.item_key, target.type_key)
            tip = f" Bác thử tìm ở {' hoặc '.join(sugg)} xem." if sugg else ""
            return WhereAnswer(**base, status="not_seen", suggestions=sugg,
                               text=f"Cháu chưa nhìn thấy {name} ở chỗ nào trong camera.{tip}")

        place = self.place_text(sighting)
        what = f"một cái {self.catalog[target.type_key].name} (cháu chưa chắc có phải {name} của bác không)" if unsure else name
        conf = round(sighting.confidence * (0.6 if unsure else 1.0), 3)

        if sighting.status == SightingStatus.SEEN:
            age = now - sighting.last_seen
            others = [s for s in own[1:] if s.status == SightingStatus.SEEN][:2] if not target.item_key else []
            if age <= cfg.fresh_minutes * 60:
                text = f"Cháu thấy {what} ở {place}, vừa thấy cách đây ít phút."
            elif age <= cfg.stale_hours * 3600:
                text = f"Lần cuối cháu thấy {what} ở {place} {self._clock(sighting.last_seen, now)}, cách đây {self._ago(age)}."
            else:
                sugg = self.suggestions(target.item_key, target.type_key, exclude=sighting.region_id)
                tip = f" Đã lâu rồi, bác thử tìm thêm ở {' hoặc '.join(sugg)}." if sugg else " Đã lâu rồi nên có thể đồ đã được dời đi."
                return WhereAnswer(**base, status="found", sighting=sighting, suggestions=sugg, confidence=conf,
                                   text=f"Lần cuối cháu thấy {what} ở {place} {self._clock(sighting.last_seen, now)}.{tip}")
            if others:
                text += " Cháu cũng thấy " + " và ".join(f"một chiếc nữa ở {self.place_text(o)}" for o in others) + "."
            return WhereAnswer(**base, status="found", sighting=sighting, others=others, confidence=conf, text=text)

        sugg = self.suggestions(target.item_key, target.type_key, exclude=sighting.region_id)
        tip = f" Bác thử tìm ở {' hoặc '.join(sugg)} xem." if sugg else ""
        at = self._clock(sighting.status_at or sighting.last_seen, now)
        if sighting.status == SightingStatus.HOLDING:
            text = f"{what[0].upper() + what[1:]} được cầm lên từ {place} {at}. Có thể bác đang cầm hoặc đã mang đi chỗ khác.{tip}"
            return WhereAnswer(**base, status="holding", sighting=sighting, suggestions=sugg, confidence=conf, text=text)
        text = f"Cháu thấy {what} ở {place} {self._clock(sighting.last_seen, now)}, nhưng {at} thì không còn ở đó nữa.{tip}"
        return WhereAnswer(**base, status="gone", sighting=sighting, suggestions=sugg, confidence=conf, text=text)

    # -------------------------------------------------------------- views

    def sightings(self) -> list[Sighting]:
        return sorted(self._history.values(), key=lambda s: s.last_seen, reverse=True)
