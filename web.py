"""FastAPI workstation. Serve locally or behind an authenticated TLS proxy."""
from __future__ import annotations
import hmac
import io
import json
import logging
import os
import secrets
import threading
import time
import zipfile
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from engine import compile_ctl, inspect, check_encoding, physical_lines, edit_record, round_trip
from spreadsheets import import_spreadsheet, default_mapping, compare_field_specifications
from reconstruction import score_candidate, DEFAULT_WEIGHTS
from projects import (Repository, Conflict, now, checksum, encode, decode, options, data_of, lines_of, event,
                      check_version, commit_bytes, undo_redo, analyze, reconcile_project, apply_proposals,
                      record_detail, ai_context, accept_ai_result, invalidate_proposals, verify_generation, supplement_diagnostics)
from bedrock import AIError, CAPABILITIES, AmazonBedrockProvider
from ai import load_environment, configured_provider, production_mode
from models import ProjectState, RecordDetail, RecordPage, ProposalPage, DiagnosisView
from studio import install as install_studio

ROOT = Path(__file__).parent
load_environment(ROOT / '.env')
MAX_FILE = 12_000_000
MAX_BODY = 30_000_000
repository = Repository()
provider = configured_provider()
rate_lock = threading.Lock()
# shortcut: process-local quotas, use shared quota storage before adding API workers.
rates = defaultdict(deque)


def limit(key, count=6, seconds=60):
    with rate_lock:
        stamp = time.monotonic(); queue = rates[key]
        while queue and queue[0] < stamp - seconds: queue.popleft()
        if len(queue) >= count: raise HTTPException(429, 'Request rate limit reached; wait one minute')
        queue.append(stamp)
        if len(rates) > 2000:
            for k in list(rates):
                if not rates[k] or rates[k][-1] < stamp - seconds: del rates[k]


@asynccontextmanager
async def lifespan(app):
    if production_mode():
        if len(os.getenv('STUDIO_ACCESS_TOKEN', '')) < 32: raise RuntimeError('Production requires STUDIO_ACCESS_TOKEN with at least 32 characters')
        if not os.getenv('STUDIO_ALLOWED_HOSTS'): raise RuntimeError('Production requires STUDIO_ALLOWED_HOSTS')
        if not os.getenv('STUDIO_PUBLIC_ORIGIN', '').startswith('https://'): raise RuntimeError('Production requires an HTTPS STUDIO_PUBLIC_ORIGIN behind a TLS proxy')
    for name in ('boto3', 'botocore', 'urllib3'): logging.getLogger(name).setLevel(logging.WARNING)
    with repository.connection() as db: repository.cleanup(db)
    studio_service.start()
    yield
    studio_service.stop()


app = FastAPI(title='CTL–DAT Alignment Studio', version='2.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=(os.getenv('STUDIO_ALLOWED_HOSTS') or '127.0.0.1,localhost,testserver').split(','))
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


@app.middleware('http')
async def boundaries(request: Request, call_next):
    correlation = secrets.token_hex(12)
    if request.method in ('POST', 'PATCH', 'DELETE', 'PUT'):
        if request.headers.get('X-Studio-Request') != '1': return JSONResponse({'detail': 'Missing same-origin request header'}, 403)
        origin = request.headers.get('origin')
        expected = os.getenv('STUDIO_PUBLIC_ORIGIN') or str(request.base_url).rstrip('/')
        if origin and origin.rstrip('/') != expected: return JSONResponse({'detail': 'Cross-origin writes are prohibited'}, 403)
        if request.headers.get('sec-fetch-site') == 'cross-site': return JSONResponse({'detail': 'Cross-site writes are prohibited'}, 403)
        chunks, length = [], 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > MAX_BODY: return JSONResponse({'detail': 'Request exceeds 30 MB'}, 413)
            chunks.append(chunk)
        request._body = b''.join(chunks)
    if request.url.path.startswith('/api/') and request.url.path != '/api/session':
        owner = repository.session(request.cookies.get('studio_session')) if request.cookies.get('studio_session') else None
        if not owner: return JSONResponse({'detail': 'Sign in or reopen the workspace to establish a session'}, 401)
        request.state.owner = owner
    try: response = await call_next(request)
    except Conflict as exc: response = JSONResponse({'detail': str(exc), 'code': 'STALE_VERSION'}, 409)
    except KeyError: response = JSONResponse({'detail': 'Project not found or access denied'}, 404)
    except AIError as exc: response = JSONResponse({'detail': exc.message, 'code': exc.code}, 503 if exc.code in ('NOT_CONFIGURED', 'DISABLED', 'ReadTimeoutError') else 422)
    except ValueError as exc: response = JSONResponse({'detail': str(exc)[:1000], 'code': 'VALIDATION'}, 422)
    except Exception as exc:
        logging.getLogger('studio').error('Request %s failed (%s)', correlation, type(exc).__name__)
        response = JSONResponse({'detail': 'Request failed; refresh to inspect the current project version', 'request_id': correlation}, 500)
    response.headers.update({'X-Request-ID': correlation, 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                             'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY',
                             'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
    if production_mode(): response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse({'detail': '; '.join(str(e['loc']) + ': ' + e['msg'] for e in exc.errors())}, 422)


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Login(Input): access_token: str = Field(default='', max_length=512)

@app.post('/api/session')
def establish(request: Request, response: Response, body: Login):
    limit(('login', request.client.host), 12)
    required = os.getenv('STUDIO_ACCESS_TOKEN')
    if required and not hmac.compare_digest(required, body.access_token): raise HTTPException(401, 'Invalid workspace access token')
    existing = request.cookies.get('studio_session')
    if not existing or not repository.session(existing):
        existing = repository.session()
        response.set_cookie('studio_session', existing, httponly=True, secure=production_mode(), samesite='strict', max_age=7 * 86400, path='/')
    return {'authenticated': True, 'draft_export_enabled': os.getenv('STUDIO_ALLOW_DRAFT_EXPORT', '').lower() == 'true'}

@app.get('/api/session')
def session_status(request: Request):
    return {'authenticated': bool(request.cookies.get('studio_session') and repository.session(request.cookies['studio_session'])),
            'login_required': bool(os.getenv('STUDIO_ACCESS_TOKEN')), 'draft_export_enabled': os.getenv('STUDIO_ALLOW_DRAFT_EXPORT', '').lower() == 'true'}

@app.get('/', response_class=HTMLResponse)
def home(): return (ROOT / 'index.html').read_text()

@app.get('/advanced', response_class=HTMLResponse)
def advanced(): return (ROOT / 'advanced.html').read_text()

@app.get('/health')
def health(): return {'status': 'ok'}

@app.get('/api/openapi.json')
def api_schema(): return app.openapi()


def session_or_404(request, pid): return repository.load(request.state.owner, pid)

@lru_cache(maxsize=4)
def diagnosis(ctl, raw, encoding, fmt, length): return inspect(ctl, raw, encoding, fmt, length)

def diagnose(p): return supplement_diagnostics(p, diagnosis(p['ctl'], data_of(p), p['encoding'], p['record_format'], p['record_length']))


def build_state(p, record_num=1, page=1, page_size=50, search='', record_type='', status='', sort='record', descending=False):
    if not p['ctl']: return {'project': {k: p[k] for k in ('id', 'name', 'version')}, 'loaded': False}
    d = diagnose(p); records = d['records']
    if search:
        needle = search.lower(); lines = lines_of(p)
        records = [r for r in records if needle in lines[r['record'] - 1][0].decode(p['encoding']).lower() or needle in ' '.join(r['issues']).lower()]
    if record_type: records = [r for r in records if r['record_type'] == record_type]
    if status: records = [r for r in records if r['status'] == status]
    records = sorted(records, key=lambda r: r.get(sort, r['record']), reverse=descending)
    page_size = max(1, min(100, page_size)); total = len(records); page = max(1, min(page, max(1, (total + page_size - 1) // page_size)))
    visible = records[(page - 1) * page_size:page * page_size]
    n = max(1, min(record_num, max(1, d['record_count'])))
    detail = record_detail(p, n) if d['record_count'] else None
    if detail:
        detail.update({k: d['records'][n - 1][k] for k in ('issues', 'findings', 'status', 'classification')})
    sh = p['sheet']
    sheet = {**{k: v for k, v in sh.items() if k != 'sheets'}, 'sheets': [{**s, 'row_count': len(s['rows']), 'rows': s['rows'][:30], 'suggested_mapping': default_mapping(p['ctl'], s)} for s in sh.get('sheets', [])]}
    rec = p.get('reconciliation', {})
    rec = {k: v[:100] if isinstance(v, list) else v for k, v in rec.items()}
    return {'project': {k: p[k] for k in ('id', 'name', 'version', 'data_version')}, 'loaded': True,
            'diagnosis': {**{k: v for k, v in d.items() if k != 'records'}, 'records': visible},
            'pagination': {'page': page, 'page_size': page_size, 'total': total, 'pages': max(1, (total + page_size - 1) // page_size)},
            'record': detail, 'record_num': n, 'schema': compile_ctl(p['ctl']).to_dict(), 'spreadsheet': sheet,
            'reconciliation': rec, 'proposals': p['proposals'][-250:], 'history': p['history'][-100:],
            'proposal_count': len(p['proposals']), 'ai_runs': p['ai_runs'][-5:], 'undo_count': len(p['undo']), 'redo_count': len(p['redo']),
            'original_checksum': checksum(decode(p['original'])), 'modified': p['data'] != p['original'], 'weights': p['weights'],
            'generated_checksum': p['generated_checksum'], 'sample': p.get('sample')}


class ProjectCreate(Input): name: str = Field(min_length=1, max_length=120)
class Versioned(Input): version: int = Field(ge=1)


@lru_cache(maxsize=1)
def sample_catalog():
    return json.loads((ROOT / 'fixtures' / 'oracle' / 'manifest.json').read_text())


def find_sample(identifier):
    item = next((x for x in sample_catalog()['samples'] if x['id'] == identifier), None)
    if item is None: raise HTTPException(404, 'Sample not found')
    return item


@app.get('/api/samples')
def samples(): return sample_catalog()


@app.get('/api/samples/{sample_id}/download')
def sample_download(sample_id: str):
    item = find_sample(sample_id); folder = ROOT / 'fixtures' / 'oracle' / item['id']
    files = ['layout.ctl', 'reference.csv', 'expected.json', *[v['file'] for v in item['variants']]]
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files: archive.writestr(item['id'] + '/' + name, (folder / name).read_bytes())
    return Response(data.getvalue(), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{item["id"]}.zip"'})


class SampleImport(Versioned):
    sample_id: str = Field(min_length=1, max_length=64)
    variant: str = Field(default='correct', min_length=1, max_length=64)


@app.post('/api/projects/{pid}/sample', response_model=ProjectState, response_model_exclude_unset=True)
def import_sample(pid: str, request: Request, body: SampleImport):
    p = session_or_404(request, pid); check_version(p, body.version)
    if p['ctl'] or p.get('studio'): raise HTTPException(409, 'Load each sample into a new project; existing source bytes are immutable')
    item = find_sample(body.sample_id)
    variant = next((x for x in item['variants'] if x['id'] == body.variant), None)
    if variant is None: raise HTTPException(404, 'Sample variant not found')
    folder = ROOT / 'fixtures' / 'oracle' / item['id']
    install_files(p, (folder / 'layout.ctl').read_bytes(), (folder / variant['file']).read_bytes(), 'reference.csv',
                  (folder / 'reference.csv').read_bytes(), item['encoding'], None, None, 'reference_values')
    p['sample'] = {k: item[k] for k in ('id', 'title', 'description', 'keys', 'record_length', 'supported')}
    p['sample'].update(variant=variant['id'], exercise=variant['exercise'])
    event(p, 'synthetic sample imported', sample_id=item['id'], variant=variant['id'])
    repository.save(request.state.owner, p, body.version)
    return build_state(p, record_num=2 if variant['id'] != 'correct' else 1)

@app.post('/api/projects', status_code=201, response_model=ProjectState, response_model_exclude_unset=True)
def create_project(request: Request, body: ProjectCreate):
    p = repository.create(request.state.owner, body.name)
    return build_state(p)

@app.get('/api/projects')
def projects(request: Request): return repository.list(request.state.owner)

@app.delete('/api/projects/{pid}')
def delete_project(pid: str, request: Request):
    session_or_404(request, pid); repository.delete(request.state.owner, pid)
    return {'deleted': True}


async def file_bytes(upload, maximum=MAX_FILE):
    data = await upload.read(maximum + 1)
    if len(data) > maximum: raise HTTPException(413, f'File exceeds {maximum} byte limit')
    return data


def install_files(p, ctl_bytes, dat_bytes, supplement_name, supplement_bytes, encoding, fmt, length, role):
    try: source = ctl_bytes.decode('utf-8-sig')
    except UnicodeDecodeError: raise ValueError('CTL must be UTF-8 text') from None
    compile_ctl(source); check_encoding(dat_bytes, encoding)
    report = inspect(source, dat_bytes, encoding, fmt, length)
    if report['record_count'] > 20000: raise ValueError('This local edition supports at most 20,000 physical records')
    sheet = import_spreadsheet(supplement_name, supplement_bytes, role) if supplement_bytes else {}
    p.update(ctl=source, data=encode(dat_bytes), original=encode(dat_bytes), encoding=encoding,
             record_format=fmt, record_length=length, sheet=sheet, reconciliation={}, proposals=[], undo=[], redo=[], generated_checksum=None)
    p['data_version'] += 1
    event(p, 'source files imported', original_checksum=checksum(dat_bytes), records=report['record_count'], encoding=encoding)
    analyze(p)

@app.post('/api/projects/{pid}/files', response_model=ProjectState, response_model_exclude_unset=True)
async def upload_files(pid: str, request: Request, ctl: UploadFile = File(...), dat: UploadFile = File(...), supplement: UploadFile | None = File(None),
                       version: int = Form(...), encoding: str = Form('ascii'), record_format: str = Form('auto'), record_length: int | None = Form(None), role: str = Form('reference_values')):
    p = session_or_404(request, pid); check_version(p, version)
    if p['ctl'] or p.get('studio'): raise HTTPException(409, 'Create a new project to import another source file set; existing source bytes are immutable')
    c, d = await file_bytes(ctl, 2_000_000), await file_bytes(dat)
    sb = await file_bytes(supplement) if supplement and supplement.filename else b''
    fmt = None if record_format == 'auto' else record_format
    await run_in_threadpool(install_files, p, c, d, supplement.filename if supplement else '', sb, encoding, fmt, record_length, role)
    repository.save(request.state.owner, p, version)
    return build_state(p)

@app.get('/api/projects/{pid}/state', response_model=ProjectState, response_model_exclude_unset=True)
def state(pid: str, request: Request, record: int = Query(1, ge=1), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
          search: str = Query('', max_length=200), record_type: str = Query('', max_length=20), status: Literal['', 'valid', 'needs_review'] = '',
          sort: Literal['record', 'record_type', 'length', 'status'] = 'record', descending: bool = False):
    return build_state(session_or_404(request, pid), record, page, page_size, search, record_type, status, sort, descending)

@app.get('/api/projects/{pid}/schema')
def schema(pid: str, request: Request): return compile_ctl(session_or_404(request, pid)['ctl']).to_dict()

@app.get('/api/projects/{pid}/diagnostics', response_model=DiagnosisView)
def diagnostics(pid: str, request: Request):
    d = diagnose(session_or_404(request, pid)); return {k: v for k, v in d.items() if k != 'records'}

@app.get('/api/projects/{pid}/records', response_model=RecordPage)
def records(pid: str, request: Request, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), record_type: str = Query('', max_length=20), layout_branch: int | None = Query(None, ge=0)):
    p = session_or_404(request, pid); rows = diagnose(p)['records']
    if record_type: rows = [r for r in rows if r['record_type'] == record_type]
    return {'version': p['version'], 'total': len(rows), 'records': [record_detail(p, r['record'], layout_branch) for r in rows[(page - 1) * page_size:page * page_size]]}

@app.get('/api/projects/{pid}/records/{record}', response_model=RecordDetail)
def record(pid: str, record: int, request: Request): return record_detail(session_or_404(request, pid), record)

@app.post('/api/projects/{pid}/parse-ctl', response_model=ProjectState, response_model_exclude_unset=True)
@app.post('/api/projects/{pid}/inspect-dat', response_model=ProjectState, response_model_exclude_unset=True)
def reparse(pid: str, request: Request): return build_state(session_or_404(request, pid))

class Analysis(Versioned):
    records: list[int] | None = Field(default=None, max_length=100)
    weights: dict[str, float] | None = None

@app.post('/api/projects/{pid}/analyze', response_model=ProjectState, response_model_exclude_unset=True)
def analysis(pid: str, request: Request, body: Analysis):
    limit(('analysis', request.state.owner), 10)
    p = session_or_404(request, pid); check_version(p, body.version)
    if body.weights:
        score_candidate({k: 0 for k in DEFAULT_WEIGHTS}, body.weights); p['weights'] = body.weights
    analyze(p, body.records); repository.save(request.state.owner, p, body.version)
    return build_state(p, body.records[0] if body.records else 1)

class Mapping(Versioned):
    sheet: str = Field(max_length=128)
    mapping: dict[str, str]
    keys: list[str] = Field(default_factory=list, max_length=10)
    role: Literal['reference_values', 'field_specifications', 'example_records', 'transaction_values'] = 'reference_values'

@app.post('/api/projects/{pid}/map-spreadsheet', response_model=ProjectState, response_model_exclude_unset=True)
def map_sheet(pid: str, request: Request, body: Mapping):
    p = session_or_404(request, pid); check_version(p, body.version); p['sheet']['role'] = body.role
    if body.role == 'field_specifications':
        p['reconciliation'] = compare_field_specifications(p['ctl'], p['sheet'], body.sheet, body.mapping)
        event(p, 'field specifications compared', matches=p['reconciliation']['matched_records'], conflicts=len(p['reconciliation']['spec_conflicts']))
    else: reconcile_project(p, body.mapping, body.keys, body.sheet)
    repository.save(request.state.owner, p, body.version)
    return build_state(p)

class CellEdit(Input):
    record: int = Field(ge=1)
    field: str = Field(min_length=1, max_length=128)
    value: str = Field(max_length=10000)
    align: Literal['left', 'right'] = 'left'
    branch: int | None = Field(default=None, ge=0)
class Edits(Versioned): edits: list[CellEdit] = Field(min_length=1, max_length=500)

@app.patch('/api/projects/{pid}/records', response_model=ProjectState, response_model_exclude_unset=True)
def bulk_edit(pid: str, request: Request, body: Edits):
    p = session_or_404(request, pid); check_version(p, body.version); data = data_of(p)
    for e in body.edits: data = edit_record(p['ctl'], data, e.record, e.field, e.value, e.align, **options(p), branch_index=e.branch)
    commit_bytes(p, data, 'user-approved cell edits')
    repository.save(request.state.owner, p, body.version)
    return build_state(p, body.edits[0].record)

class SingleEdit(Versioned): edits: list[CellEdit] = Field(min_length=1, max_length=100)
@app.patch('/api/projects/{pid}/records/{record}', response_model=ProjectState, response_model_exclude_unset=True)
def single_edit(pid: str, record: int, request: Request, body: SingleEdit):
    if any(e.record != record for e in body.edits): raise ValueError('Edit record does not match URL')
    return bulk_edit(pid, request, Edits(version=body.version, edits=body.edits))

@app.get('/api/projects/{pid}/repair-proposals', response_model=ProposalPage)
def proposals(pid: str, request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=250), pending_only: bool = False):
    p = session_or_404(request, pid)
    rows = [x for x in p['proposals'] if not pending_only or x['status'] in ('PROPOSED', 'PENDING_REVIEW')]
    rows.sort(key=lambda x: (x['record'], x['source'] != 'DETERMINISTIC', x.get('rank', 999), -x['candidate_score'], x['id']))
    return {'total': len(rows), 'proposals': rows[offset:offset + limit]}

class Review(Versioned):
    ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal['approve', 'reject']
    overrides: dict[str, str] = Field(default_factory=dict)

@app.post('/api/projects/{pid}/repairs/review', response_model=ProjectState, response_model_exclude_unset=True)
def review(pid: str, request: Request, body: Review):
    p = session_or_404(request, pid); check_version(p, body.version)
    if body.action == 'approve': apply_proposals(p, body.ids, body.overrides)
    else:
        for ident in body.ids:
            prop = next((x for x in p['proposals'] if x['id'] == ident), None)
            if not prop or prop['status'] not in ('PROPOSED', 'PENDING_REVIEW'): raise Conflict('Proposal is no longer pending')
            prop['status'] = 'REJECTED'; event(p, 'proposal rejected', proposal_id=ident, record=prop['record'])
    repository.save(request.state.owner, p, body.version)
    return build_state(p)

@app.post('/api/projects/{pid}/repairs/{repair_id}/{action}', response_model=ProjectState, response_model_exclude_unset=True)
def review_one(pid: str, repair_id: str, action: Literal['approve', 'reject'], request: Request, body: Versioned):
    return review(pid, request, Review(version=body.version, ids=[repair_id], action=action))

@app.post('/api/projects/{pid}/history/{action}', response_model=ProjectState, response_model_exclude_unset=True)
def history_action(pid: str, action: Literal['undo', 'redo', 'reset'], request: Request, body: Versioned):
    p = session_or_404(request, pid); check_version(p, body.version)
    if action == 'reset': commit_bytes(p, decode(p['original']), 'reset to source DAT')
    else: undo_redo(p, action)
    repository.save(request.state.owner, p, body.version)
    return build_state(p)

@app.get('/api/ai/bedrock/status')
@app.get('/api/ai/status')
def bedrock_status(): return provider.status()

@app.post('/api/ai/bedrock/test')
@app.post('/api/ai/test')
def bedrock_test(request: Request):
    limit(('ai', request.state.owner)); return provider.test_connection()

class AIRequest(Versioned):
    capability: Literal['schema', 'mapping', 'investigate', 'recover', 'explain']
    record: int = Field(default=1, ge=1)
    share_values: bool = False

@app.post('/api/projects/{pid}/ai/recover')
def ai_recover(pid: str, request: Request, body: AIRequest):
    limit(('ai', request.state.owner))
    p = session_or_404(request, pid); check_version(p, body.version)
    context = ai_context(p, body.capability, body.record, body.share_values)
    if context is None: return {'skipped': True, 'reason': 'No unresolved findings for this record; an AI call is unnecessary', 'state': build_state(p, body.record)}
    if body.capability == 'recover' and not context.get('evidence'):
        return {'skipped': True, 'reason': 'INSUFFICIENT_EVIDENCE: reconcile a uniquely key-matched sheet and explicitly enable value sharing', 'state': build_state(p, body.record)}
    run = provider.invoke(body.capability, context)
    accept_ai_result(p, run, body.capability, body.record, body.share_values)
    repository.save(request.state.owner, p, body.version)
    return {'skipped': False, 'run': run, 'state': build_state(p, body.record)}

@app.post('/api/projects/{pid}/validate')
def validate(pid: str, request: Request):
    p = session_or_404(request, pid); _, report = verify_generation(p)
    return {k: v for k, v in report.items() if k != 'records'}

class Generate(Versioned): draft: bool = False
@app.post('/api/projects/{pid}/generate')
def generate(pid: str, request: Request, body: Generate):
    p = session_or_404(request, pid); check_version(p, body.version)
    _, report = verify_generation(p)
    if report['export_status'] not in ('structurally_validated', 'validated_with_warnings'):
        if not body.draft or os.getenv('STUDIO_ALLOW_DRAFT_EXPORT', '').lower() != 'true': raise HTTPException(409, {'status': report['export_status'], 'message': 'Validated generation blocked. Resolve findings/unsupported declarations or enable the explicit draft export policy.'})
    p['generated_checksum'] = report['sha256']
    p['generated_draft'] = report['export_status'] not in ('structurally_validated', 'validated_with_warnings')
    event(p, 'DAT generated and re-parsed', checksum=report['sha256'], status=report['export_status'], round_trip_identical=report['round_trip_identical'])
    repository.save(request.state.owner, p, body.version)
    return {'state': build_state(p), 'validation': {k: v for k, v in report.items() if k != 'records'}}

@app.get('/api/projects/{pid}/download')
def download(pid: str, request: Request, original: bool = False):
    p = session_or_404(request, pid)
    if original: return Response(decode(p['original']), media_type='application/octet-stream', headers={'Content-Disposition': 'attachment; filename="original.dat"'})
    data, report = verify_generation(p)
    if p['generated_checksum'] != checksum(data): raise HTTPException(409, 'Generate and validate the current version before downloading')
    if report['export_status'] not in ('structurally_validated', 'validated_with_warnings') and not (p.get('generated_draft') and os.getenv('STUDIO_ALLOW_DRAFT_EXPORT', '').lower() == 'true'): raise HTTPException(409, 'Export blocked by validation')
    name = 'aligned.dat' if report['export_status'] in ('structurally_validated', 'validated_with_warnings') else 'UNVALIDATED-DRAFT.dat'
    return Response(data, media_type='application/octet-stream', headers={'Content-Disposition': f'attachment; filename="{name}"', 'X-Validation-Status': report['export_status'], 'X-Checksum-SHA256': checksum(data)})

@app.get('/api/projects/{pid}/report')
def report(pid: str, request: Request):
    p = session_or_404(request, pid); _, d = verify_generation(p)
    d.update(original_checksum=checksum(decode(p['original'])), project_version=p['version'], schema=compile_ctl(p['ctl']).to_dict())
    return Response(json.dumps(d, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="validation-report.json"'})

@app.get('/api/projects/{pid}/audit')
def audit(pid: str, request: Request):
    p = session_or_404(request, pid)
    result = {'project': p['name'], 'version': p['version'], 'original_checksum': checksum(decode(p['original'])), 'current_checksum': checksum(data_of(p)),
              'events': p['history'], 'proposals': p['proposals'], 'ai_runs': p['ai_runs'], 'weights': p['weights']}
    return Response(json.dumps(result, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="repair-audit.json"'})


def active_provider():
    if os.getenv('BEDROCK_MOCK', '').lower() in ('true', '1', 'yes'):
        return configured_provider()
    p = globals().get('provider')
    if p is not None and not isinstance(p, AmazonBedrockProvider):
        return p
    return configured_provider()


studio_service = install_studio(app, lambda: repository, active_provider)
