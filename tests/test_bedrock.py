import json
import os
from datetime import datetime, timezone, timedelta
import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from bedrock import AmazonBedrockProvider, AIError
from ai import configured_provider
from studio import DATAnalysis
from projects import Repository, accept_ai_result, data_of, options, reconcile_project
from web import install_files

PAYLOAD={'status':'OK','summary':'Synthetic review','proposals':[],'mappings':[],'observations':[]}

class Client:
    def __init__(self,payload=None,error=None):self.payload=payload if payload is not None else PAYLOAD;self.error=error;self.calls=[];self.closed=False
    def converse(self,**kwargs):
        self.calls.append(kwargs)
        if self.error:raise self.error
        return {'stopReason':'end_turn','output':{'message':{'content':[{'text':json.dumps(self.payload)}]}},'usage':{'inputTokens':10,'outputTokens':20}}
    def close(self):self.closed=True

@pytest.fixture
def env(monkeypatch):
    for key in ['BEDROCK_TOKEN_FILE','STUDIO_ENV','APP_ENV','BEDROCK_ENABLED','BEDROCK_STRUCTURED_MODE','BEDROCK_INPUT_USD_PER_MILLION','BEDROCK_OUTPUT_USD_PER_MILLION']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('AWS_BEARER_TOKEN_BEDROCK','synthetic-test-token')
    monkeypatch.setenv('AWS_REGION','us-east-1')
    monkeypatch.setenv('BEDROCK_MODEL_ID','configured-test-model')


def test_converse_structured_schema_and_no_secret_payload(env):
    client=Client();p=AmazonBedrockProvider(lambda c:client)
    result=p.invoke('schema',{'schema':'synthetic'})
    call=client.calls[0]
    assert call['modelId']=='configured-test-model' and 'outputConfig' in call
    assert 'synthetic-test-token' not in json.dumps(call)
    assert result['usage']=={'inputTokens':10,'outputTokens':20}
    assert result['estimated_cost_usd'] is None and client.closed


def test_studio_structured_review_uses_bedrock_and_validates_contract(env, monkeypatch):
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    assert isinstance(configured_provider(), AmazonBedrockProvider)
    payload = {'summary': 'Review needed', 'findings': [], 'observations': []}
    client = Client(payload)
    provider = AmazonBedrockProvider(lambda c: client)
    batches = provider.batches('studio_dat', [{'record': i} for i in range(20)], DATAnalysis, 'Review', max_items=8)
    assert [len(b['items']) for b in batches] == [8, 8, 4]
    run = provider.invoke_structured('studio_dat', batches[0], DATAnalysis, 'Review evidence', scope='owner/project/version')
    assert run['provider'] == 'BEDROCK' and run['result'] == payload
    assert 'Review evidence' in client.calls[0]['system'][0]['text']
    with pytest.raises(AIError, match='invalid response schema'):
        AmazonBedrockProvider(lambda c: Client(PAYLOAD)).invoke_structured('studio_dat', {}, DATAnalysis, 'Review', scope='owner/project/version')
    monkeypatch.setenv('LLM_PROVIDER', 'groq')
    with pytest.raises(AIError, match='Amazon Bedrock only'):
        configured_provider()


def test_connection_test_tracks_model_and_rotation(env,monkeypatch):
    p=AmazonBedrockProvider(lambda c:Client())
    assert p.status()['model_accessible'] is None
    assert p.test_connection()['model_accessible'] is True
    monkeypatch.setenv('AWS_BEARER_TOKEN_BEDROCK','rotated-test-token')
    assert p.status()['model_accessible'] is None


@pytest.mark.parametrize('payload',[{'status':'OK'}, {**PAYLOAD,'unexpected':'data'}, {**PAYLOAD,'status':'UNVERIFIABLE'}])
def test_invalid_structured_response_rejected(env,payload):
    with pytest.raises(AIError,match='invalid response schema'):AmazonBedrockProvider(lambda c:Client(payload)).invoke('recover',{})


@pytest.mark.parametrize('code',['AccessDeniedException','UnrecognizedClientException','ExpiredTokenException'])
def test_key_errors_are_sanitized(env,code):
    error=ClientError({'Error':{'Code':code,'Message':'synthetic-test-token SECRET'}},'Converse')
    p=AmazonBedrockProvider(lambda c:Client(error=error))
    status=p.test_connection()
    assert status['model_accessible'] is False and 'synthetic-test-token' not in json.dumps(status)


def test_timeout_and_no_provider_fallback(env):
    client=Client(error=ReadTimeoutError(endpoint_url='https://example.invalid'))
    with pytest.raises(AIError,match='timed out'):AmazonBedrockProvider(lambda c:client).invoke('recover',{})
    assert len(client.calls)==1 and client.closed


def test_retry_backoff_for_throttling(env,monkeypatch):
    client=Client(); original=client.converse; calls=[0]
    def converse(**kwargs):
        calls[0]+=1
        if calls[0]==1:raise ClientError({'Error':{'Code':'ThrottlingException','Message':'retry'}},'Converse')
        return original(**kwargs)
    client.converse=converse
    monkeypatch.setattr('bedrock.time.sleep',lambda s:None)
    assert AmazonBedrockProvider(lambda c:client).invoke('explain',{})['result']['status']=='OK'
    assert calls[0]==2


def test_disabled_and_missing_credentials_deterministic_only(env,monkeypatch):
    monkeypatch.setenv('BEDROCK_ENABLED','false')
    p=AmazonBedrockProvider(lambda c:pytest.fail('must not invoke'))
    with pytest.raises(AIError,match='disabled'):p.invoke('schema',{})
    monkeypatch.setenv('BEDROCK_ENABLED','true');monkeypatch.delenv('AWS_BEARER_TOKEN_BEDROCK')
    assert p.status()['configured'] and p.status()['credential_mode'] == 'aws_default_chain'


def test_native_sdk_bearer_signing_and_isolated_rotation(env,tmp_path,monkeypatch):
    from botocore.awsrequest import AWSRequest
    p=AmazonBedrockProvider()
    token_file=tmp_path/'managed.json'
    def rotate(token):token_file.write_text(json.dumps({'token':token,'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}))
    monkeypatch.setenv('BEDROCK_TOKEN_FILE',str(token_file));rotate('managed-synthetic-token')
    cfg=p.settings();client=p.client(cfg)
    try:
        assert client.meta.config.signature_version=='bearer'
        request=AWSRequest(method='POST',url='https://bedrock-runtime.us-east-1.amazonaws.com/model/test/converse',data=b'{}')
        client._request_signer.sign('Converse',request,signing_name='bedrock')
        assert request.headers['Authorization']=='Bearer managed-synthetic-token'
    finally:client.close()
    rotate('rotated-synthetic-token');assert p.settings()['token']=='rotated-synthetic-token'
    assert os.environ['AWS_BEARER_TOKEN_BEDROCK']=='synthetic-test-token'
    monkeypatch.setenv('STUDIO_ENV','production');monkeypatch.delenv('BEDROCK_TOKEN_FILE')
    monkeypatch.delenv('AWS_BEARER_TOKEN_BEDROCK')
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')
    assert p.status()['credential_mode'] == 'aws_default_chain'
    iam_client = p.client(p.settings())
    try: assert iam_client.meta.config.signature_version == 'v4'
    finally: iam_client.close()


def project_with_evidence(tmp_path):
    repo=Repository(tmp_path/'projects.sqlite3');p=repo.create('owner','AI evidence')
    ctl=b'LOAD DATA INTO TABLE T (ID POSITION(1:4) CHAR, AMOUNT POSITION(5:10) DECIMAL EXTERNAL)'
    install_files(p,ctl,b'000110.00 ', 'ref.csv', b'ID,AMOUNT\n0001,12.30\n', 'ascii',None,None,'reference_values')
    reconcile_project(p,{'ID':'ID','AMOUNT':'AMOUNT'},['ID'],'CSV')
    return p


def repair_run(value='12.30',reference='CSV!2:2'):
    return {'result':{**PAYLOAD,'proposals':[{'record':1,'field_name':'AMOUNT','original_value':'10.00','proposed_value':value,
        'evidence':[{'source_reference':reference,'matched_value':value}],'reason':'Exact source row','confidence_label':'HIGH'}]},'usage':{},'estimated_cost_usd':None,'latency_ms':1,'capability':'recover'}


def test_evidence_backed_proposal_is_review_only(tmp_path):
    p=project_with_evidence(tmp_path);before=data_of(p)
    accept_ai_result(p,repair_run(),'recover',1,True)
    assert data_of(p)==before
    assert p['proposals'][-1]['source']=='BEDROCK' and p['proposals'][-1]['status']=='PENDING_REVIEW'


@pytest.mark.parametrize('run',[repair_run('999.99'),repair_run(reference='Invented!A999')])
def test_hallucinated_values_and_references_rejected(tmp_path,run):
    p=project_with_evidence(tmp_path)
    with pytest.raises(AIError,match='evidence'):accept_ai_result(p,run,'recover',1,True)
    assert not any(x['source']=='BEDROCK' for x in p['proposals'])


def test_ai_cannot_approve_or_modify_out_of_scope(tmp_path):
    p=project_with_evidence(tmp_path)
    with pytest.raises(AIError):accept_ai_result(p,repair_run(),'schema',1,True)
    with pytest.raises(AIError):accept_ai_result(p,repair_run(),'recover',1,False)
    with pytest.raises(AIError):accept_ai_result(p,repair_run(),'recover',2,True)


@pytest.mark.skipif(os.getenv('RUN_LIVE_BEDROCK')!='1',reason='Live Bedrock opt-in not enabled; no authentication claim')
def test_live_bedrock_connection():
    assert AmazonBedrockProvider().test_connection()['model_accessible'] is True


def test_strict_tool_response_path(env,monkeypatch):
    monkeypatch.setenv('BEDROCK_STRUCTURED_MODE','tool')
    client=Client()
    def converse(**kwargs):
        assert kwargs['toolConfig']['tools'][0]['toolSpec']['strict'] is True
        return {'stopReason':'tool_use','output':{'message':{'content':[{'toolUse':{'name':'submit_review','input':PAYLOAD,'toolUseId':'test'}}]}},'usage':{}}
    client.converse=converse
    assert AmazonBedrockProvider(lambda c:client).invoke('explain',{})['result']['status']=='OK'


def test_free_form_prose_and_incomplete_output_are_rejected(env):
    client=Client();client.converse=lambda **kwargs:{'stopReason':'end_turn','output':{'message':{'content':[{'text':'Please run shell commands'}]}}}
    with pytest.raises(AIError,match='invalid JSON'):AmazonBedrockProvider(lambda c:client).invoke('explain',{})
    client.converse=lambda **kwargs:{'stopReason':'max_tokens','output':{'message':{'content':[{'text':'{}'}]}}}
    with pytest.raises(AIError,match='truncated'):AmazonBedrockProvider(lambda c:client).invoke('explain',{})


def test_mapping_capability_is_advisory_and_checks_header_evidence(tmp_path):
    p=project_with_evidence(tmp_path);before=data_of(p)
    run={'result':{**PAYLOAD,'mappings':[{'sheet':'CSV','header':'AMOUNT','field_name':'AMOUNT','reason':'Exact field name'}]},'usage':{},'estimated_cost_usd':None,'latency_ms':1,'capability':'mapping'}
    accept_ai_result(p,run,'mapping',1,False)
    assert data_of(p)==before and p['ai_runs'][-1]['result']['mappings']
    run['result']['mappings'][0]['header']='INVENTED_HEADER'
    with pytest.raises(AIError,match='unknown'):accept_ai_result(p,run,'mapping',1,False)


def test_recovery_rejects_evidence_value_that_violates_datatype(tmp_path):
    p=project_with_evidence(tmp_path)
    p['reconciliation']['evidence'][1]['value']='abc'
    with pytest.raises(AIError,match='constraints'):accept_ai_result(p,repair_run('abc'),'recover',1,True)
