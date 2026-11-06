"""Classification only; caller handles auth/context before and after this boundary."""
import time
import threading
from features.voice_assistant.transcript_intent.budget import ProjectBudget, RoutingUnavailable
from features.voice_assistant.transcript_intent.registry import allowed_intents
from features.voice_assistant.transcript_intent.registry import Intent
from features.voice_assistant.transcript_intent.policy import local_proposal, privacy_eligible, validate_proposal
from features.voice_assistant.transcript_intent.schema import proposal, SCHEMA_VERSION, Decision
from features.voice_assistant.transcript_intent.prompts import PROMPT_VERSION


class GeminiIntentService:
    def __init__(self, config, adapter=None, budget=None):
        self.config = config
        if adapter is None:
            from features.voice_assistant.transcript_intent.gemini_adapter import GeminiIntentAdapter
            adapter = GeminiIntentAdapter(config)
        self.adapter = adapter
        self.budget = budget or ProjectBudget(config)
        self._inflight = threading.Lock()

    def classify(self, text, context):
        started = time.monotonic()
        local = local_proposal(text)
        if local is not None:
            return self._result(validate_proposal(local, text, context), 'local', started)
        if not privacy_eligible(text):
            return self._result(proposal(), 'privacy_blocked', started)
        cfg = self.config
        if not cfg.enabled or not cfg.free_tier_verified or not cfg.project or not cfg.api_key:
            return self._result(proposal(), 'cloud_unconfigured', started)
        allowed = allowed_intents(context) & {Intent.HOME, Intent.UNKNOWN}
        return self._cloud(text, context, allowed, started)

    def _cloud(self, text, context, allowed, started, synthetic=False):
        if not self._inflight.acquire(blocking=False):
            return self._result(proposal(), 'provider_busy', started)
        try:
            return self._cloud_locked(text, context, allowed, started, synthetic)
        finally:
            self._inflight.release()

    def _cloud_locked(self, text, context, allowed, started, synthetic=False):
        deadline = started + self.config.timeout_seconds
        for attempt in range(self.config.retries + 1):
            reserved = False
            error = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= .25:
                    raise RoutingUnavailable('timeout')
                self.budget.reserve()
                reserved = True
                remaining = deadline - time.monotonic()
                if remaining <= .25:
                    raise RoutingUnavailable('timeout')
                kind = {0: 'medication', 1: 'rehabilitation'}.get(context.get('tab'), 'home_navigation') if synthetic else 'home_navigation'
                value = self.adapter.generate(text, kind, allowed, remaining)
                if time.monotonic() >= deadline:
                    raise RoutingUnavailable('timeout')
                validated = validate_proposal(value, text, context, synthetic=synthetic)
                routed = value.get('decision') == 'route' if isinstance(value, dict) else value.decision == Decision.ROUTE
                outcome = 'semantic_rejected' if routed and validated.intent == Intent.UNKNOWN else 'gemini'
                return self._result(validated, outcome, started)
            except RoutingUnavailable as exc:
                error = exc.code
                if error != 'temporary_failure' or attempt == self.config.retries:
                    return self._result(proposal(), error, started)
            except Exception:
                error = 'invalid_response'
                return self._result(proposal(), error, started)
            finally:
                if reserved:
                    try:
                        self.budget.finish(error)
                    except Exception:
                        # Corrupt/unwritable quota state remains closed at next reserve.
                        # Never leak filesystem or SDK exception data into the response.
                        pass
            # SDK retries are off. Only one retry under remaining total deadline/quota.
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
        return self._result(proposal(), 'unavailable', started)

    def classify_synthetic_case(self, case):
        """CLI/mock benchmark only. No HTTP endpoint accepts synthetic assertions."""
        from features.voice_assistant.transcript_intent.synthetic_dataset import load_cases
        approved = {c['id']: c for c in load_cases()}
        if approved.get(case.get('id')) != case:
            raise ValueError('Synthetic case is not from the shipped reviewed dataset')
        if not self.config.enabled or not self.config.free_tier_verified or not self.config.api_key or not self.config.project:
            raise RoutingUnavailable('cloud_unconfigured')
        return self._cloud(case['text'], case['context'], allowed_intents(case['context']), time.monotonic(), synthetic=True)

    @staticmethod
    def _result(value, outcome, started):
        return dict(proposal=value.model_dump(mode='json'), outcome=outcome,
                    model_version='gemini-3.5-flash-lite' if outcome == 'gemini' else None,
                    prompt_version=PROMPT_VERSION,
                    schema_version=SCHEMA_VERSION,
                    latency_ms=round((time.monotonic() - started) * 1000))
