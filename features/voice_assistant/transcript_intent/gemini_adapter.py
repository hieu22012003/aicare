"""Official SDK only, generateContent text/JSON, no tools or cloud-side business actions."""
import asyncio
import time
from features.voice_assistant.transcript_intent.budget import RoutingUnavailable
from features.voice_assistant.transcript_intent.schema import IntentProposal
from features.voice_assistant.transcript_intent.prompts import SYSTEM_PROMPT, payload


class GeminiIntentAdapter:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client

    def generate(self, text, context_type, allowed, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds
        from google import genai
        from google.genai import types, errors
        own_client = self.client is None
        client = self.client or genai.Client(api_key=self.config.api_key, vertexai=False,
            http_options=types.HttpOptions(timeout=max(10000, int(timeout_seconds * 1000)),
                                          retry_options=types.HttpRetryOptions(attempts=1)))
        try:
            async def request():
                async with client.aio as aio:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RoutingUnavailable('timeout')
                    return await asyncio.wait_for(aio.models.generate_content(
                        model=self.config.model, contents=payload(text, context_type, allowed),
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT, response_mime_type='application/json',
                            response_json_schema=IntentProposal.model_json_schema(),
                            temperature=0, max_output_tokens=512,
                            tools=[], automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))),
                        timeout=remaining)
            # Provider rejects deadlines <10s; client independently cancels within total <=8s.
            response = asyncio.run(request())
            if not response.candidates or str(response.candidates[0].finish_reason.value) != 'STOP':
                raise RoutingUnavailable('blocked_or_truncated')
            if not response.text:
                raise RoutingUnavailable('empty_response')
            return IntentProposal.model_validate_json(response.text)
        except (asyncio.TimeoutError, TimeoutError):
            raise RoutingUnavailable('timeout') from None
        except errors.APIError as exc:
            code = int(exc.code or 0)
            detail = str(exc).lower()
            if code == 400 and any(mark in detail for mark in ('api key not valid', 'api_key_invalid', 'api_key_expired')):
                raise RoutingUnavailable('invalid_key') from None
            if code == 400 and any(mark in detail for mark in ('json schema', 'response_schema', 'response_json_schema', 'additionalproperties')):
                raise RoutingUnavailable('schema_not_supported') from None
            raise RoutingUnavailable({429: 'quota_exhausted', 401: 'invalid_key', 403: 'forbidden',
                                      404: 'model_unavailable'}.get(code, 'temporary_failure' if code >= 500 else 'provider_error')) from None
        except RoutingUnavailable:
            raise
        except Exception:
            # Never put SDK exception text, input, or secret into UI/audit/log.
            raise RoutingUnavailable('invalid_response_or_transport') from None
        finally:
            if own_client:
                try:
                    client.close()
                except Exception:
                    pass
