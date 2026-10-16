"""Groq Chat Completions with strict JSON reviews; never executes model tools."""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
import httpx
from pydantic import ValidationError
from bedrock import AIError, AIResult, CAPABILITIES, SYSTEM, env_number, json_schema

ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'


class GroqProvider:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory
        self.last_success = self.last_error = self.last_fingerprint = None

    def settings(self):
        return {'enabled': os.getenv('GROQ_ENABLED', 'true').lower() == 'true',
                'token': os.getenv('GROQ_API_KEY', '').strip(), 'model': os.getenv('GROQ_MODEL') or 'openai/gpt-oss-120b',
                'max_tokens': env_number('GROQ_MAX_TOKENS', 4096, 256, 16384),
                'timeout': env_number('GROQ_TIMEOUT_SECONDS', 60, 5, 120),
                'retries': env_number('GROQ_MAX_RETRIES', 2, 0, 3)}

    def fingerprint(self, c): return hashlib.sha256((c['model'] + c['token']).encode()).hexdigest()

    def status(self):
        try:
            c = self.settings(); configured = bool(c['token']); tested = self.last_fingerprint == self.fingerprint(c)
            return {'provider': 'Groq', 'enabled': c['enabled'], 'model_id': c['model'], 'configured': configured,
                    'connection_status': 'disabled' if not c['enabled'] else 'connected' if tested and not self.last_error else 'error' if tested else 'not_tested' if configured else 'not_configured',
                    'model_accessible': not bool(self.last_error) if tested else None,
                    'last_successful_test': self.last_success, 'error': self.last_error if tested else None,
                    'ai_available': c['enabled'] and configured and not (tested and self.last_error),
                    'credential_mode': 'backend_environment_or_private_dotenv'}
        except AIError as exc:
            return {'provider': 'Groq', 'ai_available': False, 'connection_status': 'error', 'error': exc.message,
                    'model_id': os.getenv('GROQ_MODEL') or 'openai/gpt-oss-120b', 'model_accessible': None}

    def invoke(self, capability, context):
        if capability not in CAPABILITIES and capability != 'connection': raise AIError('CAPABILITY', 'Unknown AI capability')
        c = self.settings()
        if not c['enabled']: raise AIError('DISABLED', 'Groq AI is disabled')
        if not c['token']: raise AIError('NOT_CONFIGURED', 'Set GROQ_API_KEY in the backend environment or private .env file')
        content = json.dumps(context, ensure_ascii=True)
        if len(content.encode()) > 48000: raise AIError('CONTEXT_LIMIT', 'Select fewer fields or findings for AI analysis')
        body = {'model': c['model'], 'messages': [
                    {'role': 'system', 'content': SYSTEM + CAPABILITIES.get(capability, 'Return OK and a short connection acknowledgement with empty arrays.')},
                    {'role': 'user', 'content': 'UNTRUSTED_DATA_JSON\n' + content}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'alignment_review', 'strict': True, 'schema': json_schema()}},
                'temperature': 0, 'max_completion_tokens': c['max_tokens'], 'reasoning_effort': 'low', 'include_reasoning': False}
        started = time.monotonic(); deadline = started + c['timeout']
        client = self.client_factory(c) if self.client_factory else httpx.Client(trust_env=False, follow_redirects=False)
        try:
            for attempt in range(c['retries'] + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise AIError('TIMEOUT', 'Groq request timed out')
                try:
                    # Each attempt shares the remaining connection/read budget; responses are size bounded.
                    with client.stream('POST', ENDPOINT, headers={'Authorization': 'Bearer ' + c['token']}, json=body, timeout=max(.1, remaining / 2)) as response:
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < c['retries']:
                            delay = min(2 ** attempt, 4)
                            if time.monotonic() + delay < deadline:
                                time.sleep(delay); continue
                        safe = {401: 'Groq API key is invalid or expired.', 403: 'Groq access is denied for this key or model.',
                                404: 'Groq model is unavailable; check GROQ_MODEL.', 429: 'Groq rate limit reached; retry later.',
                                400: 'Groq rejected the model, schema or request settings.'}
                        if response.status_code != 200: raise AIError('GROQ_' + str(response.status_code), safe.get(response.status_code, 'Groq request failed; deterministic processing remains available.'))
                        chunks = []; size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 120000: raise AIError('INVALID_OUTPUT', 'Groq response exceeds the size limit')
                            if time.monotonic() > deadline: raise AIError('TIMEOUT', 'Groq request timed out')
                            chunks.append(chunk)
                        try: payload = json.loads(b''.join(chunks))
                        except (ValueError, TypeError): raise AIError('INVALID_OUTPUT', 'Groq returned invalid JSON; no changes were accepted') from None
                    break
                except httpx.TimeoutException: raise AIError('TIMEOUT', 'Groq request timed out') from None
                except httpx.HTTPError: raise AIError('CONNECTION', 'Could not connect to Groq; check backend network access') from None
            try:
                choice = payload['choices'][0]
                if choice['finish_reason'] != 'stop': raise AIError('INCOMPLETE_OUTPUT', 'Groq response was incomplete; no changes were accepted')
                result = AIResult.model_validate_json(choice['message']['content'])
                usage = payload.get('usage', {})
                tokens = {'inputTokens': int(usage.get('prompt_tokens', 0)), 'outputTokens': int(usage.get('completion_tokens', 0))}
            except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
                raise AIError('INVALID_OUTPUT', 'Groq returned an invalid response schema; no changes were accepted') from None
            return {'result': result.model_dump(), 'usage': tokens, 'estimated_cost_usd': None,
                    'latency_ms': round((time.monotonic() - started) * 1000), 'capability': capability,
                    'provider': 'GROQ', 'model_id': c['model']}
        finally: client.close()

    def test_connection(self):
        c = self.settings()
        try:
            self.invoke('connection', {'purpose': 'Synthetic connection test; no customer data.'})
            self.last_success = datetime.now(timezone.utc).isoformat(); self.last_error = None
        except AIError as exc: self.last_error = exc.message
        self.last_fingerprint = self.fingerprint(c)
        return self.status()
