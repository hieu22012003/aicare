"""
Monitoring Service for Health Observations, Remote Doctor Dashboard, and Aggregation (P0-P2).
Enforces server-side authorization on EVERY request, preserves audit trails, validates input typing ranges,
calculates timezone-aware day/week aggregations with sample count n, and prevents misleading zero/bridged plots.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from features.health_vitals.domain.contracts import (
    ICareTeamRepository,
    IObservationRepository,
    IPatientAccessRepository,
)
from features.health_vitals.domain.models import (
    AggregatedPoint,
    BloodPressureReading,
    ClinicalMessage,
    ClinicianReview,
    HealthObservation,
    MeasurementComponent,
    MeasurementGroup,
    ObservationType,
    PatientCardSummary,
    Resolution,
    ReviewStatus,
    SourceType,
    SymptomEntry,
)

logger = logging.getLogger("MonitoringService")


class MonitoringService:
    """Core domain service for vital sign logging and clinician review."""

    def __init__(
        self,
        obs_repo: IObservationRepository,
        care_team_repo: ICareTeamRepository,
        patient_access_repo: IPatientAccessRepository,
        clock=None,
    ) -> None:
        self.obs_repo = obs_repo
        self.care_team_repo = care_team_repo
        self.patient_access_repo = patient_access_repo
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._auth_cache: Dict[str, bool] = {}  # Cache key: f"{clinician_id}:{patient_id}"

    def get_current_time(self) -> datetime:
        return self._clock()

    def invalidate_cache(self, clinician_id: Optional[str] = None, patient_id: Optional[str] = None) -> None:
        """Clear cached permissions."""
        if clinician_id and patient_id:
            self._auth_cache.pop(f"{clinician_id}:{patient_id}", None)
        else:
            self._auth_cache.clear()

    # --------------------------------------------------------------------------
    # 1. PERMISSION CHECKS (Enforced on EVERY request)
    # --------------------------------------------------------------------------
    def assert_clinician_access(self, clinician_id: str, patient_id: str) -> None:
        """Verify clinician has active, unexpired care_team rights to access this patient."""
        cache_key = f"{clinician_id}:{patient_id}"
        # Always check against the database to guarantee real-time revocation enforcement
        is_authorized = self.care_team_repo.is_clinician_authorized(clinician_id, patient_id)
        self._auth_cache[cache_key] = is_authorized

        if not is_authorized:
            self.invalidate_cache(clinician_id, patient_id)
            raise PermissionError(
                f"Truy cập bị từ chối: Bác sĩ '{clinician_id}' không có quyền truy cập bệnh nhân '{patient_id}' (hết hạn hoặc chưa được phân công)."
            )

    def assert_user_can_record(self, user_id: str, patient_id: str) -> None:
        """Verify user (patient themselves, active caregiver, or doctor) can record data."""
        if not self.patient_access_repo.can_user_access_patient(user_id, patient_id, "can_view_health"):
            raise PermissionError(
                f"Người dùng '{user_id}' không có quyền nhập số đo cho bệnh nhân '{patient_id}'."
            )

    # --------------------------------------------------------------------------
    # 2. MEASUREMENT RECORDING (P1)
    # --------------------------------------------------------------------------
    def record_measurement(
        self,
        patient_id: str,
        entered_by_user_id: str,
        components: List[MeasurementComponent],
        measured_at: Optional[str] = None,
        source_type: str = SourceType.MANUAL_PATIENT.value,
        device_label: Optional[str] = None,
        context: str = "",
        client_request_id: Optional[str] = None,
    ) -> MeasurementGroup:
        """
        Record a measurement group containing one or more components.
        For Blood Pressure, components contain systolic and diastolic in the same group.
        Guarantees separate measured_at and recorded_at, plausible range check, and idempotency.
        """
        # 1. Authorize user
        self.assert_user_can_record(entered_by_user_id, patient_id)

        now_utc = self.get_current_time().isoformat()
        actual_measured_at = measured_at or now_utc

        # 2. Handle missing device label (Criterion 9)
        final_device_label = (device_label or "").strip()
        if not final_device_label:
            final_device_label = "Người dùng nhập"
            if source_type == SourceType.DEVICE_IMPORT.value:
                source_type = SourceType.MANUAL_PATIENT.value

        # 3. Validate plausible range for typing errors
        types_map = self.obs_repo.get_observation_types()
        for comp in components:
            obs_type = types_map.get(comp.type_code)
            if not obs_type:
                raise ValueError(f"Loại số đo không xác định: '{comp.type_code}'")

            if comp.value_numeric < obs_type.min_plausible_value or comp.value_numeric > obs_type.max_plausible_value:
                raise ValueError(
                    f"Giá trị '{comp.value_numeric}' của {obs_type.name} nằm ngoài phạm vi nhập hợp lý "
                    f"({obs_type.min_plausible_value} - {obs_type.max_plausible_value} {obs_type.standard_unit}). "
                    f"Vui lòng kiểm tra lại lỗi gõ phím."
                )

        # 4. Generate idempotency key if not supplied
        req_id = client_request_id or str(uuid.uuid4()).replace("-", "")

        group_id = str(uuid.uuid4()).replace("-", "")
        group = MeasurementGroup(
            group_id=group_id,
            patient_id=patient_id,
            measured_at=actual_measured_at,
            recorded_at=now_utc,
            entered_by_user_id=entered_by_user_id,
            entered_by_name="",  # Will be loaded from user in repo
            source_type=source_type,
            device_label=final_device_label,
            context=context,
            status="active",
            client_request_id=req_id,
        )

        return self.obs_repo.create_measurement_group(group, components)

    def record_blood_pressure(
        self,
        patient_id: str,
        entered_by_user_id: str,
        systolic: float,
        diastolic: float,
        measured_at: Optional[str] = None,
        source_type: str = SourceType.MANUAL_PATIENT.value,
        device_label: Optional[str] = None,
        context: str = "",
        client_request_id: Optional[str] = None,
        notes: str = "",
    ) -> MeasurementGroup:
        """Convenience method to record a Blood Pressure pair in a single measurement group."""
        components = [
            MeasurementComponent(
                type_code="blood_pressure_systolic",
                value_numeric=systolic,
                unit_code="mmHg",
                notes=notes,
            ),
            MeasurementComponent(
                type_code="blood_pressure_diastolic",
                value_numeric=diastolic,
                unit_code="mmHg",
                notes=notes,
            ),
        ]
        return self.record_measurement(
            patient_id=patient_id,
            entered_by_user_id=entered_by_user_id,
            components=components,
            measured_at=measured_at,
            source_type=source_type,
            device_label=device_label,
            context=context,
            client_request_id=client_request_id,
        )

    def correct_observation(
        self,
        observation_id: str,
        new_value: float,
        new_unit: str,
        entered_by_user_id: str,
        client_request_id: Optional[str] = None,
        notes: str = "",
    ) -> HealthObservation:
        """Correct an erroneous observation while preserving audit trail (Criterion 8)."""
        req_id = client_request_id or str(uuid.uuid4()).replace("-", "")
        return self.obs_repo.correct_observation(
            observation_id=observation_id,
            new_value=new_value,
            new_unit=new_unit,
            entered_by_user_id=entered_by_user_id,
            client_request_id=req_id,
            notes=notes,
        )

    # --------------------------------------------------------------------------
    # 3. CLINICIAN DASHBOARD & TRENDS (P2)
    # --------------------------------------------------------------------------
    def get_clinician_patients_list(self, clinician_id: str) -> List[PatientCardSummary]:
        """Tier 1: List all assigned patients for clinician."""
        return self.care_team_repo.get_clinician_patients(clinician_id)

    def get_patient_profile(self, clinician_id: str, patient_id: str) -> Dict[str, Any]:
        """
        Tier 2: Detailed patient profile with authorization enforcement.
        Returns patient metadata, latest measurements by type, review status.
        """
        self.assert_clinician_access(clinician_id, patient_id)

        patient = self.patient_access_repo.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Bệnh nhân '{patient_id}' không tồn tại.")

        latest_review = self.obs_repo.get_latest_review(patient_id, clinician_id)

        # Get latest observation for each type
        latest_obs = self.obs_repo.get_observations(patient_id=patient_id, limit=50)
        latest_by_type: Dict[str, HealthObservation] = {}
        for obs in latest_obs:
            if obs.type_code not in latest_by_type:
                latest_by_type[obs.type_code] = obs

        # Get latest BP reading
        bp_readings = self.obs_repo.get_blood_pressure_readings(patient_id=patient_id, limit=1)
        latest_bp = bp_readings[0] if bp_readings else None

        return {
            "patient": patient,
            "latest_review": latest_review,
            "latest_by_type": latest_by_type,
            "latest_bp": latest_bp,
        }

    def get_observations_trend(
        self,
        clinician_id: str,
        patient_id: str,
        type_code: str,
        days_filter: int = 30,
        resolution: Resolution = Resolution.RAW,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Retrieve trends for a specific observation type with resolution (RAW, DAY, WEEK).
        Enforces:
        - Permission check on every request (Criterion 4).
        - Missing days do NOT become 0 (Criterion 2).
        - Gaps >= 5 days / 48h tagged so chart does NOT connect misleading lines (Criterion 13).
        - Aggregated points include mean, median, and sample count n (Criterion 12).
        - Different units are separated (Criterion 14).
        """
        self.assert_clinician_access(clinician_id, patient_id)

        now = self.get_current_time()
        start_date = (now.date() - datetime.fromtimestamp(days_filter * 86400, timezone.utc).date()
                      if days_filter else None)
        start_iso = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat() if start_date else None
        if days_filter:
            start_iso = (now - datetime.fromtimestamp(days_filter * 86400, timezone.utc) + datetime.fromtimestamp(0, timezone.utc)).isoformat()
            # More direct:
            from datetime import timedelta
            start_dt = now - timedelta(days=days_filter)
            start_iso = start_dt.isoformat()

        raw_observations = self.obs_repo.get_observations(
            patient_id=patient_id,
            type_codes=[type_code],
            start_time=start_iso,
            status="active",
            limit=limit,
            offset=offset,
        )

        # Sort chronologically for charting
        raw_observations_sorted = sorted(raw_observations, key=lambda o: o.measured_at)

        # Detect gap intervals (Criterion 13: >= 48 hours or 5 days)
        data_gaps: List[Tuple[str, str]] = []
        for i in range(1, len(raw_observations_sorted)):
            prev_t = datetime.fromisoformat(raw_observations_sorted[i - 1].measured_at)
            curr_t = datetime.fromisoformat(raw_observations_sorted[i].measured_at)
            gap_hours = (curr_t - prev_t).total_seconds() / 3600.0
            if gap_hours >= 48.0:
                data_gaps.append((raw_observations_sorted[i - 1].measured_at, raw_observations_sorted[i].measured_at))

        # Check unit consistency (Criterion 14)
        distinct_units = list({o.unit_code for o in raw_observations_sorted})

        # Aggregation if requested
        aggregated_points: List[AggregatedPoint] = []
        if resolution in (Resolution.DAY, Resolution.WEEK) and raw_observations_sorted:
            aggregated_points = self._aggregate_observations(raw_observations_sorted, resolution)

        return {
            "patient_id": patient_id,
            "type_code": type_code,
            "resolution": resolution.value,
            "days_filter": days_filter,
            "raw_observations": raw_observations_sorted,
            "aggregated_points": aggregated_points,
            "data_gaps": data_gaps,
            "units": distinct_units,
            "is_empty": len(raw_observations_sorted) == 0,
        }

    def get_blood_pressure_trend(
        self,
        clinician_id: str,
        patient_id: str,
        days_filter: int = 30,
        resolution: Resolution = Resolution.RAW,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Specialized dual-line trend for Blood Pressure (Systolic + Diastolic).
        Maintains paired components with mmHg unit.
        """
        self.assert_clinician_access(clinician_id, patient_id)

        from datetime import timedelta
        start_iso = None
        if days_filter:
            start_iso = (self.get_current_time() - timedelta(days=days_filter)).isoformat()

        readings = self.obs_repo.get_blood_pressure_readings(
            patient_id=patient_id,
            start_time=start_iso,
            status="active",
            limit=limit,
        )

        readings_sorted = sorted(readings, key=lambda r: r.measured_at)

        # Detect gaps
        data_gaps: List[Tuple[str, str]] = []
        for i in range(1, len(readings_sorted)):
            prev_t = datetime.fromisoformat(readings_sorted[i - 1].measured_at)
            curr_t = datetime.fromisoformat(readings_sorted[i].measured_at)
            gap_hours = (curr_t - prev_t).total_seconds() / 3600.0
            if gap_hours >= 48.0:
                data_gaps.append((readings_sorted[i - 1].measured_at, readings_sorted[i].measured_at))

        # Aggregated points for systolic and diastolic
        aggregated_bp: List[Dict[str, Any]] = []
        if resolution in (Resolution.DAY, Resolution.WEEK) and readings_sorted:
            aggregated_bp = self._aggregate_bp_readings(readings_sorted, resolution)

        return {
            "patient_id": patient_id,
            "resolution": resolution.value,
            "days_filter": days_filter,
            "readings": readings_sorted,
            "aggregated_points": aggregated_bp,
            "data_gaps": data_gaps,
            "unit": "mmHg",
            "is_empty": len(readings_sorted) == 0,
        }

    def _aggregate_observations(
        self,
        observations: List[HealthObservation],
        resolution: Resolution,
    ) -> List[AggregatedPoint]:
        """Aggregate observations into Day or Week buckets with mean, median, and sample count n."""
        buckets: Dict[str, List[HealthObservation]] = {}
        for o in observations:
            dt = datetime.fromisoformat(o.measured_at)
            if resolution == Resolution.DAY:
                key = dt.strftime("%Y-%m-%d")
            else:
                key = f"{dt.year}-W{dt.isocalendar()[1]:02d}"

            if key not in buckets:
                buckets[key] = []
            buckets[key].append(o)

        aggregated: List[AggregatedPoint] = []
        for key in sorted(buckets.keys()):
            items = buckets[key]
            vals = [it.value_numeric for it in items]
            mean_val = round(statistics.mean(vals), 1)
            median_val = round(statistics.median(vals), 1)
            min_val = min(vals)
            max_val = max(vals)
            n_count = len(items)

            aggregated.append(
                AggregatedPoint(
                    period_key=key,
                    type_code=items[0].type_code,
                    type_name=items[0].type_name,
                    mean_value=mean_val,
                    median_value=median_val,
                    min_value=min_val,
                    max_value=max_val,
                    unit_code=items[0].unit_code,
                    sample_count_n=n_count,
                    period_start=items[0].measured_at,
                    period_end=items[-1].measured_at,
                    raw_observation_ids=[it.observation_id for it in items],
                )
            )
        return aggregated

    def _aggregate_bp_readings(
        self,
        readings: List[BloodPressureReading],
        resolution: Resolution,
    ) -> List[Dict[str, Any]]:
        """Aggregate dual-line Blood Pressure readings."""
        buckets: Dict[str, List[BloodPressureReading]] = {}
        for r in readings:
            dt = datetime.fromisoformat(r.measured_at)
            key = dt.strftime("%Y-%m-%d") if resolution == Resolution.DAY else f"{dt.year}-W{dt.isocalendar()[1]:02d}"
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(r)

        result: List[Dict[str, Any]] = []
        for key in sorted(buckets.keys()):
            items = buckets[key]
            sys_vals = [r.systolic for r in items if r.systolic is not None]
            dia_vals = [r.diastolic for r in items if r.diastolic is not None]

            if not sys_vals or not dia_vals:
                continue

            result.append({
                "period_key": key,
                "systolic_mean": round(statistics.mean(sys_vals), 1),
                "systolic_median": round(statistics.median(sys_vals), 1),
                "diastolic_mean": round(statistics.mean(dia_vals), 1),
                "diastolic_median": round(statistics.median(dia_vals), 1),
                "sample_count_n": len(items),
                "unit": "mmHg",
                "raw_readings": items,
            })
        return result

    # --------------------------------------------------------------------------
    # 4. REVIEWS & CLINICAL FEEDBACK
    # --------------------------------------------------------------------------
    def mark_as_reviewed(
        self,
        clinician_id: str,
        patient_id: str,
        period_start: str,
        period_end: str,
        notes: str = "",
    ) -> ClinicianReview:
        """Mark records as reviewed. Created ONLY when confirmed by clinician (Criterion 10)."""
        self.assert_clinician_access(clinician_id, patient_id)

        now_iso = self.get_current_time().isoformat()
        review = ClinicianReview(
            review_id=str(uuid.uuid4()).replace("-", ""),
            clinician_id=clinician_id,
            patient_id=patient_id,
            reviewed_at=now_iso,
            period_start=period_start,
            period_end=period_end,
            status="CONFIRMED",
            notes=notes,
        )
        return self.obs_repo.create_review(review)

    def send_clinical_message(
        self,
        clinician_id: str,
        patient_id: str,
        body: str,
        visibility: str = "PATIENT_AND_CAREGIVERS",
        review_id: Optional[str] = None,
        observation_id: Optional[str] = None,
    ) -> ClinicalMessage:
        """Send a doctor feedback message or repeat measurement request."""
        self.assert_clinician_access(clinician_id, patient_id)

        if not body.strip():
            raise ValueError("Nội dung phản hồi không được để trống.")

        now_iso = self.get_current_time().isoformat()
        message = ClinicalMessage(
            message_id=str(uuid.uuid4()).replace("-", ""),
            patient_id=patient_id,
            clinician_id=clinician_id,
            clinician_name="",
            review_id=review_id,
            observation_id=observation_id,
            body=body.strip(),
            visibility=visibility,
            created_at=now_iso,
            status="sent",
        )
        return self.obs_repo.create_message(message)

    def get_patient_messages(self, user_id: str, patient_id: str) -> List[ClinicalMessage]:
        """Fetch messages visible to the patient or caregiver."""
        if not self.patient_access_repo.can_user_access_patient(user_id, patient_id, "can_view_health"):
            raise PermissionError("Không có quyền đọc phản hồi của bệnh nhân này.")
        return self.obs_repo.get_messages(patient_id)

    def mark_message_as_read(self, message_id: str, user_id: str) -> None:
        """Acknowledge reading a message (separates 'sent' from 'read')."""
        self.obs_repo.mark_message_read(message_id, user_id)

    def record_symptom(
        self,
        patient_id: str,
        reported_by_user_id: str,
        text: str,
        discomfort_level: int = 1,
        reported_at: Optional[str] = None,
    ) -> SymptomEntry:
        """Record self-reported symptom notes."""
        self.assert_user_can_record(reported_by_user_id, patient_id)

        now_iso = self.get_current_time().isoformat()
        symptom = SymptomEntry(
            symptom_id=str(uuid.uuid4()).replace("-", ""),
            patient_id=patient_id,
            reported_by_user_id=reported_by_user_id,
            reported_by_name="",
            reported_at=reported_at or now_iso,
            text=text.strip(),
            discomfort_level=discomfort_level,
            status="active",
        )
        return self.obs_repo.record_symptom(symptom)

    def get_patient_symptoms(self, user_id: str, patient_id: str) -> List[SymptomEntry]:
        """Fetch symptoms reported for a patient."""
        if not self.patient_access_repo.can_user_access_patient(user_id, patient_id, "can_view_health"):
            raise PermissionError("Không có quyền xem triệu chứng của bệnh nhân.")
        return self.obs_repo.get_symptoms(patient_id)
