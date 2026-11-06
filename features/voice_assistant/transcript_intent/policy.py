"""Closed privacy catalog, negation guard, and deterministic semantic validation."""
import re
import unicodedata
from features.voice_assistant.local_voice.intents import resolve_intent
from features.voice_assistant.transcript_intent.registry import Intent, LEGACY, allowed_intents
from features.voice_assistant.transcript_intent.schema import Decision, IntentProposal, proposal


def normalize(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text).casefold().strip()).rstrip('.!…').strip()


# Exact, reviewed neutral navigation utterances. No redaction or synthetic client flag.
PUBLIC_NAVIGATION = frozenset({
    'cho tôi quay về màn hình ban đầu', 'quay lại màn hình đầu',
    'quay về màn hình chính', 'mở trang chính', 'cho tôi về trang chính',
    'trở về trang đầu', 'về màn hình ban đầu', 'đưa tôi về trang chủ',
    'mở màn hình đầu tiên', 'quay lại trang chính', 'cho xem trang chủ',
    'tôi muốn về trang chủ', 'mở trang chủ', 'trở lại trang đầu tiên',
    'về trang đầu', 'cho tôi quay lại trang đầu', 'màn hình chính đi',
    'quay lại trang đầu tiên', 'vui lòng mở trang chính', 'xin quay về trang chủ',
})

# Medical/physical-state paraphrases stay local even in a cloud-enabled deployment.
LOCAL_PARAPHRASES = {
    'tôi uống rồi': Intent.TAKEN, 'tôi đã uống thuốc': Intent.TAKEN,
    'đã uống rồi': Intent.TAKEN, 'nhắc tôi sau': Intent.SNOOZE,
    'nhắc lại một lát nữa': Intent.SNOOZE, 'nhắc sau nhé': Intent.SNOOZE,
    'tôi cần giúp với thuốc này': Intent.HELP, 'tôi không biết dùng thuốc này': Intent.HELP,
    'giúp tôi với lần uống này': Intent.HELP,
    'cho tôi bắt đầu tập': Intent.START, 'bắt đầu tập': Intent.START,
    'tôi muốn bắt đầu bài tập': Intent.START, 'cho tôi nghỉ một chút': Intent.PAUSE,
    'tạm dừng bài tập': Intent.PAUSE, 'dừng bài tập một chút': Intent.PAUSE,
    'cho tôi tạm nghỉ': Intent.PAUSE,
}


def guarded(text):
    normalized = normalize(text)
    if normalized == 'tôi không biết dùng thuốc này':
        return False  # Exact help request, never a medication confirmation.
    return (not normalized or len(text) > 240 or '?' in normalized or
            any(w in normalized.split() for w in ('không', 'chưa', 'đừng', 'chẳng', 'nhưng', 'hoặc', 'và')) or
            any(w in normalized for w in ('bỏ qua', 'đánh dấu', 'khó thở', 'đau ngực', 'tập xong',
                                          'quên', 'không nhớ', 'hình như', 'chắc là', 'system', 'sql')))


def local_proposal(text):
    if guarded(text):
        return proposal()
    legacy = resolve_intent(text)
    intent = LEGACY.get(legacy) or LOCAL_PARAPHRASES.get(normalize(text))
    return proposal(intent, Decision.ROUTE, text.strip()) if intent else None


def privacy_eligible(text):
    # Default deny: free API sees only exact reviewed neutral catalog.
    return not guarded(text) and normalize(text) in PUBLIC_NAVIGATION


def validate_proposal(value, text, context, *, synthetic=False):
    value = value if isinstance(value, IntentProposal) else IntentProposal.model_validate(value)
    if value.decision != Decision.ROUTE or value.intent == Intent.UNKNOWN:
        return proposal(decision=value.decision if value.decision != Decision.ROUTE else Decision.CLARIFY)
    if guarded(text) or value.intent not in allowed_intents(context):
        return proposal()
    if not value.evidence_text or value.evidence_text not in text:
        return proposal()
    local = local_proposal(text)
    if local and local.intent != value.intent:
        return proposal()
    if not synthetic and not local:
        # Independent semantics after JSON: neutral catalog can only route HOME.
        if not privacy_eligible(text) or value.intent != Intent.HOME:
            return proposal()
    return value
