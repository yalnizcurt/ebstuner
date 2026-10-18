"""Real HTTP workflow with explicit synthetic AI; never consumes provider quota."""
import copy
import csv
import io
import time

import pytest
from fastapi.testclient import TestClient

import specs
import web
from projects import Repository


HEADERS = [c['label'] for c in specs.DISPLAY_COLUMNS]
ROWS = [
    ['CUSTOMER', 'Customer', 'POSITION', '1', '20', '20', 'CHAR', 'Trailing spaces', 'ABC Trading', 'ABC Trading'],
    ['AMOUNT', 'Payment', 'POSITION', '21', '30', '10', 'DECIMAL EXTERNAL', 'Leading spaces', '1250.00', '1250.00'],
    ['PAYMENT_DATE', 'Payment', 'POSITION', '31', '38', '8', 'DATE "YYYYMMDD"', 'No padding', '20261017', '2026-10-17'],
]
GOOD = b'ABC Trading            1250.0020261017\r\n'
BAD = b'ABC Trading            12O0.0020260230\r\n'
H = {'X-Studio-Request': '1'}


def source(headers=None):
    output = io.StringIO(newline='')
    csv.writer(output).writerows([headers or HEADERS, *ROWS])
    return output.getvalue().encode()


class SyntheticAI:
    def __init__(self):
        self.calls = []

    def status(self):
        return {'provider': 'Groq', 'model_id': 'synthetic-offline-only', 'ai_available': True}

    def batches(self, capability, items, response_model, instructions, context=None, key='items'):
        return [{**(context or {}), key: items}]

    def invoke_structured(self, capability, context, response_model, instructions, scope):
        self.calls.append((capability, copy.deepcopy(context), scope))
        if capability == 'studio_headers':
            mapping = dict(zip(['Business field', 'Team', 'Source mapping', 'From byte', 'To byte', 'Size', 'Input format', 'Spacing', 'Original example', 'Stage example'], [c['key'] for c in specs.DISPLAY_COLUMNS]))
            result = {'summary': 'Synthetic heading mapping', 'mappings': [{'source_header': k, 'target_column': v} for k, v in mapping.items()], 'observations': []}
        elif capability == 'studio_spec':
            interpretations = []
            for group in context['groups']:
                text = group['datatype_conversion']
                datatype = 'date' if text.startswith('DATE') else 'decimal' if text.startswith('DECIMAL') else 'char'
                side = 'left' if group['padding_alignment'] == 'Leading spaces' else 'none' if datatype == 'date' else 'right'
                interpretations.append({'group_id': group['group_id'], 'datatype': datatype,
                    'conversion': 'to_date' if datatype == 'date' else 'to_number' if datatype == 'decimal' else 'identity',
                    'date_format': 'YYYYMMDD' if datatype == 'date' else '', 'padding_side': side, 'padding_char': ' ',
                    'required': True, 'allow_internal_spaces': datatype == 'char',
                    'explanation': 'Synthetic source interpretation', 'questions': []})
            result = {'summary': 'All supplied rule groups interpreted', 'interpretations': interpretations, 'observations': []}
        elif capability == 'studio_alignment':
            result = {'evidence_id': context['evidence_id'], 'status': 'agree',
                      'summary': 'Synthetic alignment evidence review', 'preferred_candidate_ids': [],
                      'findings': [], 'contradictions': [], 'spec_changes': [], 'rule_proposals': []}
        else:
            result = {'summary': 'Synthetic review of all reported checks', 'findings': [], 'observations': []}
        response_model.model_validate(result)
        return {'result': result, 'usage': {'inputTokens': 100, 'outputTokens': 100}, 'provider': 'SYNTHETIC', 'model_id': 'offline'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'repository', Repository(tmp_path / 'projects.sqlite3'))
    monkeypatch.setattr(web, 'provider', SyntheticAI())
    monkeypatch.delenv('STUDIO_ENV', raising=False)
    monkeypatch.delenv('STUDIO_ACCESS_TOKEN', raising=False)
    web.rates.clear()
    with TestClient(web.app) as session:
        assert session.post('/api/session', json={}, headers=H).status_code == 200
        yield session


def upload(client, dat=BAD, headers=None):
    created = client.post('/api/projects', json={'name': 'Spec-first acceptance'}, headers=H).json()
    pid = created['project']['id']
    response = client.post(f'/api/projects/{pid}/studio/files', headers=H, data={'version': 1},
                           files={'spec': ('spec.csv', source(headers)), 'dat': ('source.dat', dat)})
    assert response.status_code == 200, response.text
    return pid, response.json()


def call(client, pid, state, suffix, **extra):
    response = client.post(f'/api/projects/{pid}/studio{suffix}', json={'version': state['project']['version'], **extra}, headers=H)
    return response


def finish(client, pid, kind):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(f'/api/projects/{pid}/studio')
        assert response.status_code == 200, response.text
        state = response.json()
        jobs = [j for j in state['jobs'] if j['kind'] == kind]
        if jobs and jobs[0]['status'] in ('succeeded', 'failed', 'superseded'):
            assert jobs[0]['status'] == 'succeeded', jobs[0]
            return state
        time.sleep(.02)
    pytest.fail('Synthetic AI job did not finish')


def approved(client, dat=BAD, headers=None):
    pid, state = upload(client, dat, headers)
    assert call(client, pid, state, '/approve').status_code == 409
    assert call(client, pid, state, '/analyze-spec').status_code == 200
    state = finish(client, pid, 'spec')
    assert state['spec']['ai_complete'] and all(i['code'] == 'REQUIRED_RULE' for i in state['spec']['issues'])
    fields = copy.deepcopy(state['spec']['fields'])
    for field in fields:
        if field['rules']['mapping_type'] == 'physical': field['mandatory'] = 'YES'
    changed = client.put(f'/api/projects/{pid}/studio/spec', json={'version': state['project']['version'],
        'fields': fields, 'options': state['spec']['options'], 'layouts': state['spec']['layouts']}, headers=H)
    assert changed.status_code == 200, changed.text
    state = changed.json()
    assert not state['spec']['issues']
    response = call(client, pid, state, '/approve')
    assert response.status_code == 200, response.text
    return pid, response.json()


def test_real_spec_first_corrections_ai_gate_exact_download_and_invalidation(client):
    pid, state = approved(client)
    assert len(state['spec']['fields']) == 3 and state['review']['record_count'] == 1
    assert all(all(c['key'] in f for c in specs.DISPLAY_COLUMNS) for f in state['spec']['fields'])
    assert 0 < state['review']['summary']['percentage'] < 100
    assert call(client, pid, state, '/generate').status_code == 409
    assert call(client, pid, state, '/analyze-dat').status_code == 200
    state = finish(client, pid, 'dat')
    assert call(client, pid, state, '/generate').status_code == 409
    fields = {f['field_name']: f for f in state['spec']['fields']}
    edits = [{'record': 1, 'field_id': fields['AMOUNT']['id'], 'value': ' 1250.00 '},
             {'record': 1, 'field_id': fields['PAYMENT_DATE']['id'], 'value': '20261017'}]
    response = client.patch(f'/api/projects/{pid}/studio/values', json={'version': state['project']['version'], 'edits': edits}, headers=H)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['review']['summary']['percentage'] == 100 and not state['ai_review']['complete']
    original_amount = next(f for f in state['review']['record']['fields'] if f['id'] == fields['AMOUNT']['id'])
    assert original_amount['original_value'] == '12O0.00' and original_amount['value'] == '1250.00'
    assert original_amount['original_percentage'] < 100
    assert call(client, pid, state, '/generate').status_code == 409
    assert call(client, pid, state, '/analyze-dat').status_code == 200
    state = finish(client, pid, 'dat')
    for review in state['ai_review']['alignment_reviews']:
        if review['reconciliation']['human_review_required']:
            decision = call(client, pid, state, '/ai-decision', record=review['record'],
                            evidence_id=review['evidence_id'], decision='reject',
                            reason='The explicit corrections retain the approved source specification.')
            assert decision.status_code == 200, decision.text
            state = decision.json()
    response = call(client, pid, state, '/generate')
    assert response.status_code == 200, response.text
    state = response.json()
    downloaded = client.get(f'/api/projects/{pid}/studio/download')
    assert downloaded.status_code == 200 and downloaded.content == GOOD
    assert client.get(f'/api/projects/{pid}/studio/download?original=true').content == BAD
    assert downloaded.headers['x-checksum-sha256'] == state['generated']['checksum']
    calls = len(web.provider.calls)
    assert call(client, pid, state, '/analyze-dat').status_code == 200
    assert len(web.provider.calls) == calls
    response = call(client, pid, state, '/history/undo')
    assert response.status_code == 200 and response.json()['generated'] is None
    assert client.get(f'/api/projects/{pid}/studio/download').status_code == 409


def test_master_template_and_approved_downloads(client):
    template = client.get('/api/studio/master-template')
    assert template.status_code == 200
    assert next(csv.reader(io.StringIO(template.text))) == [column['label'] for column in specs.MASTER_COLUMNS]
    pid, state = approved(client, GOOD)
    approved_spec = client.get(f'/api/projects/{pid}/studio/approved-spec')
    assert approved_spec.status_code == 200
    assert approved_spec.json()['version'] == state['spec']['version']
    report = client.get(f'/api/projects/{pid}/studio/report')
    assert report.status_code == 200
    assert report.json()['spec_version'] == state['spec']['version']


def test_unfamiliar_headers_are_mapped_by_ai_without_losing_source_columns(client):
    unknown = ['Business field', 'Team', 'Source mapping', 'From byte', 'To byte', 'Size', 'Input format', 'Spacing', 'Original example', 'Stage example']
    pid, state = approved(client, GOOD, unknown)
    assert len(state['spec']['fields']) == 3
    assert [c[0] for c in web.provider.calls] == ['studio_headers', 'studio_spec']
    assert state['spec']['fields'][0]['source']['cells']['field_name']['value'] == 'CUSTOMER'


def test_oversized_atomic_edits_ownership_and_immutable_sources(client):
    pid, state = approved(client, GOOD)
    fid = state['spec']['fields'][0]['id']
    invalid = client.patch(f'/api/projects/{pid}/studio/values', json={'version': state['project']['version'], 'edits': [
        {'record': 1, 'field_id': fid, 'value': 'Changed'}, {'record': 1, 'field_id': fid, 'value': 'X' * 21}]}, headers=H)
    assert invalid.status_code == 422
    current = client.get(f'/api/projects/{pid}/studio').json()
    assert current['project']['version'] == state['project']['version'] and not current['undo_count']
    assert client.post(f'/api/projects/{pid}/files', headers=H, data={'version': state['project']['version']},
                       files={'ctl': ('bypass.ctl', b'LOAD DATA'), 'dat': ('bad.dat', b'changed')}).status_code == 409
    assert client.get(f'/api/projects/{pid}/studio/download?original=true').content == GOOD
    with TestClient(web.app) as stranger:
        assert stranger.get(f'/api/projects/{pid}/studio').status_code == 401
        stranger.post('/api/session', json={}, headers=H)
        assert stranger.get(f'/api/projects/{pid}/studio').status_code == 404


def test_spec_revision_invalidates_approval_but_preserves_explicit_rules(client):
    pid, state = approved(client, GOOD)
    fields = copy.deepcopy(state['spec']['fields'])
    fields[0]['padding_alignment'] = 'Left aligned; trailing spaces'
    fields[0]['rules']['required'] = False
    fields[0]['mandatory'] = 'NO'
    fields[0]['rules']['allowed_values'] = ['ABC Trading']
    response = client.put(f'/api/projects/{pid}/studio/spec', json={'version': state['project']['version'],
        'fields': fields, 'options': state['spec']['options'], 'layouts': state['spec']['layouts']}, headers=H)
    assert response.status_code == 200, response.text
    state = response.json()
    assert not state['spec']['approved'] and not state['spec']['ai_complete']
    assert state['spec']['fields'][0]['rules']['required'] is False
    assert state['spec']['fields'][0]['rules']['allowed_values'] == ['ABC Trading']
    assert call(client, pid, state, '/generate').status_code == 409
