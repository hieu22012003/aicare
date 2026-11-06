"""UI-thread session controller; context changes invalidate every late event."""
import multiprocessing as mp
import queue
import uuid
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from features.voice_assistant.local_voice.worker import run_worker


class VoiceSessionController(QObject):
    changed = pyqtSignal(str, str)
    transcript_ready = pyqtSignal(object, object)
    transcript_captured = pyqtSignal(object, object)

    def __init__(self, config, context_provider, parent=None, worker_target=run_worker):
        super().__init__(parent)
        self.context_provider = context_provider
        self.session = None
        self.context = None
        self.state = 'loading'
        self.closed = False
        self.review_message = ''
        self.start_when_ready = False
        self.config, self.worker_target = config, worker_target
        ctx = mp.get_context('spawn')
        self.commands = ctx.Queue(maxsize=2)
        self.events = ctx.Queue(maxsize=8)
        self.process = ctx.Process(target=worker_target, args=(config, self.commands, self.events), daemon=True)
        self.process.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)

    def retry(self):
        if self.state != 'unavailable':
            return
        self.shutdown()
        ctx = mp.get_context('spawn')
        self.commands, self.events = ctx.Queue(maxsize=2), ctx.Queue(maxsize=8)
        self.process = ctx.Process(target=self.worker_target,
                                   args=(self.config, self.commands, self.events), daemon=True)
        self.closed = False
        self.change('loading', 'Đang thử chuẩn bị model local...')
        self.process.start()
        self.timer.start(50)

    def change(self, state, message):
        self.state = state
        self.changed.emit(state, message)

    def send(self, command, token):
        try:
            self.commands.put_nowait((command, token))
        except queue.Full:
            self.change('error', 'Worker đang bận; hãy dùng nút bấm.')
            return False
        return True

    def start(self):
        if self.state == 'loading' and not self.closed:
            self.start_when_ready = True
            return
        if self.state != 'idle' or self.closed:
            return
        try:
            context = self.context_provider()
        except Exception:
            self.change('idle', 'Không đọc được ngữ cảnh; hãy dùng nút bấm.')
            return
        if not context or not context.get('patient_id'):
            self.change('idle', 'Hãy chọn hồ sơ người bệnh trước khi nói.')
            return
        from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator
        get_tts_coordinator().suspend_for_recording(True)
        self.review_message = ''
        self.session, self.context = uuid.uuid4().hex, context
        self.change('starting', 'Đang mở micro...')
        if not self.send('record', self.session):
            self.cancel()

    def stop(self):
        if self.state == 'recording':
            self.change('stopping', 'Đang dừng ghi âm...')
            self.send('stop', self.session)

    def release_tts(self):
        from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator
        get_tts_coordinator().suspend_for_recording(False)

    def cancel(self):
        self.start_when_ready = False
        if self.session is None:
            return
        token, self.session = self.session, None
        self.context = None
        self.send('cancel', token)
        # Keep busy until worker acknowledges cancellation, including native inference.
        self.change('cancelling', 'Đã hủy; đang chờ worker giải phóng micro.')

    def poll(self):
        if self.closed:
            return
        try:
            if self.session:
                current = self.context_provider()
                identity_changed = not current or current.get('patient_id') != self.context.get('patient_id')
                # Refreshing doses/plans must not close a live microphone.
                # Full context is still checked before any transcript is routed.
                if identity_changed:
                    self.cancel()
            while True:
                kind, token, payload = self.events.get_nowait()
                if kind == 'ready':
                    self.change('idle', f'Nhấn micro, chờ “Đang nghe” rồi nói. Tối đa {self.config.max_seconds:g} giây.')
                    if self.start_when_ready:
                        self.start_when_ready = False
                        self.start()
                elif kind == 'unavailable':
                    self.release_tts()
                    self.change('unavailable', payload)
                elif kind == 'idle' and self.state == 'cancelling':
                    self.release_tts()
                    self.change('idle', 'Đã hủy. Có thể dùng nút bấm.')
                elif token == self.session and self.session:
                    if kind == 'recording':
                        self.change('recording', 'Đang nghe. Nhấn Dừng khi nói xong.')
                    elif kind == 'transcribing':
                        self.release_tts()
                        self.change('transcribing', 'Đang chuyển giọng nói thành chữ...')
                    elif kind == 'result':
                        context = self.context
                        self.session = None
                        self.release_tts()
                        self.transcript_captured.emit(payload, context)
                        self.change('review', payload['transcript'] or 'Chưa nghe rõ; hãy dùng nút bấm.')
                        context_valid = self.context_provider() == context
                        if context_valid and payload.get('status') == 'completed':
                            payload['is_final'] = True
                            self.transcript_ready.emit(payload, context)
                        if not context_valid:
                            self.review_message = 'Đã nhận văn bản; ngữ cảnh đã đổi nên chưa thực hiện lệnh. Hãy nói lại để gửi yêu cầu.'
                        self.state = 'idle'
                        self.changed.emit('idle', self.review_message or payload['transcript'] or 'Chưa nghe rõ; hãy dùng nút bấm.')
                    elif kind == 'error':
                        self.session = None
                        self.release_tts()
                        self.change('idle', 'Lỗi micro/STT: ' + payload)
        except queue.Empty:
            pass
        except Exception as exc:
            self.cancel()
            self.change('error', str(exc))
        if not self.process.is_alive() and self.state != 'unavailable':
            self.release_tts()
            self.change('unavailable', 'Worker STT đã dừng; hãy dùng nút bấm.')

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.session = self.context = None
        self.release_tts()
        self.send('shutdown', None)
        self.process.join(timeout=.5)
        if self.process.is_alive():
            self.process.terminate()  # Isolated process, never QThread.terminate.
            self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)
        for channel in (self.commands, self.events):
            channel.cancel_join_thread()
            channel.close()
