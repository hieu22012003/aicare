"""Temporal smoothing of per-frame labels (BR-01).

Majority vote over a sliding time window with hysteresis: the output only switches when
the new label holds at least ``switch_share`` of the votes and has ``min_votes`` frames.
Ambiguous frames (``None``) do not vote, so they cannot flip the state.
"""

from __future__ import annotations

from collections import Counter, deque
from typing import Generic, Hashable, TypeVar

T = TypeVar("T", bound=Hashable)


class MajorityVoteSmoother(Generic[T]):
    def __init__(self, window_sec: float = 3.0, switch_share: float = 0.7, min_votes: int = 3) -> None:
        self.window_sec = window_sec
        self.switch_share = switch_share
        self.min_votes = min_votes
        self._votes: deque[tuple[float, T]] = deque()
        self.current: T | None = None
        self.share: float = 0.0

    def reset(self) -> None:
        self._votes.clear()
        self.current = None
        self.share = 0.0

    def update(self, ts: float, label: T | None) -> T | None:
        if label is not None:
            self._votes.append((ts, label))
        while self._votes and ts - self._votes[0][0] > self.window_sec:
            self._votes.popleft()
        if not self._votes:
            return self.current

        counts = Counter(label for _, label in self._votes)
        top, top_n = counts.most_common(1)[0]
        total = len(self._votes)
        if top != self.current and top_n >= self.min_votes and top_n / total >= self.switch_share:
            self.current = top
        self.share = counts.get(self.current, 0) / total if self.current is not None else 0.0
        return self.current
