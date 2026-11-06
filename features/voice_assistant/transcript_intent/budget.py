"""Project-level quota and circuit state without clinical data, shared across processes."""
import json
import os
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from filelock import FileLock


class RoutingUnavailable(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ProjectBudget:
    def __init__(self, config, clock=time.time):
        self.config, self.clock = config, clock
        self.path = Path(config.quota_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.path) + '.lock', timeout=1)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text())
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except Exception:
            raise RoutingUnavailable('quota_state_error') from None

    def _write(self, state):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(state))
        os.replace(temp, self.path)

    def reserve(self, tokens=1200):
        now = self.clock()
        # Provider daily reset is Pacific time, not device timezone.
        day = datetime.fromtimestamp(now, ZoneInfo('America/Los_Angeles')).date().isoformat()
        with self.lock:
            state = self._read()
            if state.get('project') not in (None, self.config.project):
                raise RoutingUnavailable('quota_project_mismatch')
            if state.get('day') != day:
                state = dict(project=self.config.project, day=day, daily=0, minute=[], failures=0, open_until=0)
            if state.get('open_until', 0) > now:
                raise RoutingUnavailable('circuit_open')
            if state.get('busy_until', 0) > now:
                raise RoutingUnavailable('provider_busy')
            recent = [x for x in state.get('minute', []) if x['at'] > now - 60]
            if len(recent) >= self.config.rpm:
                raise RoutingUnavailable('local_rpm_limit')
            if sum(x['tokens_reserved'] for x in recent) + tokens > self.config.tpm:
                raise RoutingUnavailable('local_tpm_limit')
            if state.get('daily', 0) >= self.config.rpd - self.config.daily_reserve:
                raise RoutingUnavailable('local_daily_limit')
            state['daily'] = state.get('daily', 0) + 1
            state['minute'] = recent + [{'at': now, 'tokens_reserved': tokens}]
            state['busy_until'] = now + self.config.timeout_seconds + 1
            self._write(state)

    def finish(self, error_code=None):
        with self.lock:
            state = self._read()
            state['busy_until'] = 0
            state['failures'] = state.get('failures', 0) + 1 if error_code else 0
            if error_code == 'quota_exhausted' or state['failures'] >= 3:
                state['open_until'] = self.clock() + (60 if error_code == 'quota_exhausted' else 30)
            elif not error_code:
                state['open_until'] = 0
            self._write(state)
