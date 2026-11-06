"""Pending approvals bind to an exact immutable context and expire closed."""
import copy
import time


class Dialogue:
    def __init__(self, seconds=60, clock=time.monotonic):
        self.seconds, self.clock = seconds, clock
        self.pending = None
        self.attempts = 0

    def clear(self):
        self.pending = None
        self.attempts = 0

    def offer(self, decision, snapshot, recipients):
        self.pending = (copy.deepcopy(decision), copy.deepcopy(snapshot), copy.deepcopy(recipients), self.clock() + self.seconds)

    def valid(self, snapshot):
        if self.pending and (snapshot != self.pending[1] or self.clock() >= self.pending[3]):
            self.clear()
        return self.pending is not None

    def approve(self, snapshot):
        if not self.valid(snapshot):
            return None
        pending = self.pending
        self.clear()
        return pending[:3]

    def clarify(self):
        self.attempts += 1
        return 'Bác cần bác sĩ hay người nhà?' if self.attempts <= 2 else 'Hãy chọn: Bác sĩ / Người nhà / Hủy.'
