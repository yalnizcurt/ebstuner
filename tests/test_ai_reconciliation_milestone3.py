"""Tests for Milestone 3 AI Reconciliation and deterministic MockBedrockProvider."""
import os
import pytest
from fastapi.testclient import TestClient

from bedrock import MockBedrockProvider, AmazonBedrockProvider
from ai import configured_provider
from alignment_audit import AlignmentReview, audit, evidence_package, reconcile
from web import app


@pytest.fixture
def mock_bedrock(monkeypatch):
    monkeypatch.setenv('BEDROCK_MOCK', 'true')


def test_mock_bedrock_provider_interface(mock_bedrock):
    provider = configured_provider()
    assert isinstance(provider, MockBedrockProvider)
    status = provider.status()
    assert status['mock_mode'] is True
    assert status['connection_status'] == 'connected'
    assert 'NOT live AWS' in status['verification_note']


def test_mock_bedrock_structured_alignment_audit(mock_bedrock):
    provider = configured_provider()
    evidence = {
        'record': 1,
        'status': 'certified',
        'exhaustive': True,
        'candidates': [
            {'id': 'path-1', 'certified': True, 'fields': [{'id': 'f1', 'hard_valid': True}]},
            {'id': 'path-2', 'certified': False, 'fields': [{'id': 'f1', 'hard_valid': True}]},
        ],
        'signatures': [{'id': 'f1', 'start': 1, 'end': 4}],
        'initial': [{'field_id': 'f1', 'valid': True}],
        'ledger': [{'offset': 0, 'byte': 65, 'classification': 'VALUE'}],
        'ambiguity_regions': [],
        'spec_conflicts': [],
        'provenance': {'source_bytes_accounted_for': 1},
    }
    run, reconciled = audit(provider, evidence, scope='test/project/1')
    assert run['provider'] == 'MOCK_BEDROCK'
    assert reconciled['ai_status'] == 'agree'
    assert reconciled['preferred_candidate_ids'] == ['path-1']
    assert reconciled['rejected_preferences'] == []
    assert reconciled['certified_candidate_ids'] == ['path-1']


def test_mock_bedrock_audit_with_spec_conflicts(mock_bedrock):
    provider = configured_provider()
    evidence = {
        'record': 1,
        'status': 'needs_review',
        'structural_status': 'SPEC_CONFLICT',
        'exhaustive': True,
        'candidates': [
            {'id': 'path-1', 'certified': False, 'fields': [{'id': 'f1', 'hard_valid': False}]},
        ],
        'signatures': [{'id': 'f1', 'start': 1, 'end': 6}],
        'initial': [{'field_id': 'f1', 'valid': False}],
        'ledger': [{'offset': 0, 'byte': 65, 'classification': 'VALUE'}],
        'ambiguity_regions': [],
        'spec_conflicts': [{'field_id': 'f1', 'code': 'TYPE_MISMATCH', 'message': 'Expected integer'}],
        'provenance': {'source_bytes_accounted_for': 1},
    }
    run, reconciled = audit(provider, evidence, scope='test/project/1')
    assert reconciled['ai_status'] == 'disagree'
    assert len(reconciled['spec_changes_for_review']) == 1
    assert reconciled['spec_changes_for_review'][0]['field_id'] == 'f1'
    assert reconciled['spec_changes_for_review'][0]['attribute'] == 'datatype'
    assert reconciled['human_review_required'] is True
    assert reconciled['rejected_preferences'] == ['path-1']


def test_end_to_end_studio_workflow_with_mock_bedrock(mock_bedrock):
    with TestClient(app) as client:
        # 1. Establish session
        session_res = client.post('/api/session', json={'access_token': ''}, headers={'X-Studio-Request': '1'})
        assert session_res.status_code == 200

        # 2. Create project
        proj_res = client.post('/api/projects', json={'name': 'Milestone3 Test'}, headers={'X-Studio-Request': '1'})
        assert proj_res.status_code == 201
        pid = proj_res.json()['project']['id']

        # 3. Upload spec and DAT
        spec_csv = (
            b"Field Name,Start Pos,End Pos,Length,Data Type / Oracle Conversion Function,Padding / Alignment Rule,Mapping Type,Mandatory\n"
            b"RECORD_TYPE,1,2,2,CHAR,RIGHT ALIGNED,PHYSICAL,YES\n"
            b"AMOUNT,3,6,4,NUMBER,LEFT ALIGNED,PHYSICAL,YES\n"
        )
        dat_bytes = b"011234\n"
        files = {
            'spec': ('spec.csv', spec_csv, 'text/csv'),
            'dat': ('test.dat', dat_bytes, 'application/octet-stream'),
        }
        data = {'version': '1', 'encoding': 'ascii'}
        up_res = client.post(f'/api/projects/{pid}/studio/files', files=files, data=data, headers={'X-Studio-Request': '1'})
        assert up_res.status_code == 200
        pstate = up_res.json()
        version = pstate['project']['version']

        # 4. Trigger spec AI analysis (uses MockBedrockProvider)
        ana_res = client.post(f'/api/projects/{pid}/studio/analyze-spec', json={'version': version}, headers={'X-Studio-Request': '1'})
        assert ana_res.status_code == 200
        # Wait for durable background worker to finish spec job
        import time
        for _ in range(50):
            st = client.get(f'/api/projects/{pid}/studio').json()
            if st['spec'].get('ai_complete'):
                break
            time.sleep(0.05)
        st = client.get(f'/api/projects/{pid}/studio').json()
        assert st['spec']['ai_complete'] is True
        version = st['project']['version']

        # 5. Approve spec
        appr_res = client.post(f'/api/projects/{pid}/studio/approve', json={'version': version}, headers={'X-Studio-Request': '1'})
        assert appr_res.status_code == 200
        st = appr_res.json()
        assert st['spec']['approved'] is True
        version = st['project']['version']

        # 6. Trigger DAT AI analysis
        dat_ai_res = client.post(f'/api/projects/{pid}/studio/analyze-dat', json={'version': version}, headers={'X-Studio-Request': '1'})
        assert dat_ai_res.status_code == 200
        for _ in range(50):
            st = client.get(f'/api/projects/{pid}/studio').json()
            if (st.get('ai_review') or {}).get('complete'):
                break
            time.sleep(0.05)
        st = client.get(f'/api/projects/{pid}/studio').json()
        assert (st.get('ai_review') or {}).get('complete') is True
        version = st['project']['version']

        # 7. Generate and validate
        gen_res = client.post(f'/api/projects/{pid}/studio/generate', json={'version': version}, headers={'X-Studio-Request': '1'})
        assert gen_res.status_code == 200
        assert gen_res.json()['generated'] is not None

        # 8. Download validated DAT
        dl_res = client.get(f'/api/projects/{pid}/studio/download')
        assert dl_res.status_code == 200
        assert dl_res.content == b"011234\n"
