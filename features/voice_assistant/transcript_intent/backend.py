"""Authenticated internal endpoint, derives patient rights and rechecks DB version."""
import logging
from pydantic import ValidationError
from features.voice_assistant.transcript_intent.schema import IntentRequest, proposal
from features.voice_assistant.transcript_intent.context import read_state, version
from features.voice_assistant.transcript_intent.config import BackendConfig
from features.voice_assistant.transcript_intent.service import GeminiIntentService
from features.voice_assistant.transcript_intent.policy import validate_proposal

logger = logging.getLogger('transcript_intent.audit')


class TranscriptIntentEndpoint:
    def __init__(self, db, auth, service=None):
        self.db, self.auth = db, auth
        self.service = service  # Lazy initialization keeps disabled backend dependency optional.

    def handle(self, body, user_id, token):
        try:
            req = IntentRequest.model_validate(body)
        except ValidationError:
            return 400, {'error': 'invalid_intent_request'}
        access, _ = self.auth.check_patient_access(user_id, req.patient_id, 'view')
        if not access:
            return 403, {'error': 'patient_access_denied'}
        state = read_state(self.db, req.patient_id)
        if state is None or version(state) != req.context_version:
            return 409, {'error': 'context_changed'}
        state.update(tab={'medication': 0, 'rehabilitation': 1, 'home_navigation': 2}[req.context_type],
                     exercise=req.exercise, running=req.running, paused=req.paused, source='camera')
        # Cloud production sees navigation only; physical-state declarations cannot grant sensitive intents.
        if self.service is None:
            try:
                self.service = GeminiIntentService(BackendConfig.from_env())
            except Exception:
                return 503, {'error': 'intent_backend_unavailable'}
        try:
            result = self.service.classify(req.utterance, state)
        except Exception:
            return 503, {'error': 'intent_backend_unavailable'}
        # Token revocation, care-team changes and backend version changes while Gemini was pending.
        session = self.auth.get_session(token)
        access, _ = self.auth.check_patient_access(user_id, req.patient_id, 'view')
        current = read_state(self.db, req.patient_id)
        if not session or session['user_id'] != user_id or not access:
            return 403, {'error': 'access_changed'}
        if current is None or version(current) != req.context_version:
            return 409, {'error': 'context_changed'}
        # Classification endpoint never executes any handler or sends notifications.
        result['proposal'] = validate_proposal(result['proposal'], req.utterance, state).model_dump(mode='json')
        result.update(request_id=req.request_id, session_id=req.session_id, context_version=req.context_version)
        logger.info('request=%s intent=%s outcome=%s prompt=%s model=%s latency_ms=%s',
                    req.request_id, result['proposal']['intent'], result.get('outcome'),
                    result.get('prompt_version'), result.get('model_version'), result.get('latency_ms'))
        return 200, result
