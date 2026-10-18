import json
import httpx
import pytest
import ai
from bedrock import AIError, AIResult, AmazonBedrockProvider
from groq_provider import ENDPOINT, GroqProvider
from groq_budget import GroqCoordinator

PAYLOAD = {'status': 'OK', 'summary': 'Synthetic review', 'proposals': [], 'mappings': [], 'observations': []}
TOKEN = 'synthetic-groq-test-token'


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for key in ('GROQ_ENABLED', 'GROQ_MAX_TOKENS', 'GROQ_TIMEOUT_SECONDS', 'GROQ_MAX_RETRIES', 'GROQ_BASE_URL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('GROQ_API_KEY', TOKEN)
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-120b')


def response(payload=PAYLOAD, finish='stop', **extra):
    return {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(payload)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20}, **extra}


def provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    return GroqProvider(lambda settings: client, coordinator=GroqCoordinator(':memory:')), client


def test_strict_schema_requested_model_fixed_endpoint_and_private_header(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response())
    monkeypatch.setenv('GROQ_BASE_URL', 'https://untrusted.example')
    instance, client = provider(handler)
    result = instance.invoke('schema', {'source': 'untrusted uploaded content'})
    request = requests[0]; body = json.loads(request.content)
    assert str(request.url) == ENDPOINT == 'https://api.groq.com/openai/v1/chat/completions'
    assert request.headers['authorization'] == 'Bearer ' + TOKEN
    assert TOKEN not in request.content.decode() and TOKEN not in json.dumps(result)
    assert body['model'] == 'openai/gpt-oss-120b'
    assert body['response_format']['json_schema']['strict'] is True
    assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
    assert body['messages'][1]['content'].startswith('UNTRUSTED_DATA_JSON\n')
    assert body['include_reasoning'] is False and body['temperature'] == 0
    assert result['usage'] == {'inputTokens': 10, 'outputTokens': 20}
    assert result['provider'] == 'GROQ' and client.is_closed


@pytest.mark.parametrize('payload', [{'status': 'OK'}, {**PAYLOAD, 'extra': 'field'},
                                     {**PAYLOAD, 'status': 'VERIFIED'}, {**PAYLOAD, 'proposals': 'none'}])
def test_malformed_structured_reviews_rejected(payload):
    instance, client = provider(lambda request: httpx.Response(200, json=response(payload)))
    with pytest.raises(AIError) as caught: instance.invoke('recover', {})
    assert caught.value.code == 'INVALID_OUTPUT' and client.is_closed


@pytest.mark.parametrize('body,code', [
    (b'not json', 'INVALID_OUTPUT'),
    (json.dumps({'choices': []}).encode(), 'INVALID_OUTPUT'),
    (json.dumps(response(usage=None)).encode(), 'INVALID_OUTPUT'),
    (json.dumps(response(finish='length')).encode(), 'INCOMPLETE_OUTPUT'),
    (json.dumps(response(finish='tool_calls')).encode(), 'INCOMPLETE_OUTPUT'),
    (b'x' * 120001, 'INVALID_OUTPUT'),
], ids=['invalid-json', 'no-choices', 'null-usage', 'token-limit', 'tool-call', 'over-size-limit'])
def test_bad_envelopes_incomplete_and_oversized_output_rejected(body, code):
    instance, client = provider(lambda request: httpx.Response(200, content=body))
    with pytest.raises(AIError) as caught: instance.invoke('explain', {})
    assert caught.value.code == code and client.is_closed


@pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500, 302])
def test_upstream_errors_and_credentials_are_sanitized(monkeypatch, status):
    monkeypatch.setenv('GROQ_MAX_RETRIES', '0')
    instance, client = provider(lambda request: httpx.Response(status, text=TOKEN + ' secret server details'))
    state = instance.test_connection()
    assert state['connection_status'] == 'error' and state['model_accessible'] is False
    assert TOKEN not in json.dumps(state) and 'secret server details' not in json.dumps(state)
    assert 'token' not in state and 'api_key' not in state and client.is_closed


def test_provider_generated_schema_failure_retries_without_exposing_or_caching_output():
    private = TOKEN + ' sensitive DAT content'
    instance, client = provider(lambda request: httpx.Response(400, json={'error': {
        'code': 'json_validate_failed', 'type': 'invalid_request_error',
        'message': private + ': expected string got null /findings/21/field_id',
        'failed_generation': private}}))
    with pytest.raises(AIError) as caught:
        instance.invoke_structured('studio_dat', {}, AIResult, 'Review source fields.', scope='owner/project/source')
    assert caught.value.code == 'GROQ_OUTPUT_SCHEMA'
    assert caught.value.retryable and caught.value.retry_after == 1
    assert 'retry' in caught.value.message.lower()
    assert TOKEN not in str(caught.value) and 'sensitive DAT content' not in str(caught.value)
    assert 'failed_generation' not in str(caught.value) and client.is_closed
    with instance.coordinator.transaction() as db:
        assert db.execute('SELECT state FROM groq_calls').fetchall() == [('failed',)]
        assert db.execute('SELECT COUNT(*) FROM groq_cache').fetchone()[0] == 0


@pytest.mark.parametrize('body', [
    json.dumps({'error': {'code': 'invalid_request_error', 'message': TOKEN}}).encode(),
    json.dumps({'error': {'code': 'json_validate_failed ' + TOKEN, 'failed_generation': TOKEN}}).encode(),
    json.dumps({'error': TOKEN}).encode(),
    json.dumps([{'error': {'code': 'json_validate_failed'}}]).encode(),
    b'not JSON ' + TOKEN.encode(),
    b'\xff\xfe' + TOKEN.encode(),
    json.dumps({'error': {'code': 'json_validate_failed', 'message': TOKEN}}).encode() + b' ' * 20_000,
], ids=['configuration', 'malicious-code', 'malformed-error', 'wrong-root', 'invalid-json', 'invalid-encoding', 'oversized'])
def test_other_400_bodies_stay_nonretryable_and_sanitized(body):
    instance, client = provider(lambda request: httpx.Response(400, content=body))
    with pytest.raises(AIError) as caught:
        instance.invoke_structured('studio_dat', {}, AIResult, 'Review source fields.', scope='owner/project/source')
    assert caught.value.code == 'GROQ_400' and not getattr(caught.value, 'retryable', False)
    assert TOKEN not in str(caught.value) and 'failed_generation' not in str(caught.value)
    assert client.is_closed
    with instance.coordinator.transaction() as db:
        assert db.execute('SELECT state FROM groq_calls').fetchall() == [('failed',)]
        assert db.execute('SELECT COUNT(*) FROM groq_cache').fetchone()[0] == 0


def test_error_body_read_is_bounded_and_closes_response():
    class LargeError(httpx.SyncByteStream):
        bytes_read = 0
        closed = False

        def __iter__(self):
            for chunk in [b'{"error":{"code":"json_validate_failed"}}'] + [b' ' * 4096] * 100:
                self.bytes_read += len(chunk)
                yield chunk

        def close(self):
            self.closed = True

    stream = LargeError()
    instance, client = provider(lambda request: httpx.Response(400, stream=stream))
    with pytest.raises(AIError) as caught: instance.invoke('explain', {})
    assert caught.value.code == 'GROQ_400' and not getattr(caught.value, 'retryable', False)
    assert stream.bytes_read <= 24_576 and stream.closed and client.is_closed


def test_connection_status_tracks_key_rotation_without_exposing_credentials(monkeypatch):
    instance, _ = provider(lambda request: httpx.Response(200, json=response()))
    assert instance.status()['connection_status'] == 'not_tested'
    assert instance.test_connection()['connection_status'] == 'connected'
    monkeypatch.setenv('GROQ_API_KEY', 'rotated-synthetic-token')
    status = instance.status()
    assert status['model_accessible'] is None and status['connection_status'] == 'not_tested'
    assert 'rotated-synthetic-token' not in json.dumps(status)


def test_retries_return_shared_cooldown_without_sleeping_worker(monkeypatch):
    calls = []
    monkeypatch.setattr('groq_provider.time.sleep', lambda seconds: pytest.fail('worker must reschedule instead of sleeping'))
    monkeypatch.setenv('GROQ_MAX_RETRIES', '3')
    monkeypatch.setenv('GROQ_TIMEOUT_SECONDS', '5')
    def handler(request):
        calls.append(request)
        return httpx.Response(429, text='busy', headers={'retry-after': '120'})
    instance, client = provider(handler)
    with pytest.raises(AIError) as caught: instance.invoke('explain', {})
    assert caught.value.code == 'GROQ_429'
    assert caught.value.retryable and caught.value.retry_after == 120
    assert len(calls) == 1 and client.is_closed


def test_transient_server_failure_requires_queue_reschedule(monkeypatch):
    calls = []
    monkeypatch.setattr('groq_provider.time.sleep', lambda seconds: None)
    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=response())
    instance, _ = provider(handler)
    with pytest.raises(AIError) as caught: instance.invoke('explain', {})
    assert caught.value.code == 'GROQ_503' and caught.value.retryable and len(calls) == 1


@pytest.mark.parametrize('error,code', [(httpx.ReadTimeout('private request details'), 'TIMEOUT'),
                                       (httpx.ConnectError('private request details'), 'CONNECTION')])
def test_network_failures_are_safe_and_do_not_fallback(error, code, monkeypatch):
    monkeypatch.setattr(ai, 'AmazonBedrockProvider', lambda: pytest.fail('must not fall back'))
    def handler(request): raise error
    instance, client = provider(handler)
    with pytest.raises(AIError) as caught: instance.invoke('explain', {})
    assert caught.value.code == code and 'private request details' not in str(caught.value)
    assert client.is_closed


def test_disabled_missing_context_and_unknown_capability_never_create_client(monkeypatch):
    instance = GroqProvider(lambda settings: pytest.fail('must not create HTTP client'))
    monkeypatch.setenv('GROQ_ENABLED', 'false')
    with pytest.raises(AIError) as caught: instance.invoke('schema', {})
    assert caught.value.code == 'DISABLED' and instance.status()['connection_status'] == 'disabled'
    monkeypatch.setenv('GROQ_ENABLED', 'true'); monkeypatch.delenv('GROQ_API_KEY')
    with pytest.raises(AIError) as caught: instance.invoke('schema', {})
    assert caught.value.code == 'NOT_CONFIGURED' and not instance.status()['ai_available']
    monkeypatch.setenv('GROQ_API_KEY', TOKEN)
    with pytest.raises(AIError) as caught: instance.invoke('schema', {'large': 'x' * 48000})
    assert caught.value.code == 'CONTEXT_LIMIT'
    with pytest.raises(AIError) as caught: instance.invoke('execute_sql', {})
    assert caught.value.code == 'CAPABILITY'


def test_environment_loader_uses_private_file_without_overriding_process(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic.env'
    path.write_text('GROQ_API_KEY=from-file\nGROQ_MODEL="synthetic model" # local comment\nLLM_PROVIDER=groq\nUNRELATED_TEST_VAR=ignored\n')
    monkeypatch.delenv('GROQ_MODEL'); monkeypatch.delenv('UNRELATED_TEST_VAR', raising=False)
    ai.load_environment(path)
    assert ai.os.environ['GROQ_API_KEY'] == TOKEN
    assert ai.os.environ['GROQ_MODEL'] == 'synthetic model'
    assert 'UNRELATED_TEST_VAR' not in ai.os.environ
    path.write_text('GROQ_MODEL="unterminated synthetic token\n')
    with pytest.raises(AIError, match='Invalid quoting'): ai.load_environment(path)
    ai.load_environment(tmp_path / 'missing.env')


def test_provider_selection_is_explicit_with_no_fallback(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('LLM_PROVIDER', 'groq')
    assert isinstance(ai.configured_provider(), GroqProvider)
    monkeypatch.setenv('LLM_PROVIDER', 'bedrock')
    assert isinstance(ai.configured_provider(), AmazonBedrockProvider)
    monkeypatch.setenv('LLM_PROVIDER', 'unknown')
    with pytest.raises(AIError, match='LLM_PROVIDER'): ai.configured_provider()

    # Verify production rejects groq
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LLM_PROVIDER', 'groq')
    with pytest.raises(AIError, match='Amazon Bedrock only'): ai.configured_provider()
