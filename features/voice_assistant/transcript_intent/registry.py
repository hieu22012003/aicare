"""Only intents backed by current desktop button handlers."""
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    HOME = 'navigate_home'
    SNOOZE = 'snooze_medication'
    TAKEN = 'confirm_medication_taken'
    HELP = 'request_medication_help'
    START = 'start_rehabilitation'
    PAUSE = 'pause_rehabilitation'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class IntentEntry:
    legacy: str
    context: str
    handler: str
    confirmation: bool = False


REGISTRY = {
    Intent.HOME: IntentEntry('home', 'home_navigation', 'medication_tabs.setCurrentIndex(0)'),
    Intent.SNOOZE: IntentEntry('snooze', 'medication', 'ElderSyncClient.confirm_dose_taken(snooze)'),
    Intent.TAKEN: IntentEntry('taken', 'medication', 'ElderSyncClient.confirm_dose_taken(self_reported_taken)', True),
    Intent.HELP: IntentEntry('help', 'medication', 'ElderSyncClient.confirm_dose_taken(help_requested)'),
    Intent.START: IntentEntry('start', 'rehabilitation', 'DashboardWindow._on_start_exercise'),
    Intent.PAUSE: IntentEntry('pause', 'rehabilitation', 'DashboardWindow._on_pause_exercise'),
}
LEGACY = {entry.legacy: intent for intent, entry in REGISTRY.items()}


def allowed_intents(context):
    """Code-derived context policy. Never accept a model's permissions."""
    allowed = {Intent.UNKNOWN}
    if not context or not context.get('patient_id'):
        return allowed
    if not context.get('running'):
        allowed.add(Intent.HOME)
    if context.get('tab') == 0 and len(context.get('doses', [])) == 1:
        allowed.update((Intent.TAKEN, Intent.HELP))
        if context['doses'][0]['state'] != 'OVERDUE':
            allowed.add(Intent.SNOOZE)
    if context.get('tab') == 1 and context.get('source') == 'camera':
        selected = [p for p in context.get('plans', []) if p.get('exercise_id') == context.get('exercise') and p.get('is_active')]
        if selected:
            if not context.get('running'):
                allowed.add(Intent.START)
            elif not context.get('paused'):
                allowed.add(Intent.PAUSE)
    return allowed
