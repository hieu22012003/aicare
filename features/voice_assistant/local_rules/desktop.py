"""Qt boundary: widgets on UI thread, persistent/network work in background workers."""
import copy
import json
import time
import queue
import threading
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from features.voice_assistant.local_rules.engine import IntentEngine
from features.voice_assistant.local_rules.dialogue import Dialogue
from features.voice_assistant.local_rules.requests import RequestService


class LocalRulesCoordinator(QObject):
    changed = pyqtSignal(str, str)
    resolved = pyqtSignal(object, object, object)

    def __init__(self, dispatcher, parent=None, request_service=None):
        super().__init__(parent)
        self.dispatcher = dispatcher
        self.engine = IntentEngine()
        self.dialogue = Dialogue(self.engine.config['pending_seconds'])
        self.pending_message = ''
        self.context_provider = dispatcher.snapshot
        self.closed = False
        self.thread = None
        self.workers = []
        self.background_workers = set()
        self.results = queue.Queue()
        self.seen = set()
        self.repeated = {}
        self.last_request = None
        self.active_context = None
        self.cancel_send = threading.Event()
        if request_service is None:
            from shared.services.email_service import EmailService
            from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
            request_service = DoctorAssistanceAdapter(dispatcher.window.today_todo_widget.db).local_request_service(
                EmailService(mode='medication'), self.engine.config['retention_days'])
        self.requests = request_service
        for key in ('request_ttl_seconds','retry_seconds','retry_max'):
            setattr(self.requests,key,self.engine.config[key])
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self._last_retry = time.monotonic()
        self._work(lambda: self.requests.prune() or 'Bộ luật cục bộ đã sẵn sàng.', self.context_provider())

    @property
    def busy(self):
        return any(t.is_alive() for t in self.workers)

    @property
    def recording_blocked(self):
        return any(t.is_alive() and t not in self.background_workers for t in self.workers)

    def _ctx(self, snapshot):
        ctx = dict(snapshot or {})
        # Exactly one explicit current reminder on medication screen; never nearest dose.
        doses = ctx.get('doses', [])
        if ctx.get('tab') == 0 and len(doses) == 1:
            ctx['medication_occurrence_id'] = doses[0]['occurrence_id']
        return ctx

    def route(self, result, captured):
        if self.closed or not result.get('is_final') or result.get('status') != 'completed':
            return
        if self.recording_blocked:
            from features.voice_assistant.local_rules.engine import normalize
            if normalize(result.get('transcript','')) in ('hủy','báo nhầm','thôi không cần nữa') and captured==self.active_context:
                self.cancel_send.set()
                self.changed.emit('routing','Đang hủy/đính chính yêu cầu. Nếu đã gửi, hệ thống giữ dấu vết.')
                return
            priority_parse = self.engine.parse(result.get('transcript',''),self._ctx(captured),result.get('session_id'))
            if not any(a['intent'] in ('urgent_help','rehab_stop') for a in priority_parse['actions']):
                self.changed.emit('routing','Đang xử lý; hãy nói lại khi hoàn tất.')
                return
        uid = result.get('session_id')
        if not uid or uid in self.seen or self.context_provider() != captured:
            return
        self.seen.add(uid)
        if len(self.seen)>2048:
            # Notification dedup remains durable in the DB across process lifetimes.
            self.seen = {uid}
        ctx = self._ctx(captured)
        ctx['session_id'] = uid
        parsed = self.engine.parse(result.get('transcript',''),ctx,uid)
        key = (parsed['patient_id'],parsed['normalized_text'])
        if any(a['roles'] for a in parsed['actions']) and not result.get('allow_repeat'):
            previous = self.repeated.get(key)
            if previous is not None and time.monotonic()-previous<self.engine.config['repeat_seconds']:
                self.changed.emit('routing_finished','Câu này vừa được xử lý; dùng Gửi lại nếu bác chủ động muốn gửi thêm.')
                return
        if any(a['roles'] for a in parsed['actions']):
            self.repeated[key] = time.monotonic()
            if len(self.repeated)>1024:
                self.repeated = {key:self.repeated[key]}
        if any(a['roles'] for a in parsed['actions']):
            self.last_transcript = result.get('transcript','')
        intents = [a['intent'] for a in parsed['actions']]
        if intents == ['cancel']:
            self.dialogue.clear()
            self.repeated.clear()
            if parsed['normalized_text']=='báo nhầm' and self.last_request and captured and self.last_request[1]==captured['patient_id']:
                rid,patient = self.last_request
                self._work(lambda: (self.requests.cancel(rid,patient), 'Đã lưu đính chính; giữ dấu vết yêu cầu gốc.')[1],captured)
            else:
                self.changed.emit('routing_finished','Đã hủy; không gửi yêu cầu đang chờ.')
            return
        if intents == ['confirm']:
            self.confirm()
            return
        # A new request supersedes pending choices, including "Không, tôi cần bác sĩ".
        previous_attempts = self.dialogue.attempts
        self.dialogue.clear()
        immediate = []
        approved = []
        recipients = {}
        for action in parsed['actions']:
            intent = action['intent']
            if intent in ('ambiguous_help',):
                self.dialogue.attempts = previous_attempts
                self.changed.emit('routing_finished',self.dialogue.clarify())
                return
            if intent in ('confirm','cancel'):
                continue
            if intent=='medication_assistance':
                self._local(dict(intent='medication_schedule'),parsed,captured)
                captured=self.context_provider()
            if action['roles']:
                try:
                    recipients[intent] = self.requests.resolve(parsed['patient_id'],action['roles'])
                    if intent=='urgent_help':
                        gaps=[]
                        for role in action['roles']:
                            try:
                                self.requests.resolve(parsed['patient_id'],[role])
                            except ValueError:
                                gaps.append(role)
                        parsed['context_refs']['missing_roles']=gaps
                except ValueError as exc:
                    self.changed.emit('routing_finished',str(exc))
                    continue
            if intent=='urgent_help':
                immediate.append(action)
            elif action['needs_confirmation']:
                approved.append(action)
            else:
                self._local(action,parsed,captured)
                # Stop can change snapshot; bind subsequent approval to the new context.
                captured = self.context_provider()
        if immediate:
            value = dict(parsed,actions=immediate)
            self._execute(value,captured,recipients)
        elif approved:
            value = dict(parsed,actions=approved)
            self.dialogue.offer(value,captured,recipients)
            names = ', '.join(r['full_name'] for items in recipients.values() for r in items)
            question = f'Gửi nguyên văn tới {names}?'
            if any(a['intent']=='medication_taken' for a in approved):
                dose = captured['doses'][0]
                question = f"Xác nhận đã uống {dose['medication_name_snapshot']} — {dose['dose_snapshot']} lúc {dose['local_scheduled_time']}?" + (f' Đồng thời gửi tới {names}?' if names else '')
            self.pending_message = f'“{parsed["original_text"]}” — {question} Nhấn Xác nhận / Hủy hoặc nói Đồng ý / Hủy.'
            self.changed.emit('pending',self.pending_message)
        elif not parsed['actions']:
            message = parsed['clarification'] or 'Chưa hiểu. Hãy chọn Bác sĩ / Người nhà / Hủy.'
            if parsed['match_level']=='ambiguous':
                self.dialogue.attempts = previous_attempts
                choices = self.dialogue.clarify()
                if self.dialogue.attempts>2:
                    message = choices
            self.changed.emit('routing_finished',message)

    def confirm(self):
        if self.recording_blocked:
            return
        value = self.dialogue.approve(self.context_provider())
        if not value:
            self.repeated.clear()
            self.changed.emit('routing_finished','Không có yêu cầu còn hiệu lực để xác nhận.')
            return
        self._execute(*value)

    def _execute(self, decision, captured, recipients):
        if captured is None or self.context_provider()!=captured:
            self.changed.emit('routing_finished','Hồ sơ/ngữ cảnh đã đổi; chưa thực hiện.')
            return
        med = [a for a in decision['actions'] if a['intent']=='medication_taken']
        notifications = [a for a in decision['actions'] if a['roles']]
        if not notifications and not med:
            return
        patient = captured['patient_id']
        client = self.dispatcher.window.today_todo_widget.sync_client
        online = client.is_online
        doses = copy.deepcopy(captured.get('doses', []))
        if med and (captured.get('tab')!=0 or len(doses)!=1):
            self.changed.emit('routing_finished','Lượt thuốc không rõ; chưa thực hiện.')
            return
        self.active_context = copy.deepcopy(captured)
        self.cancel_send = threading.Event()
        cancellation = self.cancel_send
        def run():
            if cancellation.is_set():
                return 'Đã hủy; chưa thực hiện.'
            response = self._dose(client,doses[0],'self_reported_taken',decision['utterance_id']) if med else None
            ids = [self.requests.create(decision,a,recipients[a['intent']]) for a in notifications]
            if cancellation.is_set():
                for rid in ids:
                    self.requests.cancel(rid,patient)
            else:
                self.requests.deliver(online=online)
                if cancellation.is_set():
                    for rid in ids:
                        self.requests.cancel(rid,patient)
            statuses = [self.requests.status(rid) for rid in ids]
            return (ids, statuses, response) if ids else response
        self._work(run,captured,refresh=bool(med),urgent=any(a['intent']=='urgent_help' for a in notifications))

    def _local(self, action, decision, captured, confirmed=False):
        if not captured or self.context_provider()!=captured:
            self.changed.emit('routing_finished','Hãy chọn hồ sơ hợp lệ.')
            return
        w = self.dispatcher.window
        intent = action['intent']
        if intent=='rehab_stop':
            if captured.get('running') and not captured.get('paused'):
                w._on_pause_exercise()
            message = 'Đã tạm dừng; camera vẫn là nguồn xác nhận hoàn thành.'
        elif intent=='rehab_start':
            w.medication_tabs.setCurrentIndex(1)
            message = 'Đã mở bài tập đã chỉ định. Dùng nút Bắt đầu và camera để tập.'
        elif intent=='medication_schedule':
            w.medication_tabs.setCurrentIndex(0)
            message = 'Đã mở lịch thuốc đã kê.'
        elif intent in ('medication_taken','medication_snooze','medication_not_taken'):
            if intent=='medication_taken' and not confirmed:
                return
            doses = captured.get('doses', [])
            if captured.get('tab')!=0 or len(doses)!=1:
                self.changed.emit('routing_finished','Hãy chọn đúng lượt thuốc đang hỏi.')
                return
            dose = copy.deepcopy(doses[0])
            client = w.today_todo_widget.sync_client
            action_name = {'medication_taken':'self_reported_taken','medication_snooze':'snooze','medication_not_taken':'not_taken'}[intent]
            uid = decision['utterance_id']
            self._work(lambda: self._dose(client,dose,action_name,uid,action.get('parameters',{}).get('snooze_minutes')),captured,refresh=True)
            return
        else:
            message = 'Hãy dùng nút trên màn hình.'
        self.changed.emit('routing_finished',message)

    @staticmethod
    def _dose(client, dose, action, uid, snooze_minutes=None):
        response = client.confirm_dose_taken(dose['occurrence_id'], action,
            client_event_id='voice_'+uid, expected_context=dose, source='voice',voice_snooze_minutes=snooze_minutes)
        if response.get('status')=='processed':
            if action=='not_taken':
                return 'Đã ghi phản hồi chưa uống; lượt thuốc vẫn chưa được đánh dấu đã uống.'
            return 'Đã ghi nhận tự báo cáo uống thuốc (voice_self_report).' if action=='self_reported_taken' else 'Đã hoãn nhắc theo giới hạn; không đổi đơn thuốc.'
        return 'Chưa ghi nhận: '+response.get('error','chờ đồng bộ.')

    def _work(self, func, captured, refresh=False, urgent=False, quiet=False):
        if self.recording_blocked and not urgent and not quiet:
            self.changed.emit('routing_finished','Đang xử lý; hãy nói lại khi hoàn tất.')
            return
        if not quiet:
            self.changed.emit('routing','Đang xử lý. Chưa có xác nhận tiếp nhận; nếu cần gấp hãy liên hệ thủ công.')
        def run():
            try:
                value = func()
            except Exception as exc:
                value = str(exc) if isinstance(exc,ValueError) else 'Chưa xử lý được; hãy dùng liên hệ thủ công và kiểm tra cấu hình.'
            self.results.put((captured,value,refresh,quiet))
        self.thread = threading.Thread(target=run,daemon=True,name='LocalVoiceActions')
        self.workers = [t for t in self.workers if t.is_alive()]
        self.background_workers.intersection_update(self.workers)
        self.workers.append(self.thread)
        if quiet:
            self.background_workers.add(self.thread)
        self.thread.start()

    def poll(self):
        if self.closed:
            return
        if self.dialogue.pending and not self.dialogue.valid(self.context_provider()):
            self.changed.emit('routing_finished','Yêu cầu hết hạn hoặc ngữ cảnh đã đổi; chưa gửi.')
        try:
            while True:
                captured,value,refresh,*options = self.results.get_nowait()
                quiet = bool(options and options[0])
                current = self.context_provider()
                same_patient = bool(current and captured and current.get('patient_id')==captured.get('patient_id') and current.get('epoch')==captured.get('epoch'))
                if self.context_provider()!=captured and not (refresh and same_patient):
                    # Completed transactions remain audited under original patient; no cross-profile UI.
                    self.changed.emit('routing_finished','Ngữ cảnh đã đổi; kiểm tra yêu cầu ở đúng hồ sơ gốc.')
                    continue
                if refresh:
                    self.dispatcher.window.today_todo_widget.refresh_data()
                if isinstance(value,tuple):
                    ids,statuses,*extra=value
                    if ids:
                        self.last_request=(ids[-1],captured['patient_id'])
                    labels=[]
                    for state in statuses:
                        request_labels={'open':'Đang chờ tiếp nhận','acknowledged':'Đã tiếp nhận','resolved':'Đã giải quyết','cancelled':'Đã hủy/đính chính'}
                        labels.append(request_labels[state['request']['status']])
                        gaps = json.loads(state['request']['context_refs']).get('missing_roles',[])
                        if gaps:
                            labels.append('Chưa có người nhận cho: '+', '.join(gaps))
                        delivery_labels={'queued':'Chờ gửi','sending':'Đang gửi','sent':'Máy chủ thư đã chấp nhận','failed':'Chưa gửi được','unknown':'Chưa rõ kết quả gửi','cancelled':'Đã hủy giao tin'}
                        for delivery in state['deliveries']:
                            labels.append(delivery['recipient_name']+': '+delivery_labels[delivery['status']])
                            if delivery['transport_status']=='UNCONFIGURED':
                                labels.append('Thiếu cấu hình gửi email')
                    if extra and extra[0]:
                        labels.append(extra[0])
                    acknowledged = any(st['request']['status'] in ('acknowledged','resolved') for st in statuses)
                    value='; '.join(labels)+('. Đã có xác nhận tiếp nhận trong hệ thống.' if acknowledged else '. Chưa có xác nhận tiếp nhận.')+' Nếu cần gấp hãy liên hệ thủ công.'
                if self.dialogue.valid(self.context_provider()):
                    self.changed.emit('pending',self.pending_message)
                else:
                    self.changed.emit('delivery_status' if quiet else 'routing_finished',value)
        except queue.Empty:
            pass
        if not self.busy and not self.dialogue.pending and time.monotonic()-self._last_retry>5:
            self._last_retry=time.monotonic()
            snapshot=self.context_provider()
            online=self.dispatcher.window.today_todo_widget.sync_client.is_online
            if snapshot and online:
                patient = snapshot['patient_id']
                def reconnect():
                    self.requests.prune()
                    self.requests.deliver(online=True,retry_failed=True)
                    recent=self.requests.db.fetchone('SELECT request_id FROM voice_requests WHERE patient_id=? ORDER BY created_at DESC LIMIT 1',(patient,))
                    if recent:
                        rid=recent['request_id']
                        return [rid],[self.requests.status(rid)]
                    return 'Bộ luật cục bộ đã sẵn sàng.'
                self._work(reconnect,snapshot,quiet=True)


    def manual_contacts(self):
        captured=self.context_provider()
        if not captured:
            self.changed.emit('routing_finished','Hãy chọn hồ sơ; chưa có liên hệ cấp thiết bị được cấu hình.')
            return
        def read():
            contacts=self.requests.manual_contacts(captured['patient_id'])
            if not contacts:
                return 'Chưa có số liên hệ đã xác minh trong giờ trực. Hãy dùng điện thoại hoặc nhờ người ở gần.'
            return 'Bác dùng điện thoại để liên hệ:\n'+'\n'.join(r['contact_name']+': '+r['phone'] for r in contacts)+'\nHệ thống chưa thực hiện cuộc gọi.'
        self._work(read,captured,urgent=True)

    def retry_delivery(self):
        captured = self.context_provider()
        if not self.last_request or not captured or self.last_request[1]!=captured['patient_id']:
            return
        rid=self.last_request[0]
        online=self.dispatcher.window.today_todo_widget.sync_client.is_online
        def retry():
            self.requests.deliver(online=online,retry_failed=True,force_retry=True,request_id=rid)
            return [rid],[self.requests.status(rid)]
        self._work(retry,captured)

    def cancel(self):
        if self.busy and self.active_context:
            self.cancel_send.set()
        self.dialogue.clear()
        self.repeated.clear()
        self.changed.emit('routing_finished','Đã hủy lựa chọn đang chờ. Yêu cầu đã gửi cần dùng Báo nhầm.')

    def shutdown(self):
        self.closed=True
        self.cancel_send.set()
        self.dialogue.clear()
        self.timer.stop()
