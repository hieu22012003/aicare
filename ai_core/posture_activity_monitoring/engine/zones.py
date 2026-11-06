"""Map a hip position to a location (bed, chair, sofa, floor) using caregiver-drawn zones (BR-06)."""

from __future__ import annotations

from collections.abc import Sequence

from ai_core.shared.zones import Zone, ZoneType, has_rest_zone, point_in_polygon, zone_at
from ai_core.posture_activity_monitoring.domain import Location, Posture

_ZONE_LOCATION = {ZoneType.BED: Location.BED, ZoneType.SOFA: Location.SOFA, ZoneType.CHAIR: Location.CHAIR}

__all__ = ["locate", "point_in_polygon"]


def locate(hip: tuple[float, float], posture: Posture, zones: Sequence[Zone], camera_index: int = 0) -> Location:
    zone = zone_at(hip, zones, camera_index)
    if zone is not None:
        return _ZONE_LOCATION[zone.zone_type]
    if posture == Posture.LYING:
        # Without any rest zone configured we cannot tell bed from floor.
        return Location.FLOOR if has_rest_zone(zones, camera_index) else Location.UNKNOWN
    return Location.OTHER
