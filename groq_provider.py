"""Groq structured reviews with local token accounting and durable admission."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from datetime import datetime, timezone
from functools import lru_cache
import httpx
from pydantic import BaseModel, ValidationError
from bedrock import AIError, AIResult, CAPABILITIES, SYSTEM, env_number
from groq_budget import GroqCoordinator, retry_error

ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'
TOKEN_CEILING = 8048
TOKENIZER_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
TOKENIZER_HASH = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'


@lru_cache(maxsize=4)
def _cached_tokenizer(cache_dir):
    path = Path(cache_dir) / hashlib.sha1(TOKENIZER_URL.encode()).hexdigest()
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != TOKENIZER_HASH: raise ValueError()
        import tiktoken
        return tiktoken.get_encoding('o200k_harmony')
    except (OSError, ValueError, ImportError):
        raise AIError('TOKENIZER_CONFIGURATION', 'Install tiktoken and prewarm the o200k_harmony vocabulary during setup; AI requests never download tokenizer files') from None


def local_tokenizer():
    cache_dir = os.getenv('TIKTOKEN_CACHE_DIR', os.getenv('DATA_GYM_CACHE_DIR', str(Path(tempfile.gettempdir()) / 'data-gym-cache')))
    if not cache_dir: raise AIError('TOKENIZER_CONFIGURATION', 'Set a persistent TIKTOKEN_CACHE_DIR and prewarm o200k_harmony during setup')
    return _cached_tokenizer(cache_dir)


def structured_schema(response_model):
    if not isinstance(response_model, type) or not issubclass(response_model, BaseModel):
        raise AIError('CONFIGURATION', 'A typed response contract is required for AI analysis')
    def clean(value):
        if isinstance(value, dict):
            result = {key: clean(item) for key, item in value.items() if key not in ('minLength', 'maxLength', 'minimum', 'maximum', 'minItems', 'maxItems', 'title', 'default')}
            if result.get('type') == 'object' and 'properties' in result:
                result['additionalProperties'] = False
                result['required'] = list(result['properties'])
            return result
        if isinstance(value, list): return [clean(item) for item in value]
        return value
    return clean(response_model.model_json_schema())


def _output_schema_failure(response):
    data = bytearray()
    for chunk in response.iter_bytes(chunk_size=4096):
        if len(data) + len(chunk) > 16_384: return False
        data.extend(chunk)
    try: payload = json.loads(data)
    except (ValueError, RecursionError): return False
    return (isinstance(payload, dict) and isinstance(payload.get('error'), dict)
            and payload['error'].get('code') == 'json_validate_failed')


class GroqProvider:
    def __init__(self, client_factory=None, coordinator=None, tokenizer=None):
        self.client_factory = client_factory
        self.coordinator = coordinator if coordinator is not None else GroqCoordinator()
        self.tokenizer = tokenizer
        self.last_success = self.last_error = self.last_fingerprint = None

    def settings(self):
        model = os.getenv('GROQ_MODEL') or 'openai/gpt-oss-120b'
        if model != 'openai/gpt-oss-120b': raise AIError('CONFIGURATION', 'Local token accounting requires GROQ_MODEL=openai/gpt-oss-120b')
        return {'enabled': os.getenv('GROQ_ENABLED', 'true').lower() == 'true',
                'token': os.getenv('GROQ_API_KEY', '').strip(), 'model': model,
                'max_tokens': env_number('GROQ_MAX_TOKENS', 2000, 256, TOKEN_CEILING - 256),
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
                    'credential_mode': 'backend_environment_or_private_dotenv', 'token_ceiling': TOKEN_CEILING,
                    'reserved_completion_tokens': c['max_tokens'], 'tokenizer': 'o200k_harmony',
                    'quotas': {key: value for key, value in self.coordinator.limits().items() if key != 'cache_ttl'}}
        except AIError as exc:
            return {'provider': 'Groq', 'ai_available': False, 'connection_status': 'error', 'error': exc.message,
                    'model_id': os.getenv('GROQ_MODEL') or 'openai/gpt-oss-120b', 'model_accessible': None}

    def _body(self, capability, context, response_model, instructions, completion_tokens=None):
        c = self.settings()
        completion = c['max_tokens'] if completion_tokens is None else completion_tokens
        if type(completion) is not int or not 256 <= completion < TOKEN_CEILING:
            raise AIError('CONFIGURATION', 'The completion token reservation must be between 256 and 8047')
        return {'model': c['model'], 'messages': [
                    {'role': 'system', 'content': SYSTEM + instructions},
                    {'role': 'user', 'content': 'UNTRUSTED_DATA_JSON\n' + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}],
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'alignment_review', 'strict': True, 'schema': structured_schema(response_model)}},
                'temperature': 0, 'max_completion_tokens': completion, 'reasoning_effort': 'low', 'include_reasoning': False}

    def _budget(self, body):
        tokenizer = self.tokenizer or local_tokenizer()
        serialized = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        # Count every request component, then reserve framing/schema adaptation headroom.
        counted = len(tokenizer.encode_ordinary(serialized))
        inputs = math.ceil(counted * 1.25) + 512
        completion = body['max_completion_tokens']  # Includes both hidden reasoning and the final answer.
        return {'input_tokens': inputs, 'reserved_completion_tokens': completion, 'total_tokens': inputs + completion,
                'max_input_tokens': TOKEN_CEILING - completion, 'target_input_tokens': min(5000, TOKEN_CEILING - completion),
                'ceiling': TOKEN_CEILING, 'tokenizer': 'o200k_harmony', 'fits': inputs + completion <= TOKEN_CEILING}

    def budget(self, capability, context, response_model, instructions, completion_tokens=None):
        return self._budget(self._body(capability, context, response_model, instructions, completion_tokens))

    def batches(self, capability, items, response_model, instructions, context=None, key='items', max_items=None):
        if max_items is not None and (type(max_items) is not int or not 1 <= max_items <= 1000):
            raise AIError('CONFIGURATION', 'Batch item limits must be between 1 and 1,000')
        base = dict(context or {}); batches = []; current = []
        for item in items:
            candidate = {**base, key: current + [item]}
            estimate = self.budget(capability, candidate, response_model, instructions)
            if current and (not estimate['fits'] or estimate['input_tokens'] > estimate['target_input_tokens'] or max_items is not None and len(current) >= max_items):
                batches.append({**base, key: current}); current = []
                estimate = self.budget(capability, {**base, key: [item]}, response_model, instructions)
            if not estimate['fits']: raise AIError('CONTEXT_LIMIT', 'One analysis item exceeds the token budget; split the source into smaller sections')
            current.append(item)
        if current or not batches:
            candidate = {**base, key: current}
            if not self.budget(capability, candidate, response_model, instructions)['fits']:
                raise AIError('CONTEXT_LIMIT', 'The analysis instructions and response schema exceed the token budget')
            batches.append(candidate)
        return batches

    def invoke(self, capability, context):
        if capability not in CAPABILITIES and capability != 'connection': raise AIError('CAPABILITY', 'Unknown AI capability')
        instructions = CAPABILITIES.get(capability, 'Return OK and a short connection acknowledgement with empty arrays.')
        return self._invoke(capability, context, AIResult, instructions, None)

    def invoke_structured(self, capability, context, response_model, instructions, scope=None):
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 1000:
            raise AIError('CONFIGURATION', 'AI cache scope must identify the authorized owner, project and source versions')
        if not isinstance(capability, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', capability):
            raise AIError('CAPABILITY', 'Invalid structured analysis capability')
        return self._invoke(capability, context, response_model, instructions, scope)

    def _invoke(self, capability, context, response_model, instructions, scope):
        c = self.settings()
        if not c['enabled']: raise AIError('DISABLED', 'Groq AI is disabled')
        if not c['token']: raise AIError('NOT_CONFIGURED', 'Set GROQ_API_KEY in the backend environment or private .env file')
        body = self._body(capability, context, response_model, instructions)
        budget = self._budget(body)
        if not budget['fits']: raise AIError('CONTEXT_LIMIT', 'Analysis input and reserved completion exceed 8,048 tokens; split the analysis into batches')
        credential = hashlib.sha256(c['token'].encode()).hexdigest()
        cache_key = hashlib.sha256(json.dumps({'credential': credential, 'scope': scope, 'capability': capability, 'body': body}, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() if scope is not None else None
        admission = self.coordinator.reserve(credential, cache_key, budget['total_tokens'], c['timeout'] + 15)
        if admission['cached'] is not None:
            run = admission['cached']
            try: response_model.model_validate(run['result'])
            except (KeyError, ValidationError): raise AIError('INVALID_OUTPUT', 'Cached AI response does not match the current response contract') from None
            return {**run, 'cached': True, 'latency_ms': 0}
        reservation = admission['reservation']; started = time.monotonic(); deadline = started + c['timeout']
        client = None; headers = {}; status = None; actual_tokens = None
        try:
            client = self.client_factory(c) if self.client_factory else httpx.Client(trust_env=False, follow_redirects=False)
            try:
                with client.stream('POST', ENDPOINT, headers={'Authorization': 'Bearer ' + c['token']}, json=body, timeout=max(.1, c['timeout'] / 2)) as response:
                    status = response.status_code; headers = dict(response.headers)
                    safe = {401: 'Groq API key is invalid or expired.', 403: 'Groq access is denied for this key or model.',
                            404: 'Groq model is unavailable; check GROQ_MODEL.', 429: 'Groq rate limit reached; analysis remains queued.',
                            400: 'Groq rejected the model, schema or request settings.'}
                    if status != 200:
                        output_schema_failure = status == 400 and _output_schema_failure(response)
                        delay = self.coordinator.finish(reservation, headers=headers, status=status); reservation = None
                        message = safe.get(status, 'Groq request failed; analysis remains pending.')
                        if output_schema_failure:
                            raise retry_error('GROQ_OUTPUT_SCHEMA', 'Groq returned an invalid structured review; analysis will retry automatically.', 1)
                        if status in (429, 500, 502, 503, 504): raise retry_error('GROQ_' + str(status), message, delay)
                        raise AIError('GROQ_' + str(status), message)
                    chunks = []; size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 120000: raise AIError('INVALID_OUTPUT', 'Groq response exceeds the size limit')
                        if time.monotonic() > deadline: raise retry_error('TIMEOUT', 'Groq request timed out; analysis remains queued', 1)
                        chunks.append(chunk)
                    try: payload = json.loads(b''.join(chunks))
                    except (ValueError, TypeError): raise AIError('INVALID_OUTPUT', 'Groq returned invalid JSON; no changes were accepted') from None
            except httpx.TimeoutException: raise retry_error('TIMEOUT', 'Groq request timed out; analysis remains queued', 1) from None
            except httpx.HTTPError: raise retry_error('CONNECTION', 'Could not connect to Groq; analysis remains queued', 1) from None
            try:
                choice = payload['choices'][0]
                if choice['finish_reason'] != 'stop': raise AIError('INCOMPLETE_OUTPUT', 'Groq response was incomplete; no changes were accepted')
                result = response_model.model_validate_json(choice['message']['content'])
                usage = payload.get('usage', {})
                inputs, outputs = usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0)
                if type(inputs) is not int or type(outputs) is not int or inputs < 0 or outputs < 0: raise ValueError()
                tokens = {'inputTokens': inputs, 'outputTokens': outputs}
                if 'prompt_tokens' in usage and 'completion_tokens' in usage: actual_tokens = inputs + outputs
            except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
                raise AIError('INVALID_OUTPUT', 'Groq returned an invalid response schema; no changes were accepted') from None
            if actual_tokens is not None and (inputs > budget['input_tokens'] or outputs > budget['reserved_completion_tokens'] or actual_tokens > TOKEN_CEILING):
                raise AIError('TOKEN_ACCOUNTING', 'Reported usage exceeded the conservative request reservation; review token accounting before retrying')
            run = {'result': result.model_dump(mode='json'), 'usage': tokens, 'estimated_cost_usd': None,
                   'latency_ms': round((time.monotonic() - started) * 1000), 'capability': capability,
                   'provider': 'GROQ', 'model_id': c['model'], 'cached': False,
                   'token_budget': {**budget, 'actual_total_tokens': actual_tokens}}
            self.coordinator.finish(reservation, run=run, actual_tokens=actual_tokens, headers=headers, status=200); reservation = None
            return run
        finally:
            if client is not None: client.close()
            if reservation is not None: self.coordinator.finish(reservation, actual_tokens=actual_tokens, headers=headers, status=status or 0)

    def test_connection(self):
        c = self.settings()
        try:
            self.invoke('connection', {'purpose': 'Synthetic connection test; no customer data.'})
            self.last_success = datetime.now(timezone.utc).isoformat(); self.last_error = None
        except AIError as exc: self.last_error = exc.message
        self.last_fingerprint = self.fingerprint(c)
        return self.status()
