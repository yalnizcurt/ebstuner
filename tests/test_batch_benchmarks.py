"""Measured, generated multi-file batch workload; no external AI calls."""
import time
import tracemalloc

from fastapi.testclient import TestClient

from fixtures.synthetic_generator import SyntheticFixtureGenerator
from projects import Repository
import web


HEADERS = {'X-Studio-Request': '1'}


def _post(client, pid, suffix, state):
    response = client.post(f'/api/projects/{pid}/studio/batch{suffix}',
                           json={'version': state['project']['version']}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_measured_multi_record_batch(tmp_path, monkeypatch):
    """Measure deterministic routing and TASAA for two specs, two DATs, and 2,400 generated records."""
    monkeypatch.setattr(web, 'repository', Repository(tmp_path / 'batch_benchmark.sqlite3'))
    generator = SyntheticFixtureGenerator(seed=901)
    layouts = [generator.generate_layout(4, 40, 'head', 'H'),
               generator.generate_layout(6, 60, 'detail', 'D')]
    head_rows = [generator.generate_record(layouts[0])[0] for _ in range(800)]
    detail_rows = [generator.generate_record(layouts[1])[0] for _ in range(1600)]
    mixed = b''.join(row + b'\n' for pair in zip(head_rows, detail_rows[:800]) for row in pair)
    details = b''.join(row + b'\n' for row in detail_rows[800:])

    with TestClient(web.app) as client:
        assert client.post('/api/session', json={}, headers=HEADERS).status_code == 200
        created = client.post('/api/projects', json={'name': 'Measured multi-record batch'}, headers=HEADERS)
        assert created.status_code == 201
        pid = created.json()['project']['id']
        upload = client.post(f'/api/projects/{pid}/studio/batch/files', headers=HEADERS,
            data={'version': created.json()['project']['version']},
            files=[('specs', ('head.csv', generator.to_csv_spec(layouts[0]), 'text/csv')),
                   ('specs', ('detail.csv', generator.to_csv_spec(layouts[1]), 'text/csv')),
                   ('dats', ('mixed.dat', mixed, 'application/octet-stream')),
                   ('dats', ('detail.dat', details, 'application/octet-stream'))])
        assert upload.status_code == 200, upload.text
        state = upload.json()
        specs_by_name = {item['name']: item for item in state['batch']['specs']}
        dats_by_name = {item['name']: item for item in state['batch']['dats']}
        for filename, marker in (('head.csv', 'H'), ('detail.csv', 'D')):
            item = specs_by_name[filename]
            layouts_draft = item['layouts']
            layouts_draft[0]['when'] = [{'start': 1, 'end': 1, 'equals': marker, 'trim': False}]
            revised = client.put(f'/api/projects/{pid}/studio/batch/specs/{item["id"]}', headers=HEADERS,
                json={'version': state['project']['version'], 'fields': item['fields'],
                      'layouts': layouts_draft, 'options': item['options']})
            assert revised.status_code == 200, revised.text
            state = revised.json()
            state = _post(client, pid, f'/specs/{item["id"]}/approve', state)
        head_id = specs_by_name['head.csv']['id']
        detail_id = specs_by_name['detail.csv']['id']
        mixed_id = dats_by_name['mixed.dat']['id']
        details_id = dats_by_name['detail.dat']['id']
        associations = [{'dat_id': mixed_id, 'spec_id': head_id, 'layout_ids': ['primary']},
                        {'dat_id': mixed_id, 'spec_id': detail_id, 'layout_ids': ['primary']},
                        {'dat_id': details_id, 'spec_id': detail_id, 'layout_ids': ['primary']}]
        associated = client.put(f'/api/projects/{pid}/studio/batch/associations', headers=HEADERS,
                                json={'version': state['project']['version'], 'associations': associations})
        assert associated.status_code == 200, associated.text
        state = associated.json()

        tracemalloc.start()
        start = time.perf_counter()
        state = _post(client, pid, '/process', state)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        summary = state['batch']['summary']
        assert summary['files'] == 2 and summary['records'] == 2400
        assert summary['unvalidated'] == 0 and summary['validated'] == 2400
        assert peak < 120_000_000, f'Peak traced allocation {peak} exceeded the safety bound'
        assert all(item['status'] == 'validated_pending_ai' for item in state['batch']['dats'])
        assert client.get(f'/api/projects/{pid}/studio/batch/download/{mixed_id}?original=true').content == mixed
        assert client.get(f'/api/projects/{pid}/studio/batch/download/{details_id}?original=true').content == details
        print(f'Batch benchmark: 2400 records, {len(mixed)+len(details)} input bytes, '
              f'{elapsed:.3f} s deterministic processing, {peak/1_000_000:.1f} MB peak traced allocation')
