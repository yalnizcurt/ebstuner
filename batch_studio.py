"""Additive, owner-scoped multi-file Studio workflow."""
from __future__ import annotations

import copy
import io
import json
import secrets
import zipfile
import zlib

from fastapi import HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from engine import parse_lines
from projects import check_version, checksum, decode, encode, event, now
import specs
from validation_pipeline import compile_pipeline


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Version(Strict):
    version: int = Field(ge=1)


class SpecRevision(Version):
    fields: list[dict] = Field(min_length=1, max_length=4000)
    layouts: list[dict] = Field(default_factory=list, max_length=100)
    options: dict = Field(default_factory=dict)


class Association(Strict):
    dat_id: str
    spec_id: str
    layout_ids: list[str] = Field(min_length=1)


class Associations(Version):
    associations: list[Association] = Field(max_length=500)


class AIDecision(Version):
    evidence_id: str = Field(min_length=64, max_length=64)
    decision: str = Field(pattern='^(accept|reject|dismiss|resolve)$')
    reason: str = Field(min_length=3, max_length=1000)


class Generate(Version):
    dat_id: str | None = None


def _batch(p):
    return p.setdefault('batch', {'specs': {}, 'dats': {}, 'associations': [], 'results': {}, 'summary': {}})


def _public(p):
    batch = _batch(p)
    return {'project': {k: p[k] for k in ('id', 'name', 'version')}, 'batch': {
        'specs': [{k: v for k, v in item.items() if k not in ('source', 'document', 'approved_spec', 'versions')}
                  for item in batch['specs'].values()],
        'dats': [{**{k: v for k, v in item.items() if k != 'original'},
                  'export_eligible': bool(batch['results'].get(item['id'], {}).get('generated'))}
                 for item in batch['dats'].values()],
        'associations': batch['associations'], 'summary': batch['summary']}}


def _spec(batch, sid):
    item = batch['specs'].get(sid)
    if not item:
        raise HTTPException(404, 'Specification not found in this project.')
    return item


def _dat(batch, did):
    item = batch['dats'].get(did)
    if not item:
        raise HTTPException(404, 'DAT file not found in this project.')
    return item


def _projected(spec, layout):
    selected = set(layout['field_ids'])
    return {**spec, 'fields': [f for f in spec['fields'] if f['id'] in selected],
            'layouts': [layout]}


def _candidate_routes(batch, did, row):
    routes, unevaluable = [], False
    for association in batch['associations']:
        if association['dat_id'] != did:
            continue
        item = _spec(batch, association['spec_id'])
        if not item['approved'] or item['version'] != association['spec_version']:
            continue
        spec = item['approved_spec']
        for layout in spec['layouts']:
            if layout['id'] not in association['layout_ids']:
                continue
            verdict = specs._route(layout, row, spec['options']['encoding'])
            if verdict is True:
                routes.append((item, layout))
            elif verdict is None:
                unevaluable = True
    return routes, unevaluable


def _file_rows(dat, batch):
    bindings = [a for a in batch['associations'] if a['dat_id'] == dat['id']]
    if not bindings:
        raise HTTPException(409, 'Associate an approved specification and layout before processing this DAT.')
    settings = {(batch['specs'][a['spec_id']]['approved_spec']['options'].get('record_format', 'stream'),
                 batch['specs'][a['spec_id']]['approved_spec']['options'].get('record_length'),
                 batch['specs'][a['spec_id']]['approved_spec']['options'].get('encoding', 'ascii')) for a in bindings}
    if len(settings) != 1:
        raise HTTPException(409, 'All specifications assigned to one DAT must agree on encoding and record framing.')
    record_format, record_length, encoding = settings.pop()
    raw = decode(dat['original'])
    try:
        if encoding == 'utf-8': raw.decode('utf-8', 'strict')
        else: specs.check_encoding(raw, encoding)
        rows = parse_lines(raw, record_format, record_length)
        if len(rows) > 20_000:
            raise HTTPException(413, 'A DAT batch is limited to 20,000 physical records per file.')
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from None
    return rows


def _process_file(batch, dat):
    rows = _file_rows(dat, batch)
    summaries, output, provenance = [], [], {}
    for number, (row, ending) in enumerate(rows, 1):
        routes, unevaluable = _candidate_routes(batch, dat['id'], row)
        if len(routes) != 1 or unevaluable:
            code = 'AMBIGUOUS_LAYOUT' if len(routes) > 1 or (routes and unevaluable) else 'UNKNOWN_LAYOUT'
            summary = {'number': number, 'status': 'unvalidated', 'code': code,
                       'spec_id': None, 'layout_id': None, 'original_sha256': checksum(row),
                       'output_sha256': checksum(row), 'byte_preserved': True, 'ai_status': 'not_run',
                       'issues': [specs.issue(code, 'Explicit record routing did not select exactly one layout', record=number)]}
            summaries.append(summary)
            output.append(row + ending)
            continue
        item, layout = routes[0]
        spec = _projected(item['approved_spec'], layout)
        try:
            report, candidate = specs._process(spec, row + ending, [], {'detail_record': 1})
            detail = report['records'][0] if report['records'] else {'fields': [], 'alignment': None}
            valid = report['valid'] and len(report['record_summaries']) == 1
            issues = report['record_summaries'][0]['issues'] if report['record_summaries'] else report['issues']
        except specs.SpecError as exc:
            candidate, detail, valid, issues = row + ending, {'fields': [], 'alignment': None}, False, exc.issues
        # A failed record is represented by its immutable original bytes; no partial candidate escapes.
        if not valid:
            candidate = row + ending
        summary = {'number': number, 'status': 'validated' if valid else 'needs_review',
                   'code': None if valid else 'VALIDATION_FAILED', 'spec_id': item['id'],
                   'spec_version': item['version'], 'layout_id': layout['id'],
                   'original_sha256': checksum(row), 'output_sha256': checksum(candidate[:-len(ending)] if ending else candidate),
                   'byte_preserved': candidate == row + ending, 'ai_status': 'not_run', 'issues': issues}
        if valid and candidate != row + ending:
            ledger = (detail.get('alignment') or {}).get('provenance')
            if not ledger or ledger.get('source_bytes_accounted_for') != len(row):
                valid = False
                summary.update(status='needs_review', code='MISSING_PROVENANCE', byte_preserved=True)
                summary['issues'] = [*summary['issues'], specs.issue('MISSING_PROVENANCE',
                    'Changed bytes require complete TASAA source-byte evidence', record=number)]
                candidate = row + ending
                summary['output_sha256'] = checksum(row)
            else:
                packed = zlib.compress(json.dumps(ledger, separators=(',', ':')).encode())
                if len(packed) > 2_000_000:
                    valid = False
                    summary.update(status='needs_review', code='PROVENANCE_LIMIT', byte_preserved=True)
                    summary['issues'] = [*summary['issues'], specs.issue('PROVENANCE_LIMIT',
                        'Byte provenance exceeds the supported record limit', record=number)]
                    candidate = row + ending
                    summary['output_sha256'] = checksum(row)
                else:
                    provenance[str(number)] = encode(packed)
                    summary['correction'] = {'original_bytes': len(row), 'output_bytes': len(candidate) - len(ending),
                        'inserted_padding_bytes': len(ledger.get('output', [])),
                        'removed_declared_padding_bytes': sum(x.get('disposition') == 'declared_padding' for x in ledger['source']),
                        'provenance_sha256': checksum(json.dumps(ledger, separators=(',', ':')).encode())}
        summaries.append(summary)
        output.append(candidate)
    raw = decode(dat['original'])
    result = {'input_sha256': checksum(raw), 'output': encode(b''.join(output)), 'output_sha256': checksum(b''.join(output)),
              'provenance': provenance,
              'record_summaries': summaries, 'record_count': len(rows),
              'validated_count': sum(r['status'] == 'validated' for r in summaries),
              'unvalidated_count': sum(r['status'] != 'validated' for r in summaries),
              'ai_status': 'not_run', 'generated': None}
    return result


def _detail(batch, dat, summary, rows=None):
    result = {**summary, 'routing_status': summary['code'] or 'routed',
              'validation_status': summary['status'], 'fields': [], 'alignment': None}
    if not summary['spec_id']:
        return result
    row, ending = (rows if rows is not None else _file_rows(dat, batch))[summary['number'] - 1]
    item = _spec(batch, summary['spec_id'])
    layout = next(layout for layout in item['approved_spec']['layouts'] if layout['id'] == summary['layout_id'])
    try:
        report = specs.inspect_dat(_projected(item['approved_spec'], layout), row + ending, [], {'detail_record': 1})
        if report['records']:
            result['fields'] = report['records'][0].get('fields', [])
            result['alignment'] = report['records'][0].get('alignment')
    except specs.SpecError:
        pass
    return result


def install(app, repository, provider, studio_service=None):
    def load(request, pid, version=None):
        p = repository().load(request.state.owner, pid)
        if version is not None: check_version(p, version)
        return p

    def save(request, p, action):
        event(p, action)
        repository().save(request.state.owner, p, p['version'])
        return _public(p)

    @app.get('/api/projects/{pid}/studio/batch')
    def state(pid: str, request: Request):
        return _public(load(request, pid))

    @app.post('/api/projects/{pid}/studio/batch/files')
    async def upload(pid: str, request: Request):
        form = await request.form()
        try: version = int(form['version'])
        except (KeyError, TypeError, ValueError): raise HTTPException(422, 'Supply the current project version.') from None
        p = load(request, pid, version)
        if p.get('studio') or p.get('ctl') or p.get('original'):
            raise HTTPException(409, 'Use a new project for a multi-file batch; existing originals are immutable.')
        batch = copy.deepcopy(_batch(p))
        uploads = [(key, file) for key, file in form.multi_items() if key in ('specs', 'dats')]
        if not uploads or len(uploads) > 20:
            raise HTTPException(422, 'Upload 1–20 specification or DAT files per request.')
        total = 0
        encoding = str(form.get('encoding', 'ascii'))
        if encoding not in ('ascii', 'latin1', 'iso-8859-1', 'cp1252', 'utf-8'):
            raise HTTPException(422, 'Choose a supported DAT encoding.')
        for key, file in uploads:
            if not hasattr(file, 'read'):
                raise HTTPException(422, 'Each upload must be a file.')
            limit = 2_000_000 if key == 'specs' else 12_000_000
            raw = await file.read(limit + 1)
            total += len(raw)
            if not raw or len(raw) > limit or total > 30_000_000:
                raise HTTPException(413, 'A spec is limited to 2 MB, a DAT to 12 MB, and one upload to 30 MB.')
            current_bytes = sum(s['bytes'] if 'bytes' in s else len(decode(s['source'])) for s in batch['specs'].values())
            current_bytes += sum(d['bytes'] for d in batch['dats'].values())
            if current_bytes + len(raw) > 16_000_000:
                raise HTTPException(413, 'This project is limited to 16 MB of original files. Start another batch.')
            ident = secrets.token_hex(8)
            name = (file.filename or ('spec.csv' if key == 'specs' else 'input.dat'))[:200]
            if key == 'specs':
                if len(batch['specs']) >= 20: raise HTTPException(413, 'Project specification limit reached.')
                document = specs.extract_spec(name, raw)
                draft = specs.normalize_spec({'fields': copy.deepcopy(document['fields']), 'layouts': [],
                     'source_issues': document['issues'], 'options': {'encoding': encoding, 'record_format': 'stream',
                     'unknown_records': 'block', 'confirmed_records': []}})
                batch['specs'][ident] = {'id': ident, 'name': name, 'source': encode(raw), 'document': document,
                   'sha256': checksum(raw), 'bytes': len(raw), 'version': 0, 'approved': False, 'approved_spec': None,
                   'fields': draft['fields'], 'layouts': draft['layouts'], 'options': draft['options'],
                   'issues': draft['issues'], 'coverage_limitations': draft['coverage_limitations'], 'ai_status': 'not_run'}
            else:
                if len(batch['dats']) >= 20: raise HTTPException(413, 'Project DAT limit reached.')
                batch['dats'][ident] = {'id': ident, 'name': name, 'original': encode(raw), 'sha256': checksum(raw),
                                       'bytes': len(raw), 'record_count': None, 'status': 'uploaded', 'summary': {}}
        p['batch'] = batch
        return save(request, p, 'Batch originals imported')

    @app.put('/api/projects/{pid}/studio/batch/specs/{sid}')
    def revise(pid: str, sid: str, request: Request, body: SpecRevision):
        p = load(request, pid, body.version); batch = _batch(p); item = _spec(batch, sid)
        old_ids = {f['id'] for f in item['fields']}
        if len(body.fields) != len(old_ids) or {f.get('id') for f in body.fields} != old_ids:
            raise HTTPException(422, 'Keep every source field and stable identifier.')
        fields = copy.deepcopy(body.fields)
        source = {f['id']: f for f in item['fields']}
        for field in fields:
            old = source[field['id']]
            field['source'] = old.get('source', {})
            changed = [key for key in ('mapping_type', 'datatype_conversion', 'padding_alignment')
                       if field.get(key) != old.get(key)]
            if changed:
                baseline, old_rules, submitted = specs.infer_rules(field), old.get('rules', {}), field.get('rules', {})
                merged = {**old_rules, **submitted}
                affected = []
                if 'mapping_type' in changed: affected += ['mapping_type', 'constant']
                if 'datatype_conversion' in changed: affected += ['datatype', 'conversion', 'date_format', 'unsupported_conversion']
                if 'padding_alignment' in changed: affected += ['padding']
                for key in affected:
                    if submitted.get(key) == old_rules.get(key):
                        if key in baseline: merged[key] = baseline[key]
                        else: merged.pop(key, None)
                field['rules'] = merged
        draft = specs.normalize_spec({'fields': fields, 'layouts': body.layouts or item['layouts'],
            'options': body.options or item['options'], 'source_issues': item['document']['issues']})
        item.update(fields=draft['fields'], layouts=draft['layouts'], options=draft['options'],
                    issues=draft['issues'], coverage_limitations=draft['coverage_limitations'], approved=False,
                    approved_spec=None, ai_status='not_run')
        batch['results'] = {}; batch['summary'] = {}
        return save(request, p, 'Batch specification draft revised')

    @app.get('/api/projects/{pid}/studio/batch/specs/{sid}/source')
    def spec_source(pid: str, sid: str, request: Request):
        p = load(request, pid); item = _spec(_batch(p), sid)
        data = decode(item['source'])
        if checksum(data) != item['sha256']: raise HTTPException(409, 'Original specification checksum mismatch.')
        suffix = '.xlsx' if item['document'].get('format') == 'xlsx' else '.csv'
        return Response(data, media_type='application/octet-stream', headers={'X-Checksum-SHA256': item['sha256'],
            'Content-Disposition': f'attachment; filename="original-{sid}{suffix}"'})

    @app.post('/api/projects/{pid}/studio/batch/specs/{sid}/approve')
    def approve(pid: str, sid: str, request: Request, body: Version):
        p = load(request, pid, body.version); batch = _batch(p); item = _spec(batch, sid)
        draft = specs.normalize_spec({'fields': item['fields'], 'layouts': item['layouts'],
                    'options': item['options'], 'source_issues': item['document']['issues']})
        blockers = [i for i in draft['issues'] if i.get('severity', 'error') == 'error']
        if blockers: raise HTTPException(409, {'message': 'Resolve specification issues before approval.', 'issues': blockers})
        approved = {**draft, 'approved': True, 'version': item['version'] + 1,
                    'approved_at': now(), 'approved_by': request.state.owner}
        compile_pipeline(approved)
        item.setdefault('versions', []).append(copy.deepcopy(approved))
        if len(item['versions']) > 100: raise HTTPException(409, 'Specification version limit reached.')
        item.update(version=approved['version'], approved=True, approved_spec=approved,
                    fields=approved['fields'], layouts=approved['layouts'], options=approved['options'],
                    issues=approved['issues'], coverage_limitations=approved['coverage_limitations'])
        batch['results'] = {}; batch['summary'] = {}
        return save(request, p, f'Batch specification {sid} approved as version {item["version"]}')

    @app.put('/api/projects/{pid}/studio/batch/associations')
    def associate(pid: str, request: Request, body: Associations):
        p = load(request, pid, body.version); batch = _batch(p)
        seen, associations = set(), []
        for binding in body.associations:
            dat = _dat(batch, binding.dat_id); item = _spec(batch, binding.spec_id)
            if not item['approved']: raise HTTPException(409, f'Approve specification {item["name"]} first.')
            known = {layout['id'] for layout in item['approved_spec']['layouts']}
            if len(set(binding.layout_ids)) != len(binding.layout_ids) or set(binding.layout_ids) - known:
                raise HTTPException(422, 'Association names duplicate or unknown layout identifiers.')
            marker = (dat['id'], item['id'])
            if marker in seen: raise HTTPException(422, 'Duplicate DAT/specification association.')
            seen.add(marker)
            associations.append({**binding.model_dump(), 'spec_version': item['version']})
        batch.update(associations=associations, results={}, summary={})
        for dat in batch['dats'].values(): dat.update(status='uploaded', record_count=None, summary={})
        return save(request, p, 'Batch specification associations saved')

    @app.post('/api/projects/{pid}/studio/batch/process')
    def process(pid: str, request: Request, body: Version):
        p = load(request, pid, body.version); batch = _batch(p)
        if not batch['dats']: raise HTTPException(409, 'Upload DAT files before processing.')
        stale = [a for a in batch['associations'] if not batch['specs'][a['spec_id']]['approved'] or
                 batch['specs'][a['spec_id']]['version'] != a['spec_version']]
        if stale: raise HTTPException(409, 'Specification versions changed; review and save associations again.')
        results = {}
        for dat in batch['dats'].values():
            result = _process_file(batch, dat)
            results[dat['id']] = result
            dat.update(status='needs_review' if result['unvalidated_count'] else 'validated_pending_ai',
                       record_count=result['record_count'], summary={k: result[k] for k in ('validated_count', 'unvalidated_count', 'ai_status')})
        batch['results'] = results
        batch['summary'] = {'files': len(results), 'records': sum(r['record_count'] for r in results.values()),
            'validated': sum(r['validated_count'] for r in results.values()),
            'unvalidated': sum(r['unvalidated_count'] for r in results.values()), 'ai_status': 'not_run'}
        return save(request, p, 'Batch deterministic validation completed')

    @app.get('/api/projects/{pid}/studio/batch/records/{did}')
    def records(pid: str, did: str, request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        p = load(request, pid); batch = _batch(p); _dat(batch, did)
        result = batch['results'].get(did)
        if not result: raise HTTPException(409, 'Process this DAT before opening record findings.')
        summaries = result['record_summaries'][offset:offset + limit]
        return {'project_version': p['version'], 'dat_id': did, 'total': result['record_count'],
                'records': [_detail(batch, batch['dats'][did], row) if limit == 1 else
                            {**row, 'routing_status': row['code'] or 'routed', 'validation_status': row['status'],
                             'fields': [], 'alignment': None} for row in summaries]}

    @app.get('/api/projects/{pid}/studio/batch/provenance/{did}/{number}')
    def record_provenance(pid: str, did: str, number: int, request: Request):
        p = load(request, pid); batch = _batch(p); _dat(batch, did)
        result = batch['results'].get(did)
        packed = (result or {}).get('provenance', {}).get(str(number))
        if not packed: raise HTTPException(404, 'No structural byte correction was certified for this record.')
        return json.loads(zlib.decompress(decode(packed)))

    @app.post('/api/projects/{pid}/studio/batch/ai')
    def analyze_ai(pid: str, request: Request, body: Version):
        from studio import DATAnalysis, DAT_INSTRUCTIONS
        p = load(request, pid, body.version); batch = _batch(p)
        if not batch['results']: raise HTTPException(409, 'Process the batch before AI review.')
        ai = provider()
        if not hasattr(ai, 'invoke_structured') or not ai.status().get('ai_available'):
            raise HTTPException(409, 'An approved AI provider is unavailable; deterministic results remain saved.')
        for did, result in batch['results'].items():
            dat = batch['dats'][did]
            rows = _file_rows(dat, batch)
            for record in result['record_summaries']:
                if not record['spec_id'] or record['ai_status'] in ('reviewed', 'needs_review', 'evidence_too_large'): continue
                item = _spec(batch, record['spec_id'])
                detail = _detail(batch, dat, record, rows)
                fields = detail['fields']
                layout = next(l for l in item['approved_spec']['layouts'] if l['id'] == record['layout_id'])
                by_id = {field['id']: field for field in item['approved_spec']['fields']}
                signature = [[fid, by_id[fid]['rules'].get('start'), by_id[fid]['rules'].get('end'),
                    by_id[fid]['rules'].get('datatype'), by_id[fid]['rules'].get('conversion'),
                    by_id[fid]['rules'].get('required'), by_id[fid]['rules'].get('padding')]
                    for fid in layout['field_ids']]
                context = {'purpose': 'Independently audit one record against its approved specification and TASAA evidence.',
                    'record': record['number'], 'spec_id': item['id'], 'spec_version': record['spec_version'],
                    'layout_id': record['layout_id'], 'layout_conditions': layout['when'],
                    'spec_signature': signature, 'original_record_sha256': record['original_sha256'],
                    'fields': [{'id': field['id'], 'original_value': str(field.get('original_value', ''))[:120],
                                'issues': field.get('issues', [])[:5],
                                'rules': by_id[field['id']]['rules'] if field.get('issues') else None} for field in fields],
                    'issues': record['issues'][:30], 'alignment': detail['alignment'],
                    'coverage_limitations': item['coverage_limitations'][:30]}
                if len(json.dumps(context)) > 32_000:
                    record['ai_status'] = 'evidence_too_large'
                    continue
                evidence_id = checksum(json.dumps(context, sort_keys=True, default=str).encode())
                scope = f"{request.state.owner}/{p['id']}/{did}/{record['number']}/{record['spec_version']}/{result['input_sha256']}"
                run = ai.invoke_structured('studio_dat', context, DATAnalysis, DAT_INSTRUCTIONS, scope=scope)
                findings = run['result']['findings']
                ids = {field['id'] for field in fields}
                if any(f['record'] != record['number'] or f['field_id'] not in ids for f in findings):
                    raise HTTPException(409, 'AI returned a finding outside this record; no review was accepted.')
                record['ai_review'] = {'summary': run['result']['summary'], 'findings': findings,
                    'observations': run['result']['observations'], 'provider': run.get('provider'),
                    'model_id': run.get('model_id'), 'usage': run.get('usage', {}), 'evidence_id': evidence_id}
                record['ai_status'] = 'needs_review' if findings else 'reviewed'
                # Commit each paid provider response so a timeout can resume without repeating completed calls.
                repository().save(request.state.owner, p, p['version'])
            result['ai_status'] = 'reviewed' if all(r['ai_status'] == 'reviewed' for r in result['record_summaries']) else 'incomplete'
            batch['dats'][did]['summary']['ai_status'] = result['ai_status']
        batch['summary']['ai_status'] = 'reviewed' if all(r['ai_status'] == 'reviewed' for r in batch['results'].values()) else 'incomplete'
        return save(request, p, 'Batch AI audit completed')

    @app.post('/api/projects/{pid}/studio/batch/records/{did}/{number}/ai-decision')
    def ai_decision(pid: str, did: str, number: int, request: Request, body: AIDecision):
        p = load(request, pid, body.version); batch = _batch(p); _dat(batch, did)
        result = batch['results'].get(did)
        if not result or not 1 <= number <= result['record_count']:
            raise HTTPException(404, 'Processed record not found.')
        record = result['record_summaries'][number - 1]
        review = record.get('ai_review') or {}
        if record['ai_status'] != 'needs_review' or review.get('evidence_id') != body.evidence_id:
            raise HTTPException(409, 'AI evidence changed; review the current record.')
        record['ai_decision'] = {'decision': body.decision, 'reason': body.reason,
                                 'evidence_id': body.evidence_id, 'by': request.state.owner, 'at': now()}
        record['ai_status'] = 'reviewed'
        result['ai_status'] = 'reviewed' if all(r['ai_status'] == 'reviewed' for r in result['record_summaries']) else 'incomplete'
        batch['dats'][did]['summary']['ai_status'] = result['ai_status']
        batch['summary']['ai_status'] = 'reviewed' if all(r['ai_status'] == 'reviewed' for r in batch['results'].values()) else 'incomplete'
        return save(request, p, f'Batch AI finding reviewed for file {did} record {number}')

    @app.post('/api/projects/{pid}/studio/batch/generate')
    def generate(pid: str, request: Request, body: Generate):
        p = load(request, pid, body.version); batch = _batch(p)
        if not batch['results']: raise HTTPException(409, 'Process the batch before export.')
        if body.dat_id: _dat(batch, body.dat_id)
        blocked = []
        for did, result in batch['results'].items():
            if body.dat_id and did != body.dat_id: continue
            if result['unvalidated_count'] or result['ai_status'] != 'reviewed':
                result['generated'] = None
                blocked.append({'dat_id': did, 'reason': 'Unvalidated records or unfinished AI review'})
                continue
            dat = _dat(batch, did)
            if checksum(decode(dat['original'])) != result['input_sha256']:
                result['generated'] = None
                blocked.append({'dat_id': did, 'reason': 'Original checksum changed'})
                continue
            # Re-run deterministic routing and validation on the exact bytes released to the user.
            try:
                rerun = _process_file(batch, dat)
                if rerun['unvalidated_count'] or rerun['output_sha256'] != result['output_sha256']:
                    raise specs.SpecError('Batch output changed during final validation')
                verified = []
                for summary, (row, ending) in zip(result['record_summaries'], _file_rows(dat, batch)):
                    item = _spec(batch, summary['spec_id'])
                    layout = next(l for l in item['approved_spec']['layouts'] if l['id'] == summary['layout_id'])
                    verified.append(specs.render_dat(_projected(item['approved_spec'], layout), row + ending))
                if checksum(b''.join(verified)) != result['output_sha256']:
                    raise specs.SpecError('Exact export bytes differ from compiled validation output')
            except (specs.SpecError, HTTPException) as exc:
                result['generated'] = None
                blocked.append({'dat_id': did, 'reason': str(exc)[:200]})
                continue
            result['generated'] = {'sha256': result['output_sha256'], 'bytes': len(decode(result['output'])),
                'at': now(), 'spec_versions': sorted({(r['spec_id'], r['spec_version']) for r in result['record_summaries']})}
            dat['status'] = 'export_ready'
        batch['summary']['export_ready'] = sum(bool(r.get('generated')) for r in batch['results'].values())
        batch['summary']['blocked_files'] = blocked
        return save(request, p, 'Batch DAT exports passed final validation')

    def eligible(batch, did):
        result = batch['results'].get(did); dat = _dat(batch, did)
        if not result or not result.get('generated') or result['unvalidated_count'] or result['ai_status'] != 'reviewed':
            raise HTTPException(409, 'Generate a fully validated, AI-reviewed batch before download.')
        data = decode(result['output'])
        if checksum(data) != result['generated']['sha256'] or checksum(decode(dat['original'])) != result['input_sha256']:
            raise HTTPException(409, 'Export or original checksum changed; validate again.')
        return data, result

    @app.get('/api/projects/{pid}/studio/batch/download/{did}')
    def download(pid: str, did: str, request: Request, original: bool = False):
        p = load(request, pid); batch = _batch(p); dat = _dat(batch, did)
        if original:
            data = decode(dat['original'])
            if checksum(data) != dat['sha256']: raise HTTPException(409, 'Original file checksum mismatch.')
            return Response(data, media_type='application/octet-stream', headers={'X-Checksum-SHA256': dat['sha256'],
                'Content-Disposition': f'attachment; filename="original-{did}.dat"'})
        data, result = eligible(batch, did)
        return Response(data, media_type='application/octet-stream', headers={'X-Checksum-SHA256': result['output_sha256'],
            'Content-Disposition': f'attachment; filename="validated-{did}.dat"'})

    @app.get('/api/projects/{pid}/studio/batch/archive')
    def archive(pid: str, request: Request):
        p = load(request, pid); batch = _batch(p)
        if not batch['dats']: raise HTTPException(409, 'No DAT files are ready for export.')
        included = [did for did, result in batch['results'].items() if result.get('generated')]
        if not included: raise HTTPException(409, 'No DAT files passed final validation.')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for did in included:
                data, result = eligible(batch, did)
                archive.writestr(f'validated-{did}.dat', data)
                report = {k: v for k, v in result.items() if k not in ('output', 'provenance')}
                archive.writestr(f'validation-{did}.json', json.dumps(report, ensure_ascii=False, indent=2))
                for number, packed in result.get('provenance', {}).items():
                    archive.writestr(f'provenance-{did}-{number}.json', zlib.decompress(decode(packed)))
            archive.writestr('manifest.json', json.dumps({'included_dat_ids': included,
                'blocked_files': batch['summary'].get('blocked_files', []), 'created_at': now()}, indent=2))
        return Response(buffer.getvalue(), media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="validated-batch.zip"'})
