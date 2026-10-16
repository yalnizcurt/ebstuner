import hashlib
import io
import json
from pathlib import Path
import zipfile
import pytest
from fastapi.testclient import TestClient
import web
from bedrock import AmazonBedrockProvider
from compiler import compile_ctl
from engine import field_value, inspect, physical_lines, round_trip
from projects import Repository, ai_context

ROOT = Path(__file__).parents[1] / 'fixtures' / 'oracle'
MANIFEST = json.loads((ROOT / 'manifest.json').read_text())
SAMPLES = MANIFEST['samples']
H = {'X-Studio-Request': '1'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'repository', Repository(tmp_path / 'samples.sqlite3'))
    monkeypatch.setattr(web, 'provider', AmazonBedrockProvider())
    for key in ('STUDIO_ACCESS_TOKEN', 'STUDIO_ENV', 'STUDIO_ALLOW_DRAFT_EXPORT'):
        monkeypatch.delenv(key, raising=False)
    web.rates.clear()
    with TestClient(web.app) as client:
        assert client.post('/api/session', json={}, headers=H).status_code == 200
        yield client


def empty_project(client):
    response = client.post('/api/projects', json={'name': 'Synthetic sample exercise'}, headers=H)
    assert response.status_code == 201
    return response.json()['project']


def load(client, project, sample_id, variant='correct'):
    return client.post(f'/api/projects/{project["id"]}/sample', headers=H,
                       json={'version': project['version'], 'sample_id': sample_id, 'variant': variant})


@pytest.mark.parametrize('sample', SAMPLES, ids=lambda sample: sample['id'])
def test_sample_manifest_checksums_lengths_findings_and_round_trip(sample):
    folder = ROOT / sample['id']; ctl = (folder / 'layout.ctl').read_text()
    assert json.loads((folder / 'expected.json').read_text()) == sample
    assert 1 <= sample['record_length'] <= 4800
    assert sample['scope'].startswith('Synthetic custom') and sample['sources']
    for variant in sample['variants']:
        raw = (folder / variant['file']).read_bytes()
        lines = physical_lines(ctl, raw)
        assert hashlib.sha256(raw).hexdigest() == variant['sha256']
        assert [len(row) for row, ending in lines] == variant['record_lengths']
        assert len(lines) == sample['record_count'] and max(len(row) for row, ending in lines) <= 4800
        generated, diagnosis = round_trip(ctl, raw)
        assert generated == raw and diagnosis['round_trip_identical']
        assert (diagnosis['export_status'] == 'structurally_validated') == variant['expected_valid']
        assert diagnosis['records_with_findings'] == variant['expected_records_with_findings']


def test_fixed_4800_records_have_no_line_separators_and_binary_is_unsupported():
    folder = ROOT / 'fixed_4800'; raw = (folder / 'correct.dat').read_bytes(); ctl = (folder / 'layout.ctl').read_text()
    assert len(raw) == 9600 and b'\r' not in raw and b'\n' not in raw
    assert physical_lines(ctl, raw) == [(raw[:4800], b''), (raw[4800:], b'')]
    folder = ROOT / 'binary_integer4'
    diagnosis = inspect((folder / 'layout.ctl').read_text(), (folder / 'correct.dat').read_bytes())
    assert diagnosis['export_status'] == 'not_fully_verifiable'
    assert 'binary input is unsupported' in ' '.join(diagnosis['records'][0]['issues'])


def test_record_type_uses_declared_marker_width_including_unknown_markers():
    folder = ROOT / 'multi_record_960'; ctl = (folder / 'layout.ctl').read_text()
    correct = inspect(ctl, (folder / 'correct.dat').read_bytes())['records']
    assert [record['record_type'] for record in correct] == ['01', '06', '04', '06', '09']
    damaged = inspect(ctl, (folder / 'bad_marker.dat').read_bytes())['records'][1]
    assert damaged['record_type'] == 'ZZ' and damaged['branches'] == []
    root = ROOT.parents[1]
    old = inspect((root / 'demo_lockbox.ctl').read_text(), (root / 'valid.dat').read_bytes())['records']
    assert [record['record_type'] for record in old] == ['1', '5', '6', '4', '7', '9']
    ambiguous = "LOAD DATA INTO TABLE XX_A (RT POSITION(1:1) CHAR) INTO TABLE XX_B (RT POSITION(1:2) CHAR)"
    assert inspect(ambiguous, b'AB')['records'][0]['record_type'] == 'A'


def test_float_external_scientific_notation_and_invalid_exponent():
    source = 'LOAD DATA INTO TABLE XX_RATE (RATE POSITION(1:12) FLOAT EXTERNAL(12))'
    assert compile_ctl(source).compatibility == 'fully_supported'
    for value in ('1.25E+03', '-.25e-03', '+12.5', '1E0'):
        assert inspect(source, value.encode().ljust(12))['export_status'] == 'structurally_validated'
    for value in ('1.25E+XX', '1E', 'NaN', 'Infinity', '1.2.3'):
        assert inspect(source, value.encode().ljust(12))['records'][0]['findings'][0]['code'] == 'INVALID_FORMAT'


def test_defaultif_blank_requires_preserved_blanks_and_nullif_wins():
    for dtype in ('INTEGER EXTERNAL', 'DECIMAL EXTERNAL', 'FLOAT EXTERNAL', 'ZONED EXTERNAL'):
        source = f'LOAD DATA PRESERVE BLANKS INTO TABLE XX_N (N POSITION(1:4) {dtype} DEFAULTIF N=BLANKS)'
        branch = compile_ctl(source).branches[0]
        assert field_value(branch.fields[0], b'    ', branch) == '0'
        trimmed = compile_ctl(source.replace('PRESERVE BLANKS ', '')).branches[0]
        assert field_value(trimmed.fields[0], b'    ', trimmed) is None
        both = compile_ctl(source.replace('DEFAULTIF', 'NULLIF N=BLANKS DEFAULTIF')).branches[0]
        assert field_value(both.fields[0], b'    ', both) is None


def test_authenticated_catalog_and_download_bundle_match_fixture_bytes(client):
    assert client.get('/api/samples').json() == MANIFEST
    response = client.get('/api/samples/receipt_4800/download')
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    item = next(sample for sample in SAMPLES if sample['id'] == 'receipt_4800')
    names = ['layout.ctl', 'reference.csv', 'expected.json', *[variant['file'] for variant in item['variants']]]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {item['id'] + '/' + name for name in names}
        for name in names:
            assert archive.read(item['id'] + '/' + name) == (ROOT / item['id'] / name).read_bytes()
    assert client.get('/api/samples/no-such-sample/download').status_code == 404
    with TestClient(web.app) as anonymous:
        assert anonymous.get('/api/samples').status_code == 401
        assert anonymous.get('/api/samples/receipt_4800/download').status_code == 401


@pytest.mark.parametrize('sample', [sample for sample in SAMPLES if sample['supported']], ids=lambda sample: sample['id'])
def test_supported_sample_import_generate_download_unchanged(client, sample):
    project = empty_project(client); response = load(client, project, sample['id'])
    assert response.status_code == 200, response.text
    state = response.json(); pid = project['id']
    assert state['sample']['id'] == sample['id'] and state['sample']['variant'] == 'correct'
    assert state['sample']['keys'] == sample['keys'] and not state['modified']
    assert state['diagnosis']['records_with_findings'] == 0
    assert client.post(f'/api/projects/{pid}/generate', headers=H, json={'version': state['project']['version']}).status_code == 200
    expected = (ROOT / sample['id'] / 'correct.dat').read_bytes()
    assert client.get(f'/api/projects/{pid}/download').content == expected
    assert client.get(f'/api/projects/{pid}/download?original=true').content == expected


@pytest.mark.parametrize('sample_id,variant', [('char2_int4', 'bad_numeric'), ('relative_240', 'bad_exponent'),
                                            ('nullable_480', 'bad_numeric'), ('binary_integer4', 'correct'),
                                            ('sql_expression', 'correct'), ('delimited_csv', 'correct')])
def test_damaged_or_unsupported_samples_block_validated_export(client, sample_id, variant):
    project = empty_project(client); response = load(client, project, sample_id, variant)
    assert response.status_code == 200, response.text
    state = response.json(); pid = project['id']
    assert state['diagnosis']['export_status'] != 'structurally_validated'
    assert client.post(f'/api/projects/{pid}/generate', headers=H, json={'version': state['project']['version']}).status_code == 409
    assert client.get(f'/api/projects/{pid}/download').status_code == 409


def test_4800_padding_requires_approval_and_downloads_exact_correct_bytes(client):
    project = empty_project(client); response = load(client, project, 'receipt_4800', 'missing_spaces')
    assert response.status_code == 200, response.text
    state = response.json(); pid = project['id']
    assert state['record_num'] == 2 and state['record']['length'] == 4786 and not state['modified']
    assert client.post(f'/api/projects/{pid}/generate', headers=H, json={'version': state['project']['version']}).status_code == 409
    proposal = next(item for item in state['proposals'] if item['record'] == 2 and item['kind'] == 'trailing_padding')
    assert proposal['requires_approval'] and not proposal['auto_repair_eligible'] and proposal['insert_spaces'] == 14
    response = client.post(f'/api/projects/{pid}/repairs/review', headers=H,
                           json={'version': state['project']['version'], 'ids': [proposal['id']], 'action': 'approve'})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['modified'] and state['diagnosis']['records_with_findings'] == 0
    assert client.post(f'/api/projects/{pid}/generate', headers=H, json={'version': state['project']['version']}).status_code == 200
    assert client.get(f'/api/projects/{pid}/download').content == (ROOT / 'receipt_4800' / 'correct.dat').read_bytes()
    assert client.get(f'/api/projects/{pid}/download?original=true').content == (ROOT / 'receipt_4800' / 'missing_spaces.dat').read_bytes()


def test_sample_import_enforces_owner_version_empty_project_and_known_ids(client):
    project = empty_project(client); pid = project['id']
    assert load(client, {**project, 'version': 2}, 'char2_int4').status_code == 409
    assert load(client, project, '../receipt_4800').status_code == 404
    assert load(client, project, 'char2_int4', '../correct').status_code == 404
    with TestClient(web.app) as other:
        assert other.post('/api/session', json={}, headers=H).status_code == 200
        assert load(other, project, 'char2_int4').status_code == 404
    state = load(client, project, 'char2_int4').json()
    assert load(client, state['project'], 'mixed_80').status_code == 409
    assert client.get(f'/api/projects/{pid}/download?original=true').content == (ROOT / 'char2_int4' / 'correct.dat').read_bytes()


def test_masked_groq_context_excludes_dat_values_and_spreadsheet_evidence(client):
    project = empty_project(client); state = load(client, project, 'char2_int4', 'bad_numeric').json()
    session = client.cookies.get('studio_session'); owner = web.repository.session(session)
    stored = web.repository.load(owner, project['id'])
    context = ai_context(stored, 'investigate', 2, False)
    assert context['values_shared_by_user'] is False and context['evidence'] == []
    assert all(field['value'] == '[MASKED]' for field in context['fields'])
    assert '-X12' not in json.dumps(context)


@pytest.mark.parametrize('sample_id', ['receipt_2400', 'receipt_4800'])
def test_internal_space_damage_preserves_every_nonspace_byte(sample_id):
    original = (ROOT / sample_id / 'correct.dat').read_bytes()
    damaged = (ROOT / sample_id / 'shifted_field.dat').read_bytes()
    assert len(damaged) == len(original) - 1
    assert damaged.replace(b' ', b'') == original.replace(b' ', b'')
