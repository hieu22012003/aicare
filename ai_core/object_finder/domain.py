"""Domain types for feature 7 – finding everyday objects."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_vi(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics and punctuation: 'Kính của tôi đâu?' -> 'kinh cua toi dau'."""
    text = text.lower().replace("đ", "d")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True, slots=True)
class ItemType:
    key: str
    name: str  # Vietnamese display name
    synonyms: tuple[str, ...]
    detector_labels: tuple[str, ...]  # detector class names mapped to this type


# Detector labels: COCO names where COCO has the object; the rest need the fine-tuned model (phase 3).
DEFAULT_CATALOG: tuple[ItemType, ...] = (
    ItemType("glasses", "kính", ("kính", "kính lão", "mắt kính", "cặp kính", "kiếng", "kính mắt"), ("glasses", "eyeglasses")),
    ItemType("phone", "điện thoại", ("điện thoại", "điện thoại di động", "di động", "đt", "máy điện thoại", "cái máy"), ("cell phone", "phone")),
    ItemType("remote", "điều khiển", ("điều khiển", "cái điều khiển", "remote", "điều khiển tivi", "điều khiển tv", "điều khiển ti vi"), ("remote",)),
    ItemType("keys", "chìa khóa", ("chìa khóa", "chìa khoá", "chùm chìa khóa", "chùm chìa", "chìa"), ("keys", "key")),
    ItemType("wallet", "ví", ("ví", "bóp", "ví tiền", "cái ví"), ("wallet",)),
    ItemType("pillbox", "hộp thuốc", ("hộp thuốc", "lọ thuốc", "vỉ thuốc", "thuốc"), ("pillbox", "medicine")),
    ItemType("cane", "gậy", ("gậy", "gậy chống", "ba toong", "cây gậy", "batoong"), ("cane", "walking stick")),
    ItemType("cup", "cốc", ("cốc", "ly", "tách", "cái cốc", "cái ly"), ("cup", "mug")),
    ItemType("book", "sách", ("sách", "quyển sách", "cuốn sách", "cuốn truyện"), ("book",)),
)


class SightingStatus(str, Enum):
    SEEN = "seen"  # still at the last known place
    HOLDING = "holding"  # left its place while a hand was near: probably being carried
    GONE = "gone"  # left its place, whereabouts unknown


class Region(BaseModel):
    """A named area on a camera frame ('bàn uống nước', 'tủ đầu giường'), normalized polygon."""

    region_id: str = Field(pattern=r"^[a-z0-9_\-]{1,40}$")
    name: str = Field(min_length=1, max_length=60)
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=64)
    camera_index: int = 0

    @model_validator(mode="after")
    def _check(self) -> "Region":
        for x, y in self.polygon:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("polygon coordinates must be normalized to [0, 1]")
        return self

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


@dataclass(slots=True)
class RegisteredItem:
    item_id: str
    patient_id: str
    type_key: str
    name: str
    owner: str | None = None
    synonyms: list[str] = field(default_factory=list)
    embeddings: list[list[float]] = field(default_factory=list)  # L2-normalized
    embedder_id: str | None = None
    created_at: float = 0.0


@dataclass(slots=True)
class Sighting:
    sighting_id: str
    patient_id: str
    item_key: str  # registered item id, or "type:<key>" for an unregistered object
    type_key: str
    camera_index: int
    region_id: str | None
    box: tuple[float, float, float, float]
    first_seen: float
    last_seen: float
    confidence: float
    status: SightingStatus = SightingStatus.SEEN
    status_at: float | None = None


@dataclass(frozen=True, slots=True)
class DetectionIn:
    label: str
    score: float
    box: tuple[float, float, float, float]  # x, y, w, h normalized
    embedding: tuple[float, ...] | None = None
    embedder_id: str | None = None

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.box
        return (x + w / 2, y + h / 2)


@dataclass(frozen=True, slots=True)
class DetectionFrame:
    ts: float
    camera_index: int
    detections: tuple[DetectionIn, ...]
    person_boxes: tuple[tuple[float, float, float, float], ...] = ()
    # Regions that were actually scanned in this frame; None = the whole frame was scanned.
    scanned_regions: tuple[str, ...] | None = None


class ObjectConfig(BaseModel):
    timezone: str = "Asia/Ho_Chi_Minh"
    stable_hits: int = Field(3, ge=1, le=20, description="detections needed within stable_window_seconds")
    stable_window_seconds: float = Field(10, ge=1, le=120)
    gone_misses: int = Field(3, ge=1, le=50, description="clear scans without the object before it counts as moved")
    hand_near_distance: float = Field(0.08, ge=0.01, le=0.5)
    hand_recent_seconds: float = Field(5, ge=1, le=60)
    match_threshold: float = Field(0.82, ge=0.0, le=1.0, description="cosine similarity to a registered item")
    fresh_minutes: float = Field(10, ge=1, le=120)
    stale_hours: float = Field(6, ge=1, le=72)
    min_confidence_sure: float = Field(0.7, ge=0, le=1)

    @field_validator("timezone")
    @classmethod
    def _check_tz(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value
