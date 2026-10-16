from pathlib import Path
import json
import pytest
from fastapi.testclient import TestClient
import web
from bedrock import AmazonBedrockProvider
from projects import Repository

ROOT=Path(__file__).parents[1]
H={'X-Studio-Request':'1'}

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'repository',Repository(tmp_path/'projects.sqlite3'))
    monkeypatch.setattr(web,'provider',AmazonBedrockProvider())
    for key in ['STUDIO_ACCESS_TOKEN','STUDIO_ENV','STUDIO_ALLOW_DRAFT_EXPORT','AWS_BEARER_TOKEN_BEDROCK','BEDROCK_TOKEN_FILE','BEDROCK_MODEL_ID','AWS_REGION']:
        monkeypatch.delenv(key,raising=False)
    web.rates.clear()
    with TestClient(web.app) as c:
        assert c.post('/api/session',json={},headers=H).status_code==200
        yield c


def project(client,bad=True,supplement=False,ctl=None,dat=None):
    r=client.post('/api/projects',json={'name':'Synthetic review'},headers=H);assert r.status_code==201
    p=r.json()['project'];pid=p['id']
    files={'ctl':('demo.ctl',ctl or (ROOT/'demo_lockbox.ctl').read_bytes()),'dat':('input.dat',dat if dat is not None else (ROOT/('truncated.dat' if bad else 'valid.dat')).read_bytes())}
    if supplement:files['supplement']=('ref.csv',(ROOT/'reference_values.csv').read_bytes())
    r=client.post(f'/api/projects/{pid}/files',files=files,data={'version':p['version']},headers=H)
    assert r.status_code==200,r.text
    return pid,r.json()


def post(client,pid,path,body=None):return client.post(f'/api/projects/{pid}'+path,json=body,headers=H)


def test_full_damaged_to_approved_validated_download(client):
    pid,s=project(client)
    assert s['diagnosis']['record_count']==6
    candidate=next(p for p in s['proposals'] if p['record']==3 and p['kind']=='trailing_padding')
    assert post(client,pid,'/generate',{'version':s['project']['version']}).status_code==409
    r=post(client,pid,'/repairs/review',{'version':s['project']['version'],'ids':[candidate['id']],'action':'approve'})
    assert r.status_code==200,r.text
    s=r.json();assert s['diagnosis']['records_with_findings']==0
    assert next(p for p in s['proposals'] if p['id']==candidate['id'])['status']=='APPLIED'
    generated=post(client,pid,'/generate',{'version':s['project']['version']})
    assert generated.status_code==200,generated.text
    assert generated.json()['validation']['round_trip_identical']
    download=client.get(f'/api/projects/{pid}/download')
    assert download.content==(ROOT/'valid.dat').read_bytes()
    assert download.headers['x-validation-status']=='structurally_validated'
    assert client.get(f'/api/projects/{pid}/download?original=true').content==(ROOT/'truncated.dat').read_bytes()
    assert client.get(f'/api/projects/{pid}/report').json()['serialized_sha256']==download.headers['x-checksum-sha256']
    audit=client.get(f'/api/projects/{pid}/audit').json()
    assert any(x['action']=='proposal approved' for x in audit['events'])
    assert any(x['action']=='DAT generated and re-parsed' for x in audit['events'])


def test_unchanged_original_and_generated_are_identical(client):
    pid,s=project(client,bad=False)
    r=post(client,pid,'/generate',{'version':s['project']['version']})
    assert r.status_code==200
    assert client.get(f'/api/projects/{pid}/download').content==(ROOT/'valid.dat').read_bytes()


def test_manual_cell_edit_concurrency_undo_redo_and_original(client):
    pid,s=project(client,bad=False);v=s['project']['version']
    body={'version':v,'edits':[{'record':3,'field':'CUSTOMER_NUMBER','value':'0002060'}]}
    r=client.patch(f'/api/projects/{pid}/records',json=body,headers=H);assert r.status_code==200
    edited=r.json();assert edited['modified']
    assert client.patch(f'/api/projects/{pid}/records',json=body,headers=H).status_code==409
    source=client.get(f'/api/projects/{pid}/download?original=true').content
    assert source==(ROOT/'valid.dat').read_bytes()
    s=post(client,pid,'/history/undo',{'version':edited['project']['version']}).json();assert not s['modified']
    s=post(client,pid,'/history/redo',{'version':s['project']['version']}).json();assert s['modified']
    assert source==client.get(f'/api/projects/{pid}/download?original=true').content


def test_atomic_bulk_overflow_does_not_save_first_edit(client):
    pid,s=project(client,bad=False)
    r=client.patch(f'/api/projects/{pid}/records',json={'version':s['project']['version'],'edits':[{'record':3,'field':'BATCH_NAME','value':'777'},{'record':3,'field':'ITEM_NUMBER','value':'TOO-LONG'}]},headers=H)
    assert r.status_code==422
    now=client.get(f'/api/projects/{pid}/state').json()
    assert not now['modified'] and now['project']['version']==s['project']['version']


def test_stale_proposal_after_edit_is_superseded(client):
    pid,s=project(client);prop=s['proposals'][0]
    r=client.patch(f'/api/projects/{pid}/records',json={'version':s['project']['version'],'edits':[{'record':3,'field':'BATCH_NAME','value':'777'}]},headers=H)
    assert r.status_code==200
    s=r.json();assert next(p for p in s['proposals'] if p['id']==prop['id'])['status']=='SUPERSEDED'
    assert post(client,pid,'/repairs/review',{'version':s['project']['version'],'ids':[prop['id']],'action':'approve'}).status_code==409


def test_project_access_boundaries_and_session_cookie(client):
    pid,s=project(client)
    cookie=client.cookies.get('studio_session');assert cookie
    with TestClient(web.app) as stranger:
        assert stranger.get(f'/api/projects/{pid}/state').status_code==401
        assert stranger.post('/api/session',json={},headers=H).status_code==200
        assert stranger.get(f'/api/projects/{pid}/state').status_code==404
        assert stranger.get('/api/projects').json()==[]
    cookie_header=client.post('/api/session',json={},headers=H)
    assert client.get('/').headers['content-security-policy']


def test_csrf_origin_size_limits_and_safe_html(client,monkeypatch):
    assert client.post('/api/projects',json={'name':'x'}).status_code==403
    assert client.post('/api/projects',json={'name':'x'},headers={**H,'Origin':'https://evil.example'}).status_code==403
    monkeypatch.setattr(web,'MAX_BODY',100)
    assert client.post('/api/projects',json={'name':'x'*500},headers=H).status_code==413
    home=client.get('/');assert home.status_code==200 and 'script src="/static/app.js"' in home.text
    assert client.get('/static/app.js').status_code==200


def test_spreadsheet_conflicts_never_auto_overwrite(client):
    ctl=b'LOAD DATA INTO TABLE T (ID POSITION(1:4) CHAR, AMOUNT POSITION(5:10) DECIMAL EXTERNAL)'
    p=client.post('/api/projects',json={'name':'Sheet conflict'},headers=H).json()['project'];pid=p['id']
    r=client.post(f'/api/projects/{pid}/files',headers=H,data={'version':1},files={'ctl':('s.ctl',ctl),'dat':('s.dat',b'000110.00 '),'supplement':('s.csv',b'ID,AMOUNT\n0001,12.30\n')})
    assert r.status_code==200
    s=r.json();mapped=post(client,pid,'/map-spreadsheet',{'version':s['project']['version'],'mapping':{'ID':'ID','AMOUNT':'AMOUNT'},'keys':['ID'],'sheet':'CSV'})
    assert mapped.status_code==200,mapped.text
    s=mapped.json();assert len(s['reconciliation']['conflicts'])==1 and not s['modified']
    prop=s['proposals'][-1]
    r=post(client,pid,'/repairs/review',{'version':s['project']['version'],'ids':[prop['id']],'action':'approve','overrides':{prop['id']:'11.25'}})
    assert r.status_code==200 and r.json()['modified']
    assert r.json()['record']['fields'][1]['raw']=='11.25 '


def test_no_evidence_no_ai_call_and_not_configured_status(client,monkeypatch):
    pid,s=project(client)
    def unexpected(*args):pytest.fail('must not call Bedrock')
    monkeypatch.setattr(web.provider,'invoke',unexpected)
    r=post(client,pid,'/ai/recover',{'version':s['project']['version'],'record':3,'capability':'recover'})
    assert r.status_code==200 and r.json()['skipped'] and 'INSUFFICIENT_EVIDENCE' in r.json()['reason']
    status=client.get('/api/ai/bedrock/status').json();assert not status['ai_available']
    assert 'token' not in status and 'api_key' not in status


def test_pagination_and_grid_do_not_return_entire_dataset(client):
    ctl=b'LOAD DATA INTO TABLE T (ID POSITION(1:4) CHAR)'
    pid,s=project(client,ctl=ctl,dat=b'0001\n'*155)
    assert s['diagnosis']['record_count']==155 and len(s['diagnosis']['records'])==50
    assert client.get(f'/api/projects/{pid}/state?page=4&page_size=50').json()['pagination']['total']==155
    grid=client.get(f'/api/projects/{pid}/records?page=2&page_size=25').json()
    assert len(grid['records'])==25 and grid['records'][0]['record']==26


def test_unsupported_blocks_generation_and_draft_is_explicit(client,monkeypatch):
    pid,s=project(client,ctl=b'LOAD DATA FIELDS TERMINATED BY "," INTO TABLE T (ID POSITION(1:4) CHAR)',dat=b'0001')
    assert s['diagnosis']['export_status']=='not_fully_verifiable'
    assert post(client,pid,'/generate',{'version':s['project']['version'],'draft':True}).status_code==409
    monkeypatch.setenv('STUDIO_ALLOW_DRAFT_EXPORT','true')
    r=post(client,pid,'/generate',{'version':s['project']['version'],'draft':True});assert r.status_code==200
    d=client.get(f'/api/projects/{pid}/download');assert d.status_code==200 and 'UNVALIDATED-DRAFT' in d.headers['content-disposition']


def test_reupload_cannot_destroy_original_source(client):
    pid,s=project(client)
    r=client.post(f'/api/projects/{pid}/files',headers=H,data={'version':s['project']['version']},files={'ctl':('x.ctl',b'bad'),'dat':('x.dat',b'bad')})
    assert r.status_code==409


def test_persistence_survives_repository_reinitialization(client,monkeypatch):
    pid,s=project(client,bad=False);path=web.repository.path
    monkeypatch.setattr(web,'repository',Repository(path))
    assert client.get(f'/api/projects/{pid}/state').json()['original_checksum']==s['original_checksum']


def test_disabled_bedrock_does_not_affect_deterministic_validation(client,monkeypatch):
    monkeypatch.setenv('BEDROCK_ENABLED','false');pid,s=project(client,bad=False)
    assert client.get('/api/ai/bedrock/status').json()['connection_status']=='disabled'
    assert post(client,pid,'/validate').json()['export_status']=='structurally_validated'


def test_optional_login_token_is_server_only(client,monkeypatch):
    monkeypatch.setenv('STUDIO_ACCESS_TOKEN','synthetic-workspace-access-token')
    client.cookies.clear()
    assert client.post('/api/session',json={},headers=H).status_code==401
    r=client.post('/api/session',json={'access_token':'synthetic-workspace-access-token'},headers=H)
    assert r.status_code==200 and 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert 'synthetic-workspace-access-token' not in r.text


def test_unmatched_record_can_be_explicitly_edited_using_assumed_layout(client):
    ctl=b"LOAD DATA INTO TABLE T WHEN RT='1' (RT POSITION(1:1) CHAR, ID POSITION(2:5) CHAR)"
    pid,s=project(client,ctl=ctl,dat=b'X0001')
    assert not s['record']['branches'] and not s['record']['fields']
    grid=client.get(f'/api/projects/{pid}/records?layout_branch=0').json()
    assert grid['records'][0]['fields'][0]['hypothetical_layout'] is True
    r=client.patch(f'/api/projects/{pid}/records',headers=H,json={'version':s['project']['version'],'edits':[{'record':1,'field':'RT','value':'1','branch':0}]})
    assert r.status_code==200 and r.json()['record']['status']=='valid'


def test_semantic_sheet_conflict_is_warning_without_rewriting(client):
    ctl=b'LOAD DATA INTO TABLE T (ID POSITION(1:4) CHAR, AMOUNT POSITION(5:10) DECIMAL EXTERNAL)'
    p=client.post('/api/projects',json={'name':'Semantic diagnostic'},headers=H).json()['project'];pid=p['id']
    r=client.post(f'/api/projects/{pid}/files',headers=H,data={'version':1},files={'ctl':('s.ctl',ctl),'dat':('s.dat',b'000110.00 '),'supplement':('s.csv',b'ID,AMOUNT\n0001,12.30\n')})
    s=post(client,pid,'/map-spreadsheet',{'version':r.json()['project']['version'],'mapping':{'ID':'ID','AMOUNT':'AMOUNT'},'keys':['ID'],'sheet':'CSV'}).json()
    assert s['diagnosis']['counts']['semantic_inconsistency']==1
    assert s['diagnosis']['export_status']=='validated_with_warnings'
    assert not s['modified']
    r=post(client,pid,'/generate',{'version':s['project']['version']});assert r.status_code==200
    assert r.json()['validation']['export_status']=='validated_with_warnings'
    assert client.get(f'/api/projects/{pid}/download').content==b'000110.00 '


def test_reanalysis_supersedes_old_candidates_and_orders_current_ranks(client):
    pid,s=project(client)
    owner=web.repository.session(client.cookies.get('studio_session'));p=web.repository.load(owner,pid)
    obsolete={**p['proposals'][0],'id':'obsolete-search-result','rank':999,'status':'PENDING_REVIEW'}
    p['proposals'].append(obsolete);web.repository.save(owner,p,p['version'])
    s=client.get(f'/api/projects/{pid}/state').json()
    r=post(client,pid,'/analyze',{'version':s['project']['version'],'records':[3]});assert r.status_code==200
    queue=client.get(f'/api/projects/{pid}/repair-proposals?pending_only=true').json()
    assert queue['total']<=5 and not any(x['id']=='obsolete-search-result' for x in queue['proposals'])
    assert queue['proposals'][0]['rank']==1 and queue['proposals'][0]['kind']=='trailing_padding'
    all_props=client.get(f'/api/projects/{pid}/repair-proposals').json()['proposals']
    assert next(x for x in all_props if x['id']=='obsolete-search-result')['status']=='SUPERSEDED'
