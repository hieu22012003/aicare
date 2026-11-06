"""Caregiver-drawn zones on a camera frame, shared by features 3, 4 and 7.

Coordinates are normalized to [0, 1] so they survive a change of camera resolution.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ZoneType(str, Enum):
    BED = "BED"
    CHAIR = "CHAIR"
    SOFA = "SOFA"


REST_ZONES = frozenset({ZoneType.BED, ZoneType.SOFA})


class Zone(BaseModel):
    """A polygon drawn on a still frame of the camera, coordinates normalized to [0, 1]."""

    zone_type: ZoneType
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=64)
    camera_index: int = 0
    name: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_points(self) -> "Zone":
        for x, y in self.polygon:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("polygon coordinates must be normalized to [0, 1]")
        return self


def point_in_polygon(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    """Ray casting test; points on an edge may go either way, which is fine at zone scale."""
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def zone_at(point: tuple[float, float], zones: Sequence[Zone], camera_index: int = 0) -> Zone | None:
    """First zone containing the point, beds before sofas before chairs."""
    for zone_type in (ZoneType.BED, ZoneType.SOFA, ZoneType.CHAIR):
        for zone in zones:
            if zone.camera_index == camera_index and zone.zone_type == zone_type and point_in_polygon(point, zone.polygon):
                return zone
    return None


def has_rest_zone(zones: Sequence[Zone], camera_index: int = 0) -> bool:
    return any(z.camera_index == camera_index and z.zone_type in REST_ZONES for z in zones)
