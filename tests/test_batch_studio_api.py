"""Multi-file project API and byte-preserving routing acceptance."""
import csv
import io
import json
import zipfile

from fastapi.testclient import TestClient

import specs
import web
from projects import Repository
from tests.test_studio_api import SyntheticAI
from fixtures.synthetic_generator import SyntheticFixtureGenerator


H = {'X-Studio-Request': '1'}


def source(label, mandatory='YES'):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([c['label'] for c in specs.MASTER_COLUMNS])
    field = {'id': 'code', 'field_name': 'Record code', 'mapping_type': 'POSITION', 'start_pos': '1',
             'end_pos': '2', 'length': '2', 'datatype_conversion': 'CHAR', 'padding_alignment': 'No padding',
             'mandatory': mandatory, 'sequence': '1', 'raw_sample': label}
    writer.writerow([field.get(c['key'], '') for c in specs.MASTER_COLUMNS])
    return output.getvalue().encode()


def repair_source():
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([c['label'] for c in specs.MASTER_COLUMNS])
    for ident, start, end, value in [('alpha', 1, 5, 'AB'), ('omega', 6, 8, 'XYZ')]:
        field = {'id': ident, 'field_name': ident, 'mapping_type': 'POSITION', 'start_pos': str(start),
                 'end_pos': str(end), 'length': str(end - start + 1), 'datatype_conversion': 'CHAR',
                 'padding_alignment': 'Trailing spaces', 'mandatory': 'YES', 'allowed_values': value}
        writer.writerow([field.get(c['key'], '') for c in specs.MASTER_COLUMNS])
    return output.getvalue().encode()


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'repository', Repository(tmp_path / 'batch.sqlite3'))
    monkeypatch.setattr(web, 'provider', SyntheticAI())
    monkeypatch.delenv('STUDIO_ENV', raising=False)
    monkeypatch.delenv('STUDIO_ACCESS_TOKEN', raising=False)
    web.rates.clear()
    client = TestClient(web.app)
    client.__enter__()
    assert client.post('/api/session', json={}, headers=H).status_code == 200
    created = client.post('/api/projects', json={'name': 'Batch'}, headers=H).json()
    return client, created['project']['id']


def endpoint(pid, path=''):
    return f'/api/projects/{pid}/studio/batch{path}'


def upload(client, pid, version, specs_in=(), dats_in=()):
    files = [('specs', (name, data, 'text/csv')) for name, data in specs_in]
    files += [('dats', (name, data, 'application/octet-stream')) for name, data in dats_in]
    response = client.post(endpoint(pid, '/files'), data={'version': version}, files=files, headers=H)
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, pid, state, spec_id, marker=None):
    item = next(s for s in state['batch']['specs'] if s['id'] == spec_id)
    layouts = item['layouts']
    if marker:
        layouts[0]['when'] = [{'start': 1, 'end': 2, 'equals': marker}]
    response = client.put(endpoint(pid, f'/specs/{spec_id}'), json={'version': state['project']['version'],
        'fields': item['fields'], 'layouts': layouts, 'options': item['options']}, headers=H)
    assert response.status_code == 200, response.text
    state = response.json()
    response = client.post(endpoint(pid, f'/specs/{spec_id}/approve'),
                           json={'version': state['project']['version']}, headers=H)
    assert response.status_code == 200, response.text
    return response.json()


def post(client, pid, state, path):
    return client.post(endpoint(pid, path), json={'version': state['project']['version']}, headers=H)


def test_two_specs_two_dats_and_exact_archive(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('a.csv', source('AA')), ('b.csv', source('BB'))],
                       [('one.dat', b'AA\nBB\n'), ('two.dat', b'BB\n')])
        sa, sb = [s['id'] for s in state['batch']['specs']]
        da, db = [d['id'] for d in state['batch']['dats']]
        state = approve(client, pid, state, sa, 'AA')
        state = approve(client, pid, state, sb, 'BB')
        association = [{'dat_id': da, 'spec_id': sa, 'layout_ids': ['primary']},
                       {'dat_id': da, 'spec_id': sb, 'layout_ids': ['primary']},
                       {'dat_id': db, 'spec_id': sb, 'layout_ids': ['primary']}]
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
                              'associations': association}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        response = post(client, pid, state, '/process')
        assert response.status_code == 200, response.text
        state = response.json()
        assert state['batch']['summary']['validated'] == 3
        rows = client.get(endpoint(pid, f'/records/{da}')).json()['records']
        assert [(r['spec_id'], r['layout_id']) for r in rows] == [(sa, 'primary'), (sb, 'primary')]
        blocked = post(client, pid, state, '/generate')
        assert blocked.status_code == 200 and blocked.json()['batch']['summary']['export_ready'] == 0
        state = blocked.json()
        response = post(client, pid, state, '/ai')
        assert response.status_code == 200, response.text
        state = response.json()
        response = post(client, pid, state, '/generate')
        assert response.status_code == 200, response.text
        assert client.get(endpoint(pid, f'/download/{da}')).content == b'AA\nBB\n'
        assert client.get(endpoint(pid, f'/download/{db}')).content == b'BB\n'
        with zipfile.ZipFile(io.BytesIO(client.get(endpoint(pid, '/archive')).content)) as archive:
            assert archive.read(f'validated-{da}.dat') == b'AA\nBB\n'
            assert archive.read(f'validated-{db}.dat') == b'BB\n'
    finally:
        client.__exit__(None, None, None)


def test_unknown_and_ambiguous_records_remain_original(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('a.csv', source('AA')), ('b.csv', source('BB'))],
                       [('mixed.dat', b'AA\nZZ\n')])
        sa, sb = [s['id'] for s in state['batch']['specs']]
        did = state['batch']['dats'][0]['id']
        state = approve(client, pid, state, sa, 'AA')
        state = approve(client, pid, state, sb, 'AA')
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
            'associations': [{'dat_id': did, 'spec_id': sid, 'layout_ids': ['primary']} for sid in (sa, sb)]}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        response = post(client, pid, state, '/process')
        assert response.status_code == 200, response.text
        state = response.json()
        rows = client.get(endpoint(pid, f'/records/{did}')).json()['records']
        assert [r['code'] for r in rows] == ['AMBIGUOUS_LAYOUT', 'UNKNOWN_LAYOUT']
        assert all(r['byte_preserved'] for r in rows)
        assert state['batch']['summary']['unvalidated'] == 2
        assert client.get(endpoint(pid, f'/download/{did}?original=true')).content == b'AA\nZZ\n'
        assert post(client, pid, state, '/generate').json()['batch']['summary']['export_ready'] == 0
    finally:
        client.__exit__(None, None, None)


def test_one_spec_multiple_dats_partial_failure_and_original_source(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        original_spec = source('AA')
        state = upload(client, pid, 1, [('a.csv', original_spec)],
                       [('good.dat', b'AA\n'), ('mixed.dat', b'AA\nZZ\n')])
        sid = state['batch']['specs'][0]['id']
        good, mixed = [d['id'] for d in state['batch']['dats']]
        assert client.get(endpoint(pid, f'/specs/{sid}/source')).content == original_spec
        assert client.get(endpoint(pid, f'/download/{mixed}?original=true')).content == b'AA\nZZ\n'
        state = approve(client, pid, state, sid, 'AA')
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
            'associations': [{'dat_id': did, 'spec_id': sid, 'layout_ids': ['primary']} for did in (good, mixed)]}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        response = post(client, pid, state, '/process')
        assert response.status_code == 200, response.text
        state = response.json()
        assert state['batch']['summary']['validated'] == 2
        assert state['batch']['summary']['unvalidated'] == 1
        assert client.get(endpoint(pid, f'/records/{good}?offset=0&limit=1')).json()['records'][0]['fields']
        state = post(client, pid, state, '/ai').json()
        state = post(client, pid, state, '/generate').json()
        assert state['batch']['summary']['export_ready'] == 1
        assert state['batch']['summary']['blocked_files'][0]['dat_id'] == mixed
        assert client.get(endpoint(pid, f'/download/{good}')).content == b'AA\n'
        assert client.get(endpoint(pid, f'/download/{mixed}')).status_code == 409
        with zipfile.ZipFile(io.BytesIO(client.get(endpoint(pid, '/archive')).content)) as archive:
            assert archive.read(f'validated-{good}.dat') == b'AA\n'
            assert f'validated-{mixed}.dat' not in archive.namelist()
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['blocked_files'][0]['dat_id'] == mixed
        assert client.get(endpoint(pid, f'/download/{mixed}?original=true')).content == b'AA\nZZ\n'
    finally:
        client.__exit__(None, None, None)


def test_stale_spec_binding_and_project_version_rejected(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('a.csv', source('AA'))], [('a.dat', b'AA\n')])
        sid, did = state['batch']['specs'][0]['id'], state['batch']['dats'][0]['id']
        stale_version = state['project']['version']
        state = approve(client, pid, state, sid, 'AA')
        assert client.post(endpoint(pid, '/files'), data={'version': stale_version},
            files=[('dats', ('late.dat', b'AA\n'))], headers=H).status_code == 409
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
            'associations': [{'dat_id': did, 'spec_id': sid, 'layout_ids': ['primary']}]}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        response = client.post(endpoint(pid, f'/specs/{sid}/approve'),
                               json={'version': state['project']['version']}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        assert post(client, pid, state, '/process').status_code == 409
        assert client.get(endpoint(pid, f'/download/{did}?original=true')).content == b'AA\n'
    finally:
        client.__exit__(None, None, None)


def test_unknown_mandatory_acknowledgment_stays_unvalidated(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('unknown.csv', source('AA', 'UNKNOWN'))], [('a.dat', b'AA\n')])
        sid, did = state['batch']['specs'][0]['id'], state['batch']['dats'][0]['id']
        item = state['batch']['specs'][0]
        options = {**item['options'], 'acknowledge_unknown_mandatory': True}
        response = client.put(endpoint(pid, f'/specs/{sid}'), json={'version': state['project']['version'],
            'fields': item['fields'], 'layouts': item['layouts'], 'options': options}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        response = client.post(endpoint(pid, f'/specs/{sid}/approve'),
                               json={'version': state['project']['version']}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        assert state['batch']['specs'][0]['coverage_limitations']
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
            'associations': [{'dat_id': did, 'spec_id': sid, 'layout_ids': ['primary']}]}, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        response = post(client, pid, state, '/process')
        assert response.status_code == 200, response.text
        state = response.json()
        assert state['batch']['summary']['unvalidated'] == 1
        assert post(client, pid, state, '/generate').json()['batch']['summary']['export_ready'] == 0
    finally:
        client.__exit__(None, None, None)


def test_ai_finding_requires_evidence_bound_human_decision(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        class FindingAI(SyntheticAI):
            def invoke_structured(self, capability, context, response_model, instructions, scope):
                run = super().invoke_structured(capability, context, response_model, instructions, scope)
                run['result']['findings'] = [{'record': context['record'], 'field_id': 'code',
                                               'message': 'Review this field reading'}]
                return run

        monkeypatch.setattr(web, 'provider', FindingAI())
        state = upload(client, pid, 1, [('a.csv', source('AA'))], [('a.dat', b'AA\n')])
        sid, did = state['batch']['specs'][0]['id'], state['batch']['dats'][0]['id']
        state = approve(client, pid, state, sid)
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
            'associations': [{'dat_id': did, 'spec_id': sid, 'layout_ids': ['primary']}]}, headers=H)
        state = response.json()
        state = post(client, pid, state, '/process').json()
        state = post(client, pid, state, '/ai').json()
        record = client.get(endpoint(pid, f'/records/{did}?offset=0&limit=1')).json()['records'][0]
        assert record['ai_status'] == 'needs_review'
        state = post(client, pid, state, '/generate').json()
        assert state['batch']['summary']['export_ready'] == 0
        decision = {'version': state['project']['version'], 'evidence_id': '0' * 64,
                    'decision': 'dismiss', 'reason': 'False positive after source review'}
        assert client.post(endpoint(pid, f'/records/{did}/1/ai-decision'), json=decision, headers=H).status_code == 409
        decision['evidence_id'] = record['ai_review']['evidence_id']
        response = client.post(endpoint(pid, f'/records/{did}/1/ai-decision'), json=decision, headers=H)
        assert response.status_code == 200, response.text
        state = response.json()
        assert post(client, pid, state, '/generate').status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_project_byte_and_record_bounds(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('a.csv', source('AA'))], [('big.dat', b'A' * 12_000_000)])
        rejected = client.post(endpoint(pid, '/files'), data={'version': state['project']['version']},
            files=[('dats', ('too-large-project.dat', b'B' * 4_100_000))], headers=H)
        assert rejected.status_code == 413
        assert len(client.get(endpoint(pid)).json()['batch']['dats']) == 1
    finally:
        client.__exit__(None, None, None)


def test_certified_batch_repair_exports_byte_provenance(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('repair.csv', repair_source())], [('shift.dat', b'ABXYZ\n')])
        sid, did = state['batch']['specs'][0]['id'], state['batch']['dats'][0]['id']
        state = approve(client, pid, state, sid)
        response = client.put(endpoint(pid, '/associations'), json={'version': state['project']['version'],
            'associations': [{'dat_id': did, 'spec_id': sid, 'layout_ids': ['primary']}]}, headers=H)
        state = response.json()
        state = post(client, pid, state, '/process').json()
        assert state['batch']['summary']['validated'] == 1
        record = client.get(endpoint(pid, f'/records/{did}?offset=0&limit=1')).json()['records'][0]
        assert record['correction']['inserted_padding_bytes'] == 3
        ledger = client.get(endpoint(pid, f'/provenance/{did}/1')).json()
        assert ledger['source_bytes_accounted_for'] == 5
        assert [entry['byte'] for entry in ledger['source']] == list(b'ABXYZ')
        state = post(client, pid, state, '/ai').json()
        state = post(client, pid, state, '/generate').json()
        assert state['batch']['summary']['export_ready'] == 1
        assert client.get(endpoint(pid, f'/download/{did}')).content == b'AB   XYZ\n'
        assert client.get(endpoint(pid, f'/download/{did}?original=true')).content == b'ABXYZ\n'
        with zipfile.ZipFile(io.BytesIO(client.get(endpoint(pid, '/archive')).content)) as archive:
            assert json.loads(archive.read(f'provenance-{did}-1.json')) == ledger
    finally:
        client.__exit__(None, None, None)


def test_batch_source_rule_edit_recompiles_inferred_datatype(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        state = upload(client, pid, 1, [('a.csv', source('12'))])
        item = state['batch']['specs'][0]
        fields = item['fields']
        assert fields[0]['rules']['datatype'] == 'char'
        fields[0]['datatype_conversion'] = 'INTEGER EXTERNAL'
        response = client.put(endpoint(pid, f'/specs/{item["id"]}'), json={'version': state['project']['version'],
            'fields': fields, 'layouts': item['layouts'], 'options': item['options']}, headers=H)
        assert response.status_code == 200, response.text
        updated = response.json()['batch']['specs'][0]
        assert updated['fields'][0]['rules']['datatype'] == 'integer'
        assert not any(i['code'] == 'SOURCE_DATATYPE' for i in updated['issues'])
    finally:
        client.__exit__(None, None, None)


def test_xlsx_specification_upload_keeps_original_bytes(tmp_path, monkeypatch):
    client, pid = setup(tmp_path, monkeypatch)
    try:
        fixture = SyntheticFixtureGenerator(seed=812)
        layout = fixture.generate_layout(4, 40, 'spreadsheet', all_mandatory=True)
        source_bytes = fixture.to_xlsx_spec(layout)
        state = upload(client, pid, 1, [('rules.xlsx', source_bytes)])
        item = state['batch']['specs'][0]
        assert len(item['fields']) == 4
        assert client.get(endpoint(pid, f'/specs/{item["id"]}/source')).content == source_bytes
    finally:
        client.__exit__(None, None, None)
