"""Exact Vietnamese allowlist: unknown/negated/multiple commands do nothing."""
import re
import unicodedata

COMMANDS = {
    'về trang chính': 'home',
    'tôi đã uống rồi': 'taken', 'tôi đã uống thuốc rồi': 'taken',
    'nhắc lại sau': 'snooze', 'tôi cần hỗ trợ': 'help',
    'bắt đầu bài tập': 'start', 'tạm dừng': 'pause',
}


def resolve_intent(transcript):
    text = unicodedata.normalize('NFC', transcript).lower().strip()
    if '?' in text:
        return None
    text = re.sub(r'[.!…]+$', '', text).strip()
    text = re.sub(r'\s+', ' ', text)
    if any(word in text.split() for word in ('không', 'chưa', 'đừng', 'chẳng')):
        return None
    return COMMANDS.get(text)
