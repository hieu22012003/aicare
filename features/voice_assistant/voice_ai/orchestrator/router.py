"""
Workflow Orchestrator and Intent Router for Voice AI.
Implements Section 4, Section 5, and Phase 4 of VOICE_AI_IMPLEMENTATION_PLAN.md.

Non-Negotiables Enforced:
1. Fast-Path Bypass: Urgent doctor assistance bypasses LLM in <5ms.
2. AI Interprets, Backend Authorizes: LLM never executes commands or writes directly to DB.
3. Priority Scheduling: P1 (Assistance) -> P2 (Health) -> P3 (Medication) -> P4 (Entertainment).
4. Failure Isolation: Medication failure never rolls back committed assistance.
5. Idempotency: Request deduplication prevents double-actions.
6. Safe Client Actions: Only strictly allowlisted tokens emitted to UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, List, Optional
import uuid

from features.voice_assistant.voice_ai.domain.contracts import (
    ActionPayload,
    ExecutionStatus,
    INTENT_PRIORITY_MAP,
    IntentExecutionResult,
    IntentPriority,
    IntentType,
    OrchestratorExecutionResult,
    RedFlagSeverity,
    SafetySignal,
    SemanticResult,
    VoiceIntent,
)
from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
from features.voice_assistant.voice_ai.business_adapters.health_adapter import HealthReportAdapter
from features.voice_assistant.voice_ai.business_adapters.medication_adapter import MedicationAdapter
from features.voice_assistant.voice_ai.business_adapters.media_games_adapter import MediaAndGamesAdapter
from features.voice_assistant.voice_ai.orchestrator.actions import (
    ActionType,
    make_assistance_alert_action,
    make_game_open_action,
    make_media_action,
    make_rehab_start_action,
)
from features.voice_assistant.voice_ai.orchestrator.context_resolver import ContextResolver
from features.voice_assistant.voice_ai.orchestrator.idempotency import IdempotencyManager
from features.voice_assistant.voice_ai.orchestrator.state_machine import SessionState, VoiceSessionStateMachine
from features.voice_assistant.voice_ai.observability.tracer import (

    TraceStage,
    mask_patient_id,
    mask_transcript_preview,
    start_trace,
)
from features.voice_assistant.voice_ai.safety.assistance_rules import DeterministicSafetyDetector
from features.voice_assistant.voice_ai.semantic.factory import get_semantic_provider
from features.voice_assistant.voice_ai.semantic.provider import SemanticProvider

logger = logging.getLogger("voice_ai.orchestrator.router")



class WorkflowOrchestrator:
    """
    Central Coordinator for Voice AI requests.
    Validates, schedules, isolates, and routes voice intents.
    """

    def __init__(
        self,
        safety_detector: Optional[DeterministicSafetyDetector] = None,
        semantic_provider: Optional[SemanticProvider] = None,
        context_resolver: Optional[ContextResolver] = None,
        idempotency_manager: Optional[IdempotencyManager] = None,
        assistance_handler: Optional[Callable[[Dict[str, Any], VoiceIntent], Dict[str, Any]]] = None,
        medication_handler: Optional[Callable[[Dict[str, Any], VoiceIntent], Dict[str, Any]]] = None,
        health_handler: Optional[Callable[[Dict[str, Any], VoiceIntent], Dict[str, Any]]] = None,
        db: Optional[Any] = None,
    ) -> None:
        self.db = db
        self.safety_detector = safety_detector or DeterministicSafetyDetector()
        self.semantic_provider = semantic_provider or get_semantic_provider()
        self.context_resolver = context_resolver or ContextResolver(db_manager=db)
        self.idempotency_manager = idempotency_manager or IdempotencyManager()
        self.assistance_handler = assistance_handler
        self.medication_handler = medication_handler
        self.health_handler = health_handler

        # Business Adapters
        self.assistance_adapter = DoctorAssistanceAdapter(db=db)
        self.medication_adapter = MedicationAdapter(db=db)
        self.health_adapter = HealthReportAdapter(db=db)
        self.media_games_adapter = MediaAndGamesAdapter()

        self._sessions: Dict[str, VoiceSessionStateMachine] = {}
        self._interactions: Dict[str, OrchestratorExecutionResult] = {}

    def get_or_create_session(self, patient_id: str, session_id: Optional[str] = None) -> VoiceSessionStateMachine:
        sid = session_id or f"vses_{uuid.uuid4().hex[:12]}"
        if sid not in self._sessions:
            self._sessions[sid] = VoiceSessionStateMachine(patient_id=patient_id, session_id=sid)
        return self._sessions[sid]

    def get_interaction(self, interaction_id: str) -> Optional[OrchestratorExecutionResult]:
        """Lookup stored interaction result by interaction_id."""
        return self._interactions.get(interaction_id)

    def get_session_by_id(self, session_id: str) -> Optional[VoiceSessionStateMachine]:
        """Lookup active voice session by session_id."""
        return self._sessions.get(session_id)

    def process_voice_interaction(
        self,
        auth_context: Dict[str, Any],
        transcript: str,
        idempotency_key: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> OrchestratorExecutionResult:
        """
        Main pipeline processing a voice interaction.
        """
        interaction_id = f"vint_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.now(timezone.utc).isoformat()
        patient_id = str(auth_context.get("patient_id") or auth_context.get("elder_id") or "").strip()
        trace = start_trace(interaction_id=interaction_id, session_id=session_id, patient_id=patient_id)

        # 1. Idempotency Check
        if idempotency_key:
            cached_result = self.idempotency_manager.check(idempotency_key)
            if cached_result is not None:
                logger.info(f"Returning cached execution for idempotency_key={idempotency_key}")
                return cached_result

        # 2. Server-side identity validation
        if not patient_id:

            logger.error("Voice interaction rejected: missing authenticated patient_id")
            trace.finish("FAILED", error_code="AUTH_MISSING_PATIENT_ID")
            return OrchestratorExecutionResult(
                interaction_id=interaction_id,
                session_id=session_id or "unauthenticated",
                patient_id="unknown",
                timestamp_utc=now_utc,
                transcript=transcript,
                safety_signal=SafetySignal(),
                overall_status=ExecutionStatus.FAILED,
                response_speech_text="Lỗi xác thực người dùng.",
                trace_summary=trace.stage_latencies_ms,
            )

        session = self.get_or_create_session(patient_id, session_id)
        session.start_processing()

        # 3. Fast-Path Deterministic Safety Triage (<5ms)
        trace.start_stage(TraceStage.SAFETY_TRIAGE)
        safety_signal = self.safety_detector.detect(transcript)
        is_urgent = safety_signal.is_urgent_assistance or safety_signal.red_flag_severity == RedFlagSeverity.CRITICAL
        trace.end_stage(TraceStage.SAFETY_TRIAGE)

        # 4. Context Resolution (safe operational data only)
        operational_context = self.context_resolver.resolve_context(
            patient_id=patient_id,
            current_screen=auth_context.get("current_screen"),
            extra_state=auth_context.get("extra_state"),
        )

        intents_to_schedule: List[VoiceIntent] = []
        lower_text = transcript.lower().strip()

        # Check if there is an active pending clarification from prior turn
        if session.pending_clarification:
            if is_urgent:
                logger.info("Urgent assistance request superseding pending clarification.")
                session.cancel_action()
            else:
                # Explicit cancellation by user ("Thôi không cần nữa", "Hủy đi")
                is_explicit_cancel = any(w in lower_text for w in ["thôi", "hủy", "bỏ qua", "không cần", "không phải", "dừng lại"])
                if is_explicit_cancel:
                    logger.info("User explicitly cancelled pending action: '%s'", transcript)
                    session.cancel_action()
                    final_speech = "Dạ con đã hủy yêu cầu theo ý cụ rồi ạ."
                    trace_summary = trace.finish("COMPLETED")
                    res = OrchestratorExecutionResult(
                        interaction_id=interaction_id,
                        session_id=session.context.session_id,
                        patient_id=patient_id,
                        timestamp_utc=now_utc,
                        transcript=transcript,
                        safety_signal=safety_signal,
                        intent_results=[],
                        overall_status=ExecutionStatus.COMPLETED,
                        response_speech_text=final_speech,
                        trace_summary=trace_summary,
                    )
                    if idempotency_key:
                        self.idempotency_manager.store(idempotency_key, res)
                    self._interactions[interaction_id] = res
                    return res

                # Check if user requested an unrelated service
                has_unrelated_intent = any(k in lower_text for k in ["nhạc", "bài hát", "trò chơi", "lật thẻ", "game", "bác sĩ", "tập"])
                if has_unrelated_intent:
                    logger.info("User switched topics; cancelling pending clarification.")
                    session.cancel_action()
                elif session.pending_intent and session.pending_intent.type == IntentType.MEDICATION_CONFIRM:
                    if session.context.turn_count >= 2:
                        # Exceeded clarification retries: gracefully exit without infinite repeating
                        logger.info("Clarification turn limit reached (>=2). Resetting session.")
                        session.reset()
                        final_speech = "Dạ con xin phép tạm gác lại câu hỏi xác nhận thuốc. Khi nào cụ cần xác nhận thuốc, cụ cứ bảo con nhé."
                        trace_summary = trace.finish("COMPLETED")
                        res = OrchestratorExecutionResult(
                            interaction_id=interaction_id,
                            session_id=session.context.session_id,
                            patient_id=patient_id,
                            timestamp_utc=now_utc,
                            transcript=transcript,
                            safety_signal=safety_signal,
                            intent_results=[],
                            overall_status=ExecutionStatus.COMPLETED,
                            response_speech_text=final_speech,
                            trace_summary=trace_summary,
                        )
                        if idempotency_key:
                            self.idempotency_manager.store(idempotency_key, res)
                        self._interactions[interaction_id] = res
                        return res
                    elif lower_text and not any(w in lower_text for w in ["không", "chưa", "hủy"]):
                        clarified_intent = session.pending_intent
                        is_confirmation_only = any(w in lower_text for w in ["đã uống", "uống rồi", "rồi", "vâng", "đúng", "rồi ạ"])
                        if is_confirmation_only:
                            # Context resolution: resolve from pending intent or clarification prompt
                            if not clarified_intent.entities.get("medication_ref"):
                                prompt_text = (session.clarification_prompt or "").lower()
                                found_med = None
                                if "thuốc" in prompt_text:
                                    m = re.search(r"thuốc\s+([a-zA-Z0-9_\u00C0-\u024F\u1EA0-\u1EF9\s]+?)(?:\s+chưa|\s*\?|$)", prompt_text)
                                    if m:
                                        found_med = "thuốc " + m.group(1).strip()
                                if not found_med and operational_context.get("pending_medications"):
                                    found_med = operational_context["pending_medications"][0]
                                if found_med:
                                    clarified_intent.entities["medication_ref"] = found_med
                        else:
                            clarified_intent.entities["medication_ref"] = transcript.strip()

                        clarified_intent.requires_clarification = False
                        intents_to_schedule.append(clarified_intent)
                        session.cancel_action()

        # 5. Check if utterance is pure urgent assistance (Fast-Path LLM Bypass)
        words = transcript.strip().split()
        is_short_urgent_only = is_urgent and len(words) <= 6 and not any(
            w in transcript.lower() for w in ["thuốc", "nhạc", "chơi", "game"]
        )

        if not intents_to_schedule:
            if is_short_urgent_only:
                # Bypass LLM completely for speed & reliability
                logger.info("Fast-path bypass triggered: dispatching DOCTOR_ASSISTANCE without LLM roundtrip")
                intents_to_schedule.append(
                    VoiceIntent(
                        type=IntentType.DOCTOR_ASSISTANCE,
                        entities={"reason": safety_signal.urgency_reason or "Yêu cầu khẩn cấp", "urgency": "high"},
                        evidence_span=transcript[:100],
                        requires_clarification=False,
                        priority=IntentPriority.P1,
                    )
                )
            else:
                # 6. Semantic API Interpretation (Gemini Flash or Mock)
                trace.start_stage(TraceStage.SEMANTIC_ANALYSIS)
                semantic_result: SemanticResult = self.semantic_provider.analyze(
                    transcript=transcript,
                    sanitized_context=operational_context,
                )
                trace.end_stage(TraceStage.SEMANTIC_ANALYSIS)
                raw_intents = semantic_result.intents

                # Merge with Fast-Path signals
                has_doctor_intent = any(i.type == IntentType.DOCTOR_ASSISTANCE for i in raw_intents)
                if is_urgent and not has_doctor_intent:
                    intents_to_schedule.append(
                        VoiceIntent(
                            type=IntentType.DOCTOR_ASSISTANCE,
                            entities={"reason": safety_signal.urgency_reason or "Yêu cầu khẩn cấp", "urgency": "high"},
                            evidence_span=transcript[:100],
                            requires_clarification=False,
                            priority=IntentPriority.P1,
                        )
                    )

                for item in raw_intents:
                    if safety_signal.has_negation and item.type == IntentType.MEDICATION_CONFIRM:
                        logger.warning(f"Overriding MEDICATION_CONFIRM to CLARIFY due to safety negation: '{transcript}'")
                        intents_to_schedule.append(
                            VoiceIntent(
                                type=IntentType.CLARIFY,
                                entities={},
                                evidence_span=item.evidence_span,
                                requires_clarification=True,
                                clarification_prompt="Dạ cụ đã uống thuốc của cữ này chưa ạ?",
                                priority=IntentPriority.NONE,
                            )
                        )
                    else:
                        intents_to_schedule.append(item)

        # 7. Priority Scheduling (P1 -> P2 -> P3 -> P4 -> NONE)
        priority_rank = {
            IntentPriority.P1: 1,
            IntentPriority.P2: 2,
            IntentPriority.P3: 3,
            IntentPriority.P4: 4,
            IntentPriority.NONE: 5,
        }
        intents_to_schedule.sort(key=lambda i: priority_rank.get(i.priority, 5))

        # 8. Per-Intent Execution & Tracking (with failure isolation)
        trace.start_stage(TraceStage.WORKFLOW_EXECUTION)
        intent_results: List[IntentExecutionResult] = []
        speech_messages: List[str] = []
        overall_status = ExecutionStatus.COMPLETED

        for idx, intent in enumerate(intents_to_schedule):
            intent_id = f"int_{uuid.uuid4().hex[:10]}"
            trace.record_intent(intent.type.value)

            if intent.requires_clarification:
                session.set_awaiting_clarification(
                    intent=intent,
                    question=intent.clarification_prompt or "Dạ cụ vừa nói gì ạ?",
                )
                intent_results.append(
                    IntentExecutionResult(
                        intent_id=intent_id,
                        intent_type=intent.type,
                        status=ExecutionStatus.NEEDS_CLARIFICATION,
                        priority=intent.priority,
                        message_to_elder=intent.clarification_prompt,
                    )
                )
                if intent.clarification_prompt:
                    speech_messages.append(intent.clarification_prompt)
                overall_status = ExecutionStatus.NEEDS_CLARIFICATION
                continue

            # Execute individual intent handler
            exec_res = self._execute_single_intent(auth_context, intent, intent_id)
            intent_results.append(exec_res)

            if exec_res.message_to_elder:
                speech_messages.append(exec_res.message_to_elder)

            if exec_res.status == ExecutionStatus.NEEDS_CLARIFICATION:
                overall_status = ExecutionStatus.NEEDS_CLARIFICATION
            elif exec_res.status == ExecutionStatus.FAILED and overall_status == ExecutionStatus.COMPLETED:
                has_completed_urgent = any(
                    r.status == ExecutionStatus.COMPLETED and r.priority == IntentPriority.P1
                    for r in intent_results
                )
                if not has_completed_urgent:
                    overall_status = ExecutionStatus.FAILED

        trace.end_stage(TraceStage.WORKFLOW_EXECUTION)

        # 9. Synthesize Vietnamese Response Speech
        final_speech = " ".join(speech_messages).strip()
        if not final_speech:
            final_speech = "Con đã hiểu và ghi nhận yêu cầu của cụ ạ."

        trace_summary = trace.finish(status=overall_status.value)

        response = OrchestratorExecutionResult(
            interaction_id=interaction_id,
            session_id=session.context.session_id,
            patient_id=patient_id,
            timestamp_utc=now_utc,
            transcript=transcript,
            safety_signal=safety_signal,
            intent_results=intent_results,
            overall_status=overall_status,
            response_speech_text=final_speech,
            trace_summary=trace_summary,
        )

        if overall_status == ExecutionStatus.COMPLETED:
            session.dispatch_completed(outcome_summary=final_speech)
        elif overall_status == ExecutionStatus.FAILED:
            session.set_error(final_speech)
        # Note: If NEEDS_CLARIFICATION, session remains in AWAITING_CLARIFICATION!

        # 10. Cache in Idempotency Manager & Interaction History
        if idempotency_key:
            self.idempotency_manager.store(idempotency_key, response)

        self._interactions[interaction_id] = response
        return response


    def _execute_single_intent(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
        intent_id: str,
    ) -> IntentExecutionResult:
        """
        Executes a validated intent against authoritative services.
        Isolated: An exception in one handler does not abort subsequent or previous intents.
        """
        try:
            if intent.type == IntentType.DOCTOR_ASSISTANCE:
                return self._handle_doctor_assistance(auth_context, intent, intent_id)

            elif intent.type == IntentType.HEALTH_REPORT:
                return self._handle_health_report(auth_context, intent, intent_id)

            elif intent.type == IntentType.MEDICATION_CONFIRM:
                return self._handle_medication_confirm(auth_context, intent, intent_id)

            elif intent.type == IntentType.MEDICATION_NOT_TAKEN:
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    message_to_elder="Con đã ghi nhận cụ chưa uống thuốc. Cụ nhớ uống đúng giờ nhé.",
                )

            elif intent.type == IntentType.MEDICATION_GUIDANCE:
                guide_res = self.medication_adapter.handle_guidance(auth_context, intent)
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    message_to_elder=guide_res.get("message", "Con đang hiển thị hướng dẫn uống thuốc trên màn hình cho cụ ạ."),
                )

            elif intent.type == IntentType.MEDICATION_REMIND_LATER:
                delay = intent.entities.get("delay_minutes", 15)
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    message_to_elder=f"Dạ con sẽ nhắc cụ uống thuốc sau {delay} phút nữa ạ.",
                )

            elif intent.type == IntentType.REHABILITATION_START:
                exercise = intent.entities.get("exercise_type", "sit_to_stand")
                rehab_action = make_rehab_start_action(exercise_type=exercise)
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    client_action=ActionPayload(
                        action_type=rehab_action.action_type.value,
                        parameters=rehab_action.payload,
                    ),
                    message_to_elder="Con mở bài tập phục hồi chức năng cho cụ ngay đây ạ.",
                )

            elif intent.type == IntentType.GAME_LAUNCH:
                game_res = self.media_games_adapter.handle_game(auth_context, intent)
                action_dict = game_res.get("action", {})
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    client_action=ActionPayload(
                        action_type=action_dict.get("action_type", "game.open"),
                        parameters=action_dict.get("payload", {}),
                    ),
                    message_to_elder=game_res.get("message", "Con mở trò chơi cho cụ ngay đây ạ."),
                )

            elif intent.type == IntentType.MUSIC_PLAY:
                media_res = self.media_games_adapter.handle_music(auth_context, intent)
                action_dict = media_res.get("action", {})
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    client_action=ActionPayload(
                        action_type=action_dict.get("action_type", "media.play"),
                        parameters=action_dict.get("payload", {}),
                    ),
                    message_to_elder=media_res.get("message", "Con bật nhạc cho cụ nghe đây ạ."),
                )

            elif intent.type == IntentType.MUSIC_PAUSE:
                media_res = self.media_games_adapter.handle_music(auth_context, intent)
                action_dict = media_res.get("action", {})
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    client_action=ActionPayload(
                        action_type=action_dict.get("action_type", "media.pause"),
                        parameters=action_dict.get("payload", {}),
                    ),
                    message_to_elder=media_res.get("message", "Con đã tạm dừng nhạc ạ."),
                )

            elif intent.type == IntentType.MUSIC_STOP:
                media_res = self.media_games_adapter.handle_music(auth_context, intent)
                action_dict = media_res.get("action", {})
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.COMPLETED,
                    priority=intent.priority,
                    client_action=ActionPayload(
                        action_type=action_dict.get("action_type", "media.stop"),
                        parameters=action_dict.get("payload", {}),
                    ),
                    message_to_elder=media_res.get("message", "Con đã tắt nhạc rồi ạ."),
                )

            else:
                return IntentExecutionResult(
                    intent_id=intent_id,
                    intent_type=intent.type,
                    status=ExecutionStatus.NEEDS_CLARIFICATION,
                    priority=intent.priority,
                    message_to_elder=intent.clarification_prompt or "Dạ con chưa hiểu rõ yêu cầu, cụ nói lại giúp con nhé.",
                )

        except Exception as ex:
            logger.error(f"Error executing intent {intent.type}: {ex}")
            return IntentExecutionResult(
                intent_id=intent_id,
                intent_type=intent.type,
                status=ExecutionStatus.FAILED,
                priority=intent.priority,
                error_code="EXECUTION_ERROR",
                error_message=str(ex),
                message_to_elder="Dạ thao tác này tạm thời gặp lỗi xử lý ạ.",
            )

    def _handle_doctor_assistance(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
        intent_id: str,
    ) -> IntentExecutionResult:
        urgency = intent.entities.get("urgency", "high")
        symptoms = intent.entities.get("symptoms", intent.entities.get("reason", ""))

        if self.assistance_handler:
            result = self.assistance_handler(auth_context, intent)
        else:
            result = self.assistance_adapter.handle_assistance(auth_context, intent)

        req_id = result.get("request_id", f"ast_{uuid.uuid4().hex[:8]}")
        msg = result.get("message", "Đã gửi yêu cầu trợ giúp đến bác sĩ trực. Bác sĩ sẽ phản hồi ngay ạ.")

        action = make_assistance_alert_action(request_id=req_id, urgency=urgency, symptoms=symptoms)
        return IntentExecutionResult(
            intent_id=intent_id,
            intent_type=IntentType.DOCTOR_ASSISTANCE,
            status=ExecutionStatus.COMPLETED,
            priority=IntentPriority.P1,
            aggregate_id=req_id,
            aggregate_type="assistance_requests",
            client_action=ActionPayload(
                action_type=action.action_type.value,
                parameters=action.payload,
            ),
            message_to_elder=msg,
        )

    def _handle_health_report(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
        intent_id: str,
    ) -> IntentExecutionResult:
        if self.health_handler:
            result = self.health_handler(auth_context, intent)
        else:
            result = self.health_adapter.handle_health_report(auth_context, intent)

        rep_id = result.get("report_id", f"hlth_{uuid.uuid4().hex[:8]}")
        msg = result.get("message", "Con đã ghi nhận tình trạng sức khỏe của cụ vào sổ theo dõi ạ.")

        return IntentExecutionResult(
            intent_id=intent_id,
            intent_type=IntentType.HEALTH_REPORT,
            status=ExecutionStatus.COMPLETED,
            priority=IntentPriority.P2,
            aggregate_id=rep_id,
            aggregate_type="health_observations",
            message_to_elder=msg,
        )

    def _handle_medication_confirm(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
        intent_id: str,
    ) -> IntentExecutionResult:
        if self.medication_handler:
            result = self.medication_handler(auth_context, intent)
        else:
            result = self.medication_adapter.handle_confirmation(auth_context, intent)

        if result.get("requires_clarification") or result.get("status") in ("NEEDS_DISAMBIGUATION", "NO_MATCH"):
            return IntentExecutionResult(
                intent_id=intent_id,
                intent_type=IntentType.MEDICATION_CONFIRM,
                status=ExecutionStatus.NEEDS_CLARIFICATION,
                priority=IntentPriority.P3,
                message_to_elder=result.get("message", "Dạ cụ uống thuốc nào ạ?"),
            )

        med_id = result.get("occurrence_id", f"med_{uuid.uuid4().hex[:8]}")
        msg = result.get("message", "Con đã ghi nhận cụ đã uống thuốc ạ.")

        # Medication adherence is strictly user-declared, never clinically verified
        return IntentExecutionResult(
            intent_id=intent_id,
            intent_type=IntentType.MEDICATION_CONFIRM,
            status=ExecutionStatus.COMPLETED,
            priority=IntentPriority.P3,
            aggregate_id=med_id,
            aggregate_type="medication_administrations",
            message_to_elder=msg,
        )
