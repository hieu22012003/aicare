"""One background routing request. Cancellation invalidates the late result, never overlaps."""
import queue
import threading
import time
import uuid
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from features.voice_assistant.transcript_intent.policy import local_proposal, privacy_eligible, validate_proposal
from features.voice_assistant.transcript_intent.schema import proposal
from features.voice_assistant.transcript_intent.client import GeminiIntentClient


class VoiceRequestCoordinator(QObject):
    changed = pyqtSignal(str, str)
    resolved = pyqtSignal(object, object, object)

    def __init__(self, context_provider, parent=None, client=None):
        super().__init__(parent)
        self.context_provider = context_provider
        self.client = client or GeminiIntentClient()
        self.active = None
        self.thread = None
        self.closed = False
        self.queue = queue.Queue(maxsize=1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(50)

    @property
    def busy(self):
        return self.active is not None or (self.thread is not None and self.thread.is_alive())

    def route(self, result, captured):
        if self.closed or self.busy or self.context_provider() != captured:
            return
        text = result.get('transcript', '')
        local = local_proposal(text)
        if local is not None:
            self.resolved.emit(result, captured, validate_proposal(local, text, captured))
            return
        if not privacy_eligible(text) or not self.client.enabled:
            self.resolved.emit(result, captured, proposal())
            return
        request_id = uuid.uuid4().hex
        self.active = (request_id, result, captured, time.monotonic())
        self.changed.emit('routing', 'Đang hiểu yêu cầu... Có thể nhấn Hủy.')
        def request():
            try:
                value = self.client.classify(result, captured, request_id)
            except Exception:
                value = None
            try:
                self.queue.put_nowait((request_id, value))
            except queue.Full:
                pass
        self.thread = threading.Thread(target=request, daemon=True)
        self.thread.start()

    def cancel(self):
        if self.active:
            self.active = None
            self.changed.emit('routing_cancelled', 'Đã hủy yêu cầu; đang chờ kết nối kết thúc.')

    def poll(self):
        if self.closed:
            return
        try:
            if self.active and (self.context_provider() != self.active[2] or time.monotonic() - self.active[3] > 8):
                self.cancel()
            while True:
                request_id, value = self.queue.get_nowait()
                if self.active and self.active[0] == request_id:
                    _, result, captured, _ = self.active
                    self.active = None
                    if self.context_provider() == captured:
                        try:
                            resolved = validate_proposal(value['proposal'], result['transcript'], captured) if value else proposal()
                        except Exception:
                            resolved = proposal()
                        self.resolved.emit(result, captured, resolved)
                if not self.active:
                    self.changed.emit('routing_finished', 'Bác có thể nói lại hoặc chọn nút trên màn hình.')
        except queue.Empty:
            pass
        except Exception:
            self.cancel()

    def shutdown(self):
        self.closed = True
        self.active = None
        self.timer.stop()
        # HTTP worker has a bounded timeout; it owns no UI/model/micro and no business action.
        if self.thread:
            self.thread.join(timeout=.1)
