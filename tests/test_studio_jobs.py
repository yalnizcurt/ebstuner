import copy
import pytest
from bedrock import AIError
from groq_budget import retry_error
from projects import Repository, encode
from studio import StudioService, digest, fingerprint, normalize


class SyntheticProvider:
    def __init__(self):
        self.calls = []; self.fail_once = None; self.after_call = None

    def status(self): return {'provider': 'Synthetic', 'model_id': 'synthetic-offline-model'}

    def batches(self, capability, items, response_model, instructions, context=None, key='items', max_items=None):
        return [{**(context or {}), key: [item]} for item in items] or [{**(context or {}), key: []}]

    def invoke_structured(self, capability, context, response_model, instructions, scope):
        self.calls.append({'capability': capability, 'context': copy.deepcopy(context), 'scope': scope})
        if self.fail_once == len(self.calls):
            self.fail_once = None
            raise retry_error('QUOTA_MINUTE', 'Synthetic quota wait', 120)
        if capability == 'studio_spec':
            interpretations = []
            for group in context['groups']:
                integer = group['datatype_conversion'] == 'INTEGER EXTERNAL'
                interpretations.append({'group_id': group['group_id'], 'datatype': 'integer' if integer else 'char',
                     'conversion': 'to_number' if integer else 'identity', 'date_format': '',
                     'padding_side': 'right', 'padding_char': ' ', 'allow_internal_spaces': False,
                     'required': False, 'explanation': 'Synthetic interpretation', 'questions': []})
            result = {'summary': 'Synthetic spec analysis', 'interpretations': interpretations, 'observations': []}
        else: result = {'summary': 'Synthetic DAT review', 'findings': [], 'observations': []}
        response_model.model_validate(result)
        if self.after_call: self.after_call()
        return {'result': result, 'usage': {'inputTokens': 10, 'outputTokens': 10},
                'provider': 'SYNTHETIC', 'estimated_cost_usd': None, 'latency_ms': 1}


@pytest.fixture
def work(tmp_path, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr('studio.time.time', lambda: clock[0])
    repository = Repository(tmp_path / 'jobs.sqlite3'); owner = 'owner-a'
    project = repository.create(owner, 'Synthetic durable review')
    fields = [{'id': 'code', 'field_name': 'CODE', 'mapping_type': 'POSITION', 'start_pos': '1', 'end_pos': '2',
               'length': '2', 'datatype_conversion': 'CHAR', 'padding_alignment': 'Trailing spaces'},
              {'id': 'quantity', 'field_name': 'QUANTITY', 'mapping_type': 'POSITION', 'start_pos': '3', 'end_pos': '6',
               'length': '4', 'datatype_conversion': 'INTEGER EXTERNAL', 'padding_alignment': 'Trailing spaces'}]
    spec = normalize({'fields': fields, 'options': {'encoding': 'ascii', 'record_format': 'stream'}})
    spec.update(approved=False, ai_complete=False, version=0)
    project.update(original=encode(b'AB0042\r\n'), data=encode(b'AB0042\r\n'))
    project['studio'] = {'document': {'sha256': 'synthetic-document-hash', 'filename': 'spec.csv', 'sections': []},
                         'spec': spec, 'stage': 'spec_review', 'edits': [], 'undo': [], 'redo': [], 'generated': None,
                         'dat_name': 'input.dat', 'spec_source': encode(b'synthetic source')}
    repository.save(owner, project, project['version'])
    provider = SyntheticProvider(); service = StudioService(lambda: repository, lambda: provider)
    return repository, owner, project, provider, service, clock


def test_durable_duplicate_submit_claim_scope_and_completed_spec(work):
    repository, owner, project, provider, service, clock = work
    ident = service.enqueue(owner, project, 'spec')
    restarted = StudioService(lambda: repository, lambda: provider)
    assert restarted.enqueue(owner, project, 'spec') == ident
    job = restarted.claim(); assert job['id'] == ident and service.claim() is None
    restarted.process(job)
    stored = repository.load(owner, project['id'])
    assert stored['studio']['spec']['ai_complete'] and not stored['studio']['spec']['approved']
    assert restarted.jobs(owner, project['id'])[0]['status'] == 'succeeded'
    assert restarted.jobs('owner-b', project['id']) == []
    assert len(provider.calls) == 2
    assert all(call['scope'].startswith(owner + '/' + project['id'] + '/' + job['fingerprint'] + '/') for call in provider.calls)


def test_partial_batch_results_resume_after_quota_wait_without_repeating_success(work):
    repository, owner, project, provider, service, clock = work
    provider.fail_once = 2
    service.enqueue(owner, project, 'spec'); service.process(service.claim())
    queued = service.jobs(owner, project['id'])[0]
    assert queued['status'] == 'retry_wait' and queued['completed_batches'] == 1 and queued['retry_at'] == 1120
    assert service.claim() is None
    clock[0] = 1120
    restarted = StudioService(lambda: repository, lambda: provider)
    job = restarted.claim(); assert len(job['payload']['responses']) == 1
    restarted.process(job)
    assert restarted.jobs(owner, project['id'])[0]['status'] == 'succeeded'
    assert len(provider.calls) == 3 and provider.calls[0]['context'] != provider.calls[2]['context']


def test_repeated_quota_admission_waits_do_not_exhaust_transient_failure_budget(work):
    repository, owner, project, provider, service, clock = work
    service.enqueue(owner, project, 'spec')
    for _ in range(7):
        provider.fail_once = len(provider.calls) + 1
        service.process(service.claim())
        pending = service.jobs(owner, project['id'])[0]
        assert pending['status'] == 'retry_wait'
        clock[0] = pending['retry_at']
    service.process(service.claim())
    assert service.jobs(owner, project['id'])[0]['status'] == 'succeeded'


def test_permanent_provider_error_preserves_files_and_requires_explicit_retry(work, monkeypatch):
    repository, owner, project, provider, service, clock = work
    def unavailable(*args, **kwargs): raise AIError('NOT_CONFIGURED', 'Synthetic missing configuration')
    monkeypatch.setattr(provider, 'invoke_structured', unavailable)
    service.enqueue(owner, project, 'spec'); service.process(service.claim())
    assert service.jobs(owner, project['id'])[0]['status'] == 'failed' and service.claim() is None
    stored = repository.load(owner, project['id'])
    assert stored['original'] == project['original'] and not stored['studio']['spec']['ai_complete']


def test_expired_claim_can_be_recovered_after_worker_restart(work):
    repository, owner, project, provider, service, clock = work
    ident = service.enqueue(owner, project, 'spec'); abandoned = service.claim()
    clock[0] += 301
    restarted = StudioService(lambda: repository, lambda: provider)
    recovered = restarted.claim(); assert recovered['id'] == ident and recovered['attempts'] > abandoned['attempts']
    restarted.process(recovered)
    assert restarted.jobs(owner, project['id'])[0]['status'] == 'succeeded'


def test_changed_source_supersedes_queued_job_without_calling_model(work):
    repository, owner, project, provider, service, clock = work
    service.enqueue(owner, project, 'spec'); job = service.claim()
    changed = repository.load(owner, project['id'])
    changed['studio']['spec']['fields'][0]['padding_alignment'] = 'Leading spaces'
    repository.save(owner, changed, changed['version'])
    service.process(job)
    assert service.jobs(owner, project['id'])[0]['status'] == 'superseded' and not provider.calls


def test_partial_job_remains_queued_when_shutdown_interrupts_between_batches(work):
    repository, owner, project, provider, service, clock = work
    provider.after_call = service.stop_event.set
    service.enqueue(owner, project, 'spec'); service.process(service.claim())
    pending = service.jobs(owner, project['id'])[0]
    assert pending['status'] in ('queued', 'retry_wait') and pending['completed_batches'] == 1
    clock[0] = pending.get('retry_at') or clock[0]
    restarted = StudioService(lambda: repository, lambda: provider); provider.after_call = None
    restarted.process(restarted.claim())
    assert restarted.jobs(owner, project['id'])[0]['status'] == 'succeeded' and len(provider.calls) == 2


def test_dat_review_uses_approved_version_and_stale_edits_supersede_job(work):
    repository, owner, project, provider, service, clock = work
    approved = copy.deepcopy(project['studio']['spec'])
    for field in approved['fields']:
        field['mandatory'] = 'NO'
        field['rules'].update(required=False, allow_internal_spaces=False)
    approved.update(approved=True, ai_complete=True, version=1)
    assert not normalize(approved)['issues']
    project['studio'].update(spec=approved, approved_spec=copy.deepcopy(approved), approved_checksum=digest(approved))
    repository.save(owner, project, project['version'])
    service.enqueue(owner, project, 'dat'); job = service.claim()
    before = fingerprint(project, 'dat')
    project['studio']['edits'] = [{'record': 1, 'field_id': 'quantity', 'value': '42'}]
    assert fingerprint(project, 'dat') != before
    repository.save(owner, project, project['version'])
    service.process(job)
    assert service.jobs(owner, project['id'])[0]['status'] == 'superseded' and not provider.calls
    service.enqueue(owner, project, 'dat'); service.process(service.claim())
    stored = repository.load(owner, project['id'])
    assert stored['studio']['dat_ai']['complete']
    assert stored['studio']['dat_ai']['spec_checksum'] == project['studio']['approved_checksum']
