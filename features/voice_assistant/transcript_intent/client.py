"""Desktop HTTP client. Only app session token, never provider credentials."""
import os
from urllib.parse import urlsplit
import requests
from features.voice_assistant.transcript_intent.context import version, context_type
from features.voice_assistant.transcript_intent.schema import IntentRequest
from features.voice_assistant.transcript_intent.budget import RoutingUnavailable


class GeminiIntentClient:
    def __init__(self, url=None, token=None, enabled=None):
        self.enabled = os.getenv('VOICE_INTENT_ENABLED') == 'true' if enabled is None else enabled
        self.url = url or os.getenv('VOICE_INTENT_BACKEND_URL', '')
        self.token = token or os.getenv('VOICE_INTENT_SESSION_TOKEN', '')
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')):
                raise ValueError('Intent backend requires HTTPS outside loopback')
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Backend URL must not contain credentials/query parameters')

    def classify(self, result, captured, request_id):
        if not self.enabled or not self.url or not self.token:
            raise RoutingUnavailable('cloud_unconfigured')
        envelope = IntentRequest(session_id=result['session_id'], request_id=request_id,
            patient_id=captured['patient_id'], context_version=version(captured),
            context_type=context_type(captured), exercise=captured.get('exercise', ''),
            running=captured.get('running', False), paused=captured.get('paused', False),
            utterance=result['transcript'])
        try:
            with requests.Session() as session:
                session.trust_env = False  # No accidental external proxy for patient envelope.
                response = session.post(self.url.rstrip('/') + '/api/transcript-intent',
                    headers={'Authorization': 'Bearer ' + self.token},
                    json=envelope.model_dump(), timeout=(1, 8), allow_redirects=False)
                if response.status_code != 200:
                    raise RoutingUnavailable('backend_unavailable')
                value = response.json()
                if (value.get('request_id') != request_id or value.get('session_id') != result['session_id'] or
                        value.get('context_version') != envelope.context_version):
                    raise RoutingUnavailable('response_context_mismatch')
                return value
        except RoutingUnavailable:
            raise
        except Exception:
            raise RoutingUnavailable('backend_transport_error') from None
