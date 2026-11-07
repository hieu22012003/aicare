"""Mental support when the elder looks sad: a spoken suggestion, then family (or relaxing) music.

The emotion feature publishes a summary every two hours; when the newest one says "Sad" (sadness
for a long stretch) this checker, run every minute by the desktop app:
    1. says one short, varied support suggestion out loud,
    2. starts music (a track the family uploaded first, otherwise a relaxing track),
    3. records "emotion.support_offered" so the family sees it on the web.
At most once per summary and never more than once every ``cooldown_minutes``.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from shared.database.outbox_helper import insert_outbox_event

log = logging.getLogger("SadnessComfort")
UTC = timezone.utc

SUGGESTIONS = (
    "{h} ơi, trông {h} có vẻ hơi buồn. Cháu mở một bài nhạc người nhà gửi cho {h} nghe nhé.",
    "{h} ơi, mình cùng hít thở thật sâu ba lần nhé. Cháu bật chút nhạc nhẹ cho {h} thư giãn.",
    "{h} ơi, {h} có muốn gọi cho con cháu không? Trong lúc chờ, cháu mở nhạc cho {h} nghe nhé.",
    "{h} ơi, {h} uống một cốc nước ấm và đi lại nhẹ nhàng một chút nhé. Cháu mở nhạc cho {h}.",
)


class SadnessComfort:
    def __init__(self, db, speak: Callable[[str], None], play_music: Callable[[], bool] | None = None,
                 honorific: str = "bác", cooldown_minutes: float = 90, max_age_minutes: float = 30,
                 clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.speak = speak
        self.play_music = play_music
        self.honorific = honorific
        self.cooldown = cooldown_minutes * 60
        self.max_age = max_age_minutes * 60
        self.clock = clock
        self._handled: set[str] = set()
        self._last_offer = -1e18
        self._turn = 0

    def check(self, patient_id: str | None) -> str | None:
        """Returns the sentence spoken, or None."""
        if not patient_id:
            return None
        now = self.clock()
        row = self.db.fetchone("SELECT summary_id, label, published_at FROM emotion_summaries_v2 WHERE patient_id=? "
                               "ORDER BY published_at DESC LIMIT 1", (patient_id,))
        if not row or row["label"] != "Sad" or row["summary_id"] in self._handled:
            return None
        self._handled.add(row["summary_id"])
        try:
            age = now - datetime.fromisoformat(row["published_at"]).timestamp()
        except (TypeError, ValueError):
            age = 0
        if age > self.max_age or now - self._last_offer < self.cooldown:
            return None
        self._last_offer = now
        text = SUGGESTIONS[self._turn % len(SUGGESTIONS)].format(h=self.honorific)
        self._turn += 1
        try:
            self.speak(text)
        except Exception:
            log.exception("TTS failed")
        played = False
        if self.play_music is not None:
            try:
                played = bool(self.play_music())
            except Exception:
                log.exception("Could not start music")
        try:
            with self.db.connect() as conn:
                insert_outbox_event(conn, patient_id, "emotion.support_offered", "emotion_summary", row["summary_id"],
                                    source="comfort", payload_minimal={"text": text, "music": played})
        except Exception:
            log.debug("outbox write failed", exc_info=True)
        return text
