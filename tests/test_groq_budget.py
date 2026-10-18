import hashlib
import json
import pytest
import httpx
from pydantic import BaseModel, ConfigDict
from bedrock import AIError
from groq_budget import GroqCoordinator, reset_seconds
from groq_provider import GroqProvider, TOKEN_CEILING, TOKENIZER_URL, local_tokenizer


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    fields: list[str]


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for key in ('GROQ_ENABLED', 'GROQ_MAX_TOKENS', 'GROQ_TIMEOUT_SECONDS', 'GROQ_RPM', 'GROQ_RPD', 'GROQ_TPM', 'GROQ_TPD', 'GROQ_CACHE_TTL_SECONDS'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-private-key')
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-120b')


def provider(coordinator=None, handler=None):
    calls = []
    def transport(request):
        calls.append(json.loads(request.content))
        if handler: return handler(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"fields":["F1"]}'}}],
                                        'usage': {'prompt_tokens': 20, 'completion_tokens': 10}})
    return GroqProvider(lambda settings: httpx.Client(transport=httpx.MockTransport(transport)),
                        coordinator=coordinator if coordinator is not None else GroqCoordinator(':memory:')), calls


def invoke(instance, context=None, scope='owner-a/project-a/source-v1'):
    return instance.invoke_structured('spec_analysis', context or {'source': 'synthetic'}, Review, 'Account for every field.', scope=scope)


def test_budget_counts_system_schema_and_reasoning_reservation_and_batches_all_items():
    instance, calls = provider()
    small = instance.budget('spec_analysis', {}, Review, 'Small task')
    verbose = instance.budget('spec_analysis', {}, Review, 'Additional rule. ' * 300)
    assert verbose['input_tokens'] > small['input_tokens'] > 256
    assert small['reserved_completion_tokens'] == 2000
    assert small['total_tokens'] == small['input_tokens'] + 2000 <= TOKEN_CEILING == 8048
    items = [{'id': index, 'source': 'Exact source values, widths and padding rules. ' * 30} for index in range(120)]
    batches = instance.batches('spec_analysis', items, Review, 'Preserve source references.', context={'document': 'source-hash'}, key='fields')
    assert len(batches) > 1 and [item for batch in batches for item in batch['fields']] == items
    assert all(batch['document'] == 'source-hash' for batch in batches)
    assert all(instance.budget('spec_analysis', batch, Review, 'Preserve source references.')['fits'] for batch in batches)
    bounded = instance.batches('spec_analysis', items, Review, 'Preserve source references.', key='fields', max_items=3)
    assert max(len(batch['fields']) for batch in bounded) <= 3
    assert [item for batch in bounded for item in batch['fields']] == items
    assert not calls


def test_input_or_schema_overflow_is_rejected_before_any_network_call():
    instance, calls = provider()
    with pytest.raises(AIError) as caught:
        instance.invoke_structured('spec_analysis', {}, Review, 'Rule and schema explanation. ' * 5000, scope='owner/project')
    assert caught.value.code == 'CONTEXT_LIMIT' and not calls
    with pytest.raises(AIError, match='One analysis item'):
        instance.batches('spec_analysis', [{'text': 'Uncompressible field context. ' * 10000}], Review, 'Interpret fields')


def test_completion_reservation_includes_reasoning_and_reported_usage_is_checked():
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"fields":[]}'}}],
                                        'usage': {'prompt_tokens': 20, 'completion_tokens': 2001, 'completion_tokens_details': {'reasoning_tokens': 1800}}})
    instance, calls = provider(handler=handler)
    with pytest.raises(AIError) as caught: invoke(instance)
    assert caught.value.code == 'TOKEN_ACCOUNTING'
    assert calls[0]['max_completion_tokens'] == 2000 and calls[0]['include_reasoning'] is False


def test_missing_or_corrupt_local_vocabulary_fails_without_downloading(tmp_path, monkeypatch):
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR', str(tmp_path))
    monkeypatch.setattr('requests.get', lambda *args, **kwargs: pytest.fail('request-time vocabulary download prohibited'))
    with pytest.raises(AIError) as caught: local_tokenizer()
    assert caught.value.code == 'TOKENIZER_CONFIGURATION'
    cache = tmp_path / hashlib.sha1(TOKENIZER_URL.encode()).hexdigest(); cache.write_bytes(b'corrupt vocabulary')
    with pytest.raises(AIError) as caught: local_tokenizer()
    assert caught.value.code == 'TOKENIZER_CONFIGURATION'


def test_structured_cache_requires_scope_and_survives_restart_without_quota_charge(tmp_path):
    path = tmp_path / 'shared.sqlite3'; first, calls = provider(GroqCoordinator(path))
    with pytest.raises(AIError, match='scope'): first.invoke_structured('spec_analysis', {}, Review, 'Interpret fields')
    result = invoke(first)
    assert not result['cached'] and result['result'] == {'fields': ['F1']} and len(calls) == 1
    first.coordinator.close()
    restarted, calls = provider(GroqCoordinator(path))
    cached = invoke(restarted)
    assert cached['cached'] and not calls and cached['result'] == result['result']
    with restarted.coordinator.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM groq_calls').fetchone()[0] == 1
    assert b'synthetic-private-key' not in path.read_bytes()


def test_cache_isolated_by_owner_project_source_model_rules_and_content(tmp_path):
    instance, calls = provider(GroqCoordinator(tmp_path / 'cache.sqlite3'))
    invoke(instance)
    assert invoke(instance)['cached'] and len(calls) == 1
    invoke(instance, scope='owner-b/project-a/source-v1')
    invoke(instance, scope='owner-a/project-b/source-v1')
    invoke(instance, scope='owner-a/project-a/source-v2')
    invoke(instance, context={'source': 'changed content'})
    instance.invoke_structured('spec_analysis', {'source': 'synthetic'}, Review, 'Different interpretation rules.', scope='owner-a/project-a/source-v1')
    assert len(calls) == 6


def test_one_active_request_per_credential_across_coordinators_and_expired_lease_recovery(tmp_path):
    now = [1000.]; clock = lambda: now[0]; path = tmp_path / 'coord.sqlite3'
    first = GroqCoordinator(path, clock); second = GroqCoordinator(path, clock)
    lease = first.reserve('hashed-credential', None, 100, 10)['reservation']
    with pytest.raises(AIError) as caught: second.reserve('hashed-credential', None, 100, 10)
    assert caught.value.code == 'AI_BUSY' and caught.value.retryable and caught.value.retry_after <= 5
    other = second.reserve('different-credential', None, 100, 10)['reservation']
    second.finish(other, actual_tokens=10)
    first.close(); now[0] += 11
    recovered = second.reserve('hashed-credential', None, 100, 10)['reservation']
    second.finish(recovered, actual_tokens=20)
    with pytest.raises(AIError, match='expired'): first.finish(lease)


@pytest.mark.parametrize('limit,first_tokens,next_tokens,expected', [('GROQ_RPM', 10, 10, 'QUOTA_MINUTE'),
                                                                  ('GROQ_RPD', 10, 10, 'QUOTA_DAILY'),
                                                                  ('GROQ_TPM', 6000, 3000, 'QUOTA_MINUTE'),
                                                                  ('GROQ_TPD', 6000, 3000, 'QUOTA_DAILY')])
def test_shared_minute_and_daily_quotas(tmp_path, monkeypatch, limit, first_tokens, next_tokens, expected):
    monkeypatch.setenv(limit, '8000' if 'TP' in limit else '1')
    if limit == 'GROQ_TPD': monkeypatch.setenv('GROQ_TPM', '20000')
    now = [1000.]; coordinator = GroqCoordinator(tmp_path / 'quotas.sqlite3', lambda: now[0])
    reservation = coordinator.reserve('credential-hash', None, first_tokens, 10)['reservation']
    coordinator.finish(reservation, actual_tokens=first_tokens)
    with pytest.raises(AIError) as caught: coordinator.reserve('credential-hash', None, next_tokens, 10)
    assert caught.value.code == expected and caught.value.retryable
    assert caught.value.retry_after == (86400 if expected == 'QUOTA_DAILY' else 60)
    now[0] += caught.value.retry_after
    assert coordinator.reserve('credential-hash', None, next_tokens, 10)['reservation']


def test_success_usage_releases_unused_reserved_tokens_and_cache_bypasses_exhaustion(monkeypatch):
    monkeypatch.setenv('GROQ_TPM', '2000'); monkeypatch.setenv('GROQ_RPD', '1')
    coordinator = GroqCoordinator(':memory:')
    reservation = coordinator.reserve('hash', 'scope-content-hash', 1900, 10)['reservation']
    coordinator.finish(reservation, run={'result': {'fields': []}}, actual_tokens=30)
    assert coordinator.reserve('hash', 'scope-content-hash', 1900, 10)['cached'] == {'result': {'fields': []}}
    with pytest.raises(AIError) as caught: coordinator.reserve('hash', 'different-source', 1900, 10)
    assert caught.value.code == 'QUOTA_DAILY'


def test_rate_headers_retry_after_and_reset_are_persistent_across_workers(tmp_path):
    now = [1000.]; path = tmp_path / 'headers.sqlite3'; first = GroqCoordinator(path, lambda: now[0])
    reservation = first.reserve('hash', None, 100, 10)['reservation']
    delay = first.finish(reservation, headers={'retry-after': '120', 'x-ratelimit-remaining-tokens': '0',
                                              'x-ratelimit-reset-tokens': '2m59.56s'}, status=429)
    assert delay == pytest.approx(179.56)
    first.close(); second = GroqCoordinator(path, lambda: now[0])
    with pytest.raises(AIError) as caught: second.reserve('hash', None, 100, 10)
    assert caught.value.code == 'QUOTA_COOLDOWN' and caught.value.retry_after == 180
    now[0] += 180
    assert second.reserve('hash', None, 100, 10)['reservation']


def test_daily_request_headers_and_observed_token_limit_are_respected():
    now = [1000.]; coordinator = GroqCoordinator(':memory:', lambda: now[0])
    reservation = coordinator.reserve('hash', None, 100, 10)['reservation']
    coordinator.finish(reservation, actual_tokens=20, headers={'x-ratelimit-remaining-requests': '0',
                      'x-ratelimit-reset-requests': '1h', 'x-ratelimit-limit-tokens': '200'})
    with pytest.raises(AIError) as caught: coordinator.reserve('hash', None, 100, 10)
    assert caught.value.code == 'QUOTA_DAILY' and caught.value.retry_after == 3600
    now[0] += 3600
    with pytest.raises(AIError) as caught: coordinator.reserve('hash', None, 201, 10)
    assert caught.value.code == 'QUOTA_CONFIGURATION'
    assert reset_seconds('1m2.5s', now[0]) == 62.5 and reset_seconds('250ms', now[0]) == .25
    assert reset_seconds('172800', now[0]) == 172800
    assert reset_seconds('Thu, 01 Jan 1970 01:16:42 GMT', now[0]) == 2
