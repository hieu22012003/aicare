"""Validate captured context and dispatch through existing button services."""
from datetime import datetime, timezone
from features.voice_assistant.local_voice.intents import resolve_intent


class CommandDispatcher:
    def __init__(self, window):
        self.window = window
        self.epoch = 0
        self.used = set()

    def invalidate(self):
        self.epoch += 1
        widget = getattr(self.window, 'local_voice_widget', None)
        routing = getattr(widget, 'request_coordinator', None)
        if routing:
            routing.cancel()
        if widget:
            widget.clear_review()
        controller = getattr(self.window, 'local_voice_controller', None)
        if controller:
            current = self.snapshot()
            recording = controller.state in ('starting', 'recording', 'stopping', 'transcribing')
            same_patient = bool(current and controller.context and
                                current.get('patient_id') == controller.context.get('patient_id'))
            if not (recording and same_patient):
                controller.cancel()

    def snapshot(self):
        w = self.window
        todo = w.today_todo_widget
        raw = w.current_patient_id
        if w.router.face_adapter.get_latest_recognition().get('status') == 'unknown':
            return None
        if not raw or not w.current_patient_name or w.current_patient_name.startswith('Người lạ'):
            return None
        # Voice identity is a verified DB mapping, never dashboard's legacy guessed IDs.
        with todo.db.connect() as conn:
            direct = conn.fetchone("SELECT patient_id FROM patients WHERE patient_id=? AND status='ACTIVE'",(str(raw),))
            mapped = conn.fetchall("SELECT p.patient_id FROM legacy_patient_map m JOIN patients p ON p.patient_id=m.patient_id WHERE m.legacy_id=? AND m.source_system='camera_face' AND p.status='ACTIVE'",(str(raw),))
        if direct:
            patient = direct['patient_id']
        elif len(mapped)==1:
            patient = mapped[0]['patient_id']
        else:
            return None
        if todo.patient_id != patient or todo.sync_client.patient_id != patient:
            return None
        from features.voice_assistant.transcript_intent.context import read_state
        current = read_state(todo.db, patient)
        if current is None:
            return None
        doses, sessions, plans = current['doses'], current['sessions'], current['plans']
        # Entire dose snapshot includes action timestamps and prescription revision.
        return dict(patient_id=patient, epoch=self.epoch, doses=doses, plans=plans, sessions=sessions,
                    exercise_session_id=next((s['session_id'] for s in sessions if s['session_id']==getattr(w,'_active_db_session_id',None)),None),
                    tab=w.medication_tabs.currentIndex(), exercise=w.active_exercise_id,
                    source=w.current_source_mode,
                    running=w.btn_stop.isEnabled(), paused=w.router.rehab_adapter.engine.is_paused,
                    plan_signature=w._last_rehab_plans_signature)

    def dispatch(self, result, captured, confirm, proposed=None):
        command_id = result['session_id']
        if command_id in self.used:
            return 'Lệnh này đã được xử lý.'
        if self.snapshot() != captured:
            return 'Ngữ cảnh đã đổi. Hãy chọn nút hoặc nói lại.'
        from shared.ai_runtime.app_hooks import voice_reply
        ai_reply = voice_reply((captured or {}).get('patient_id'), result.get('transcript', ''))
        if ai_reply:
            self.used.add(command_id)
            return ai_reply
        if proposed is not None:
            from features.voice_assistant.transcript_intent.policy import validate_proposal
            from features.voice_assistant.transcript_intent.registry import REGISTRY
            from features.voice_assistant.transcript_intent.schema import Decision
            value = validate_proposal(proposed, result['transcript'], captured)
            intent = REGISTRY[value.intent].legacy if value.decision == Decision.ROUTE and value.intent in REGISTRY else None
        else:
            intent = resolve_intent(result['transcript'])
        if intent is None:
            return 'Chưa rõ câu lệnh; hãy chọn nút trên màn hình.'
        w = self.window
        if intent in ('taken', 'snooze', 'help'):
            if captured['tab'] != 0 or len(captured['doses']) != 1:
                return 'Hãy chọn đúng lần uống trên màn hình; không thể đoán lần uống.'
            dose = captured['doses'][0]
            if intent == 'snooze' and dose['state'] == 'OVERDUE':
                return 'Lần uống đã quá hạn; hãy dùng nút trên màn hình.'
            if intent == 'taken':
                text = f"Xác nhận đã uống {dose['medication_name_snapshot']} — {dose['dose_snapshot']}\nLần uống: {dose['local_scheduled_time']}?"
                if not confirm(text):
                    return 'Chưa ghi nhận uống thuốc.'
            if self.snapshot() != captured:
                return 'Lần uống hoặc hồ sơ đã đổi; chưa thực hiện.'
            self.used.add(command_id)
            action = {'taken': 'self_reported_taken', 'snooze': 'snooze', 'help': 'help_requested'}[intent]
            response = w.today_todo_widget.sync_client.confirm_dose_taken(
                dose['occurrence_id'], action, client_event_id='voice_' + command_id,
                expected_context=dose, source='voice')
            w.today_todo_widget.refresh_data()
            if response.get('status') == 'processed':
                return 'Đã ghi nhận trong hệ thống. ' + ('Yêu cầu hỗ trợ đã được tạo.' if intent == 'help' else '')
            return 'Chưa ghi nhận: ' + response.get('error', 'chờ đồng bộ theo cơ chế hiện có.')
        if intent in ('start', 'pause'):
            if captured['tab'] != 1 or captured['source'] != 'camera' or not captured['plans']:
                return 'Hãy chọn bài tập và camera trên màn hình.'
            selected = [p for p in captured['plans'] if p['exercise_id'] == captured['exercise'] and p['is_active']]
            if not selected:
                return 'Kế hoạch bài tập không còn hoạt động; hãy dùng nút bấm.'
            if intent == 'start' and not captured['running']:
                self.used.add(command_id)
                w._on_start_exercise(expected_plan=max(selected, key=lambda p: p['version']))
                return 'Đã bắt đầu; camera sẽ xác nhận số lần tập.'
            if intent == 'pause' and captured['running'] and not captured['paused']:
                self.used.add(command_id)
                w._on_pause_exercise()
                return 'Đã tạm dừng bài tập.'
            return 'Trạng thái bài tập không phù hợp; hãy dùng nút bấm.'
        if intent == 'home':
            self.used.add(command_id)
            w.medication_tabs.setCurrentIndex(0)
            return 'Đã về trang chính.'
        return 'Hãy dùng nút bấm.'
