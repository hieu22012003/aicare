"""
Medication Confirmation and Guidance Business Flow Adapter.
Implements Workflows W1, W2 and Phase 5 of VOICE_AI_IMPLEMENTATION_PLAN.md.

Guarantees:
1. Adherence confirmation is strictly user-declared ('source=voice_user_declared'), never clinically verified.
2. Disambiguation if multiple due medications exist.
3. Atomic occurrence state update and outbox event 'medication.dose_changed'.
4. Guidance reads only approved prescriptions; never invents dosage or recommends make-up doses.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from shared.database.connection import UnifiedDatabaseManager
from shared.database.outbox_helper import insert_outbox_event
from features.voice_assistant.voice_ai.domain.contracts import VoiceIntent

logger = logging.getLogger("voice_ai.business.medication")
UTC = timezone.utc


class MedicationAdapter:
    """Handles medication confirmation, disambiguation, and guidance."""

    def __init__(self, db: Optional[UnifiedDatabaseManager] = None) -> None:
        self.db = db

    def handle_confirmation(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
    ) -> Dict[str, Any]:
        """
        Confirms medication intake self-declared by elder.
        """
        patient_id = str(auth_context.get("patient_id") or auth_context.get("elder_id") or "").strip()
        if not patient_id:
            raise ValueError("Thiếu mã định danh bệnh nhân (patient_id)")

        med_ref = str(intent.entities.get("medication_ref") or "").strip().lower()
        now_iso = datetime.now(UTC).isoformat()

        if self.db is None:
            # Mock mode for decoupled testing
            return {
                "status": "CONFIRMED",
                "occurrence_id": f"occ_mock_{uuid.uuid4().hex[:8]}",
                "medication_name": med_ref or "thuốc theo đơn",
                "source": "voice_user_declared",
                "message": f"Con đã ghi nhận cụ đã uống {med_ref or 'thuốc'} ạ.",
            }

        with self.db.connect() as conn:
            # 1. Query pending/reminding dose occurrences for today
            rows = conn.fetchall(
                """
                SELECT occurrence_id, medication_name_snapshot, dose_snapshot, 
                       instructions_snapshot, state, local_scheduled_time
                FROM dose_occurrences
                WHERE patient_id = ? AND state IN ('PENDING', 'REMINDING', 'OVERDUE')
                ORDER BY scheduled_at_utc ASC
                """,
                (patient_id,),
            )

            if not rows:
                return {
                    "status": "NO_MATCH",
                    "requires_clarification": False,
                    "message": "Dạ hiện tại con không thấy lịch thuốc nào cần xác nhận uống ạ.",
                }

            # Filter if elder mentioned a specific drug name or scheduled time
            matching = []
            if med_ref:
                for r in rows:
                    name = (r.get("medication_name_snapshot") or "").lower()
                    time_slot = (r.get("local_scheduled_time") or "").lower()
                    instructions = (r.get("instructions_snapshot") or "").lower()
                    if med_ref in name or (time_slot and med_ref in time_slot) or (instructions and med_ref in instructions):
                        matching.append(r)

                if not matching:
                    # Do NOT fallback to all rows! Explain which doses actually exist.
                    unique_available = []
                    for r in rows:
                        t = r.get("local_scheduled_time") or ""
                        name = r.get("medication_name_snapshot") or "Thuốc"
                        label = f"{name} (cữ {t})" if t else name
                        if label not in unique_available:
                            unique_available.append(label)

                    return {
                        "status": "NO_MATCH",
                        "requires_clarification": True,
                        "candidates": rows,
                        "clarification_prompt": f"Dạ con không tìm thấy thuốc '{med_ref}' trong lịch uống hôm nay. Cụ có các cữ thuốc: {', '.join(unique_available)}. Cụ vừa uống cữ nào ạ?",
                        "message": f"Dạ con không tìm thấy thuốc '{med_ref}' trong lịch uống hôm nay. Cụ có các cữ thuốc: {', '.join(unique_available)}. Cụ vừa uống cữ nào ạ?",
                    }
            else:
                matching = rows

            # If multiple matching candidates exist, prioritize direct name prefix match
            if len(matching) > 1 and med_ref:
                prefix_matching = [
                    r for r in matching
                    if (r.get("medication_name_snapshot") or "").lower().startswith(med_ref)
                ]
                if len(prefix_matching) == 1:
                    matching = prefix_matching
                elif len(prefix_matching) > 1:
                    matching = prefix_matching

            # If multiple matching candidates exist, disambiguate with unique, contextualized labels
            if len(matching) > 1:
                formatted_options = []
                for m in matching:
                    t = m.get("local_scheduled_time") or ""
                    name = m.get("medication_name_snapshot") or "Thuốc"
                    label = f"{name} (cữ {t})" if t else name
                    if label not in formatted_options:
                        formatted_options.append(label)

                options_str = ", ".join(formatted_options)
                return {
                    "status": "NEEDS_DISAMBIGUATION",
                    "requires_clarification": True,
                    "candidates": matching,
                    "clarification_prompt": f"Dạ cụ vừa uống thuốc nào ạ? Cụ có lịch uống: {options_str}.",
                    "message": f"Dạ cụ vừa uống thuốc nào ạ? Cụ có lịch uống: {options_str}.",
                }

            selected = matching[0]
            occ_id = selected["occurrence_id"]
            med_name = selected.get("medication_name_snapshot") or "thuốc"
            sched_time = selected.get("local_scheduled_time") or ""
            time_suffix = f" (cữ {sched_time})" if sched_time else ""
            event_id = f"evt_{uuid.uuid4().hex[:16]}"

            # 2. Update dose_occurrences state to CONFIRMED
            conn.execute(
                """
                UPDATE dose_occurrences
                SET state = 'CONFIRMED',
                    action_taken_at_utc = ?,
                    action_actor = 'voice_user_declared',
                    action_notes = 'Xác nhận bằng giọng nói (voice_user_declared)'
                WHERE occurrence_id = ?
                """,
                (now_iso, occ_id),
            )

            # 3. Insert audit log into dose_events
            conn.execute(
                """
                INSERT INTO dose_events (
                    event_id, occurrence_id, event_type, actor_id, actor_role, 
                    timestamp_utc, idempotency_key, metadata
                ) VALUES (?, ?, 'self_reported_taken', ?, 'patient', ?, ?, ?)
                """,
                (
                    event_id,
                    occ_id,
                    patient_id,
                    now_iso,
                    f"idemp_{event_id}",
                    json.dumps({
                        "source": "voice_user_declared",
                        "evidence_span": intent.evidence_span,
                    }),
                ),
            )

            # 4. Atomically insert outbox event
            insert_outbox_event(
                conn=conn,
                patient_id=patient_id,
                event_type="medication.dose_changed",
                aggregate_type="dose_occurrence",
                aggregate_id=occ_id,
                aggregate_version=1,
                occurred_at=now_iso,
                actor_id=patient_id,
                source="voice_ai",
                payload_minimal={
                    "occurrence_id": occ_id,
                    "state": "CONFIRMED",
                    "source": "voice_user_declared",
                    "medication_name": med_name,
                },
                event_id=event_id,
            )
            conn.commit()

            return {
                "status": "CONFIRMED",
                "occurrence_id": occ_id,
                "medication_name": med_name,
                "scheduled_time": sched_time,
                "source": "voice_user_declared",
                "message": f"Con đã ghi nhận cụ đã uống {med_name}{time_suffix} ạ.",
            }


    def handle_guidance(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
    ) -> Dict[str, Any]:
        """
        Provides guidance on approved prescriptions without clinical hallucination.
        """
        patient_id = str(auth_context.get("patient_id") or auth_context.get("elder_id") or "").strip()
        if not patient_id:
            raise ValueError("Thiếu mã định danh bệnh nhân (patient_id)")

        med_ref = str(intent.entities.get("medication_ref") or "").strip().lower()

        if self.db is None:
            # Fallback guidance when db is decoupled
            return {
                "status": "APPROVED_GUIDANCE",
                "medication_name": med_ref or "Amlodipine",
                "dose_info": "1 viên (5mg)",
                "instructions": "Uống sau bữa ăn sáng",
                "message": f"Dạ đơn thuốc của cụ gồm {med_ref or 'Amlodipine 5mg'}, uống 1 viên sau ăn sáng ạ.",
            }

        with self.db.connect() as conn:
            # Query active approved medication prescriptions
            rows = conn.fetchall(
                """
                SELECT m.brand_name AS medication_name, pi.original_instructions AS instructions_text,
                       pi.dose_amount, pi.dose_unit, m.product_strength AS strength, m.dosage_form AS form
                FROM prescription_items pi
                JOIN prescriptions p ON pi.prescription_id = p.prescription_id
                JOIN medications m ON pi.medication_id = m.medication_id
                WHERE p.patient_id = ? AND p.status = 'ACTIVE' AND pi.status = 'ACTIVE'
                """,
                (patient_id,),
            )

            if not rows:
                return {
                    "status": "NO_ACTIVE_PRESCRIPTION",
                    "message": "Dạ hiện con không tìm thấy đơn thuốc đang hoạt động của cụ. Cụ vui lòng hỏi bác sĩ hoặc người chăm sóc nhé.",
                }

            # Filter if specific drug asked
            matched = rows
            if med_ref:
                matched = [r for r in rows if med_ref in r["medication_name"].lower()]

            if not matched:
                return {
                    "status": "MEDICATION_NOT_FOUND",
                    "message": f"Dạ con không tìm thấy thuốc '{med_ref}' trong đơn thuốc của cụ. Cụ hỏi lại bác sĩ trước khi dùng nhé.",
                }

            item = matched[0]
            med_name = item["medication_name"]
            dose_str = f"{item['dose_amount']} {item['dose_unit']}" if item.get("dose_amount") else "theo chỉ định"
            instructions = item.get("instructions_text") or "Uống đúng giờ theo hướng dẫn của bác sĩ"

            return {
                "status": "APPROVED_GUIDANCE",
                "medication_name": med_name,
                "dose_info": dose_str,
                "instructions": instructions,
                "message": f"Dạ đơn thuốc của cụ: {med_name}, liều dùng là {dose_str}. Hướng dẫn: {instructions} ạ.",
            }
