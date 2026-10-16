"""Persistent, owner-scoped project state and review transactions."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from engine import (compile_ctl, inspect, physical_lines, selected_fields, field_value, edit_record,
                    round_trip, inspect_record, resolve_field)
from reconstruction import reconstruct, score_candidate, DEFAULT_WEIGHTS, hard_constraints
from spreadsheets import reconcile
from bedrock import AIError


def now(): return datetime.now(timezone.utc).isoformat()
def checksum(data): return hashlib.sha256(data).hexdigest()
def encode(data): return base64.b64encode(data).decode('ascii')
def decode(data): return base64.b64decode(data)
def options(p): return {k: p[k] for k in ('encoding', 'record_format', 'record_length')}
def data_of(p): return decode(p['data'])
def lines_of(p, original=False): return physical_lines(p['ctl'], decode(p['original'] if original else p['data']), p['record_format'], p['record_length'])


class Conflict(ValueError): pass


# shortcut: SQLite snapshots, use indexed record/blob storage before lifting local file limits.
class Repository:
    def __init__(self, path=None): self.path = path
    def connection(self):
        path = Path(self.path or os.getenv('STUDIO_DB_PATH') or Path(__file__).parent / '.studio' / 'projects.sqlite3')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(path, timeout=15)
        db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA foreign_keys=ON')
        db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires REAL NOT NULL)')
        try: path.chmod(0o600)
        except OSError: pass
        return db
    def session(self, token=None):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            if token:
                digest = checksum(token.encode())
                return digest if db.execute('SELECT id FROM sessions WHERE id=?', (digest,)).fetchone() else None
            token = secrets.token_urlsafe(32); digest = checksum(token.encode())
            db.execute('INSERT INTO sessions VALUES (?, ?)', (digest, time.time() + 7 * 86400))
            return token
    def cleanup(self, db):
        days = int(os.getenv('STUDIO_RETENTION_DAYS') or 7)
        if not 1 <= days <= 365: raise ValueError('STUDIO_RETENTION_DAYS must be 1..365')
        db.execute('DELETE FROM projects WHERE updated < ?', (time.time() - days * 86400,))
    def list(self, owner):
        with self.connection() as db:
            self.cleanup(db)
            return [{'id': r[0], 'name': r[1], 'version': r[2], 'updated': r[3]} for r in db.execute('SELECT id,name,version,updated FROM projects WHERE owner=? ORDER BY updated DESC', (owner,))]
    def create(self, owner, name):
        p = {'id': secrets.token_urlsafe(18), 'name': name, 'version': 1, 'ctl': '', 'data': '', 'original': '',
             'encoding': 'ascii', 'record_format': None, 'record_length': None, 'sheet': {}, 'reconciliation': {},
             'proposals': [], 'history': [], 'undo': [], 'redo': [], 'ai_runs': [], 'generated_checksum': None,
             'weights': DEFAULT_WEIGHTS.copy(), 'data_version': 1, 'record_versions': {}}
        with self.connection() as db:
            self.cleanup(db)
            if db.execute('SELECT COUNT(*) FROM projects WHERE owner=?', (owner,)).fetchone()[0] >= 20: raise ValueError('Project limit reached; delete an unused project')
            if db.execute('SELECT COUNT(*) FROM projects').fetchone()[0] >= 200: raise ValueError('Server project limit reached')
            db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)', (p['id'], owner, name, 1, time.time(), json.dumps(p)))
        return p
    def load(self, owner, pid):
        with self.connection() as db:
            self.cleanup(db)
            r = db.execute('SELECT payload FROM projects WHERE id=? AND owner=?', (pid, owner)).fetchone()
        if not r: raise KeyError('Project not found or access denied')
        return json.loads(r[0])
    def save(self, owner, p, expected):
        p['version'] = expected + 1
        if len(p['proposals']) > 5000: raise ValueError('Project exceeds 5,000 repair proposals; export its audit and start another project')
        payload = json.dumps(p)
        if len(payload) > 80_000_000: raise ValueError('Project storage limit reached; export and start another project')
        with self.connection() as db:
            if db.execute('UPDATE projects SET name=?,version=?,updated=?,payload=? WHERE id=? AND owner=? AND version=?',
                          (p['name'], p['version'], time.time(), payload, p['id'], owner, expected)).rowcount != 1:
                raise Conflict('Project changed in another request; refresh before applying changes')
        return p
    def delete(self, owner, pid):
        with self.connection() as db: db.execute('DELETE FROM projects WHERE id=? AND owner=?', (pid, owner))


def check_version(p, version):
    if version != p['version']: raise Conflict('Stale project version; refresh and review the current bytes')


def event(p, action, **details): p['history'].append({'timestamp': now(), 'action': action, **details})


def invalidate_proposals(p, affected=None):
    for proposal in p['proposals']:
        if proposal['status'] in ('PROPOSED', 'PENDING_REVIEW', 'APPROVED', 'APPLIED') and (affected is None or proposal['record'] in affected): proposal['status'] = 'SUPERSEDED'


def commit_bytes(p, new, action, approved_ids=None):
    before = data_of(p)
    if before == new: return
    old_lines = lines_of(p); new_lines = physical_lines(p['ctl'], new, p['record_format'], p['record_length'])
    if len(old_lines) != len(new_lines): raise ValueError('Edits may not change the physical record count')
    changes = [{'record': i, 'before': encode(a + ae), 'after': encode(b + be)} for i, ((a, ae), (b, be)) in enumerate(zip(old_lines, new_lines), 1) if (a, ae) != (b, be)]
    p['undo'].append({'changes': changes, 'approved_ids': approved_ids or []}); p['undo'] = p['undo'][-20:]; p['redo'] = []
    p['data'] = encode(new); p['data_version'] += 1; p['generated_checksum'] = None
    affected = {x['record'] for x in changes}
    for n in affected: p.setdefault('record_versions', {})[str(n)] = p.get('record_versions', {}).get(str(n), 1) + 1
    invalidate_proposals(p, affected)
    for prop in p['proposals']:
        if prop['id'] in (approved_ids or []):
            prop['status'] = 'APPLIED'
            prop['resulting_record_checksum'] = checksum(new_lines[prop['record'] - 1][0])
            prop['resulting_file_checksum'] = checksum(new)
            prop['applied_at'] = now()
    p['reconciliation'] = {}
    event(p, action, records=sorted(affected), before_checksum=checksum(before), after_checksum=checksum(new), data_version=p['data_version'])


def undo_redo(p, action):
    source, target = ('undo', 'redo') if action == 'undo' else ('redo', 'undo')
    if not p[source]: raise ValueError(f'No {action} available')
    item = p[source].pop(); lines = lines_of(p)
    for c in item['changes']:
        i = c['record'] - 1
        expected = decode(c['after'] if action == 'undo' else c['before'])
        if lines[i][0] + lines[i][1] != expected: raise Conflict('Undo bytes no longer match current record')
        replacement = decode(c['before'] if action == 'undo' else c['after'])
        ending = lines[i][1]
        lines[i] = (replacement[:-len(ending)], ending) if ending else (replacement, b'')
    before = data_of(p); new = b''.join(a + e for a, e in lines)
    p[target].append(item); p['data'] = encode(new); p['data_version'] += 1; p['generated_checksum'] = None
    invalidate_proposals(p); p['reconciliation'] = {}
    for c in item['changes']: p.setdefault('record_versions', {})[str(c['record'])] = p.get('record_versions', {}).get(str(c['record']), 1) + 1
    if action == 'undo':
        for prop in p['proposals']:
            if prop['id'] in item['approved_ids']: prop['status'] = 'SUPERSEDED'
    event(p, action, before_checksum=checksum(before), after_checksum=checksum(new), data_version=p['data_version'])


def analyze(p, records=None):
    active = [x for x in p['proposals'] if x['status'] in ('PROPOSED', 'PENDING_REVIEW')]
    evidence = p['reconciliation'].get('evidence', [])
    candidates = reconstruct(p['ctl'], data_of(p), **options(p), weights=p['weights'], evidence=evidence, record_numbers=records)
    new_ids = {x['id'] for x in candidates}
    for x in active:
        if x['source'] == 'DETERMINISTIC' and (records is None or x['record'] in records): x['status'] = 'SUPERSEDED'
    candidates = [x for x in candidates if not any(y['id'] == x['id'] and y['status'] in ('APPLIED', 'REJECTED') for y in p['proposals'])]
    existing = {x['id']: x for x in p['proposals']}
    for x in candidates:
        x['status'] = 'PENDING_REVIEW'; x['data_version'] = p['data_version']; x['record_version'] = p.get('record_versions', {}).get(str(x['record']), 1); existing[x['id']] = x
    p['proposals'] = list(existing.values())
    event(p, 'deterministic analysis', candidate_count=len(candidates), bound='100 damaged records; 64 missing spaces; width <= 16384; beam 24')
    return len(candidates)


def reconcile_project(p, mapping, keys, sheet):
    p['reconciliation'] = reconcile(p['ctl'], data_of(p), p['sheet'], mapping, keys, sheet, **options(p))
    lines = lines_of(p)
    for c in p['reconciliation']['conflicts']:
        row = lines[c['record'] - 1][0]
        f = resolve_field(p['ctl'], row, c['field'], p['encoding'])
        try:
            edited = edit_record(p['ctl'], data_of(p), c['record'], c['field'], c['value'], **options(p))
            new_row = physical_lines(p['ctl'], edited, p['record_format'], p['record_length'])[c['record'] - 1][0]
            report = inspect_record(compile_ctl(p['ctl']), new_row, c['record'], p['encoding'])
            old_report = inspect_record(compile_ctl(p['ctl']), row, c['record'], p['encoding'])
            if any(x['field'] == f.name for x in report['findings']) or set(report['issues']) - set(old_report['issues']) or report['branches'] != old_report['branches']: continue
        except ValueError: continue
        pid = checksum((str(c['record']) + c['field'] + c['source_reference'] + c['value'] + checksum(row)).encode())[:24]
        features = {'preservation': 0., 'datatype': 1., 'routing': 1., 'spreadsheet': 1., 'edit_cost': 0., 'cross_record': 0.}
        score, breakdown = score_candidate(features, p['weights'])
        proposal = {'id': pid, 'record': c['record'], 'kind': 'semantic_reconciliation', 'source': 'SPREADSHEET', 'field': c['field'],
                    'original_value': c['original_value'], 'proposed_value': c['value'], 'start': f.start, 'end': f.end,
                    'record_checksum': checksum(row), 'record_version': p.get('record_versions', {}).get(str(c['record']), 1), 'data_version': p['data_version'], 'candidate_score': score,
                    'score_breakdown': breakdown, 'requires_approval': True, 'status': 'PENDING_REVIEW', 'evidence': [c],
                    'reason': 'Exact unique key match in the imported sheet conflicts with the current DAT. The DAT is preserved until approval.',
                    'expected_serialized_value': c['value'].ljust(f.width), 'destinations': report['branches']}
        if not any(x['id'] == pid for x in p['proposals']): p['proposals'].append(proposal)
    event(p, 'spreadsheet reconciled', matches=p['reconciliation']['matched_records'], conflicts=len(p['reconciliation']['conflicts']))


def apply_proposals(p, ids, overrides=None):
    if len(set(ids)) != len(ids) or not ids: raise ValueError('Choose distinct proposals')
    proposals = []
    for pid in ids:
        proposal = next((x for x in p['proposals'] if x['id'] == pid), None)
        if not proposal or proposal['status'] not in ('PROPOSED', 'PENDING_REVIEW'): raise Conflict('Proposal is no longer pending')
        proposals.append(proposal)
    # One structural candidate per record; semantic patches may share a record but not overlap.
    rows = [x['record'] for x in proposals if 'candidate_hex' in x]
    if len(set(rows)) != len(rows) or any(x['record'] in rows and 'candidate_hex' not in x for x in proposals):
        raise ValueError('Alternative reconstructions of a record must be reviewed separately')
    lines, new = lines_of(p), data_of(p)
    occupied = {}
    for proposal in proposals:
        row = lines[proposal['record'] - 1][0]
        if checksum(row) != proposal['record_checksum'] or proposal.get('record_version', 1) != p.get('record_versions', {}).get(str(proposal['record']), 1): raise Conflict('Underlying record changed; analyze again before approval')
        if 'candidate_hex' in proposal:
            candidate = bytes.fromhex(proposal['candidate_hex'])
            schema = compile_ctl(p['ctl']); matches = [b['index'] for b in inspect_record(schema, row, proposal['record'], p['encoding'])['branches']]
            if not hard_constraints(schema, row, candidate, p['encoding'], matches): raise ValueError('Candidate no longer satisfies hard constraints')
            current = physical_lines(p['ctl'], new, p['record_format'], p['record_length'])
            current[proposal['record'] - 1] = (candidate, current[proposal['record'] - 1][1]); new = b''.join(a + e for a, e in current)
        else:
            f = resolve_field(p['ctl'], row, proposal['field'], p['encoding'])
            for start, end in occupied.get(proposal['record'], []):
                if max(start, f.start) <= min(end, f.end): raise ValueError('Bulk proposals overlap')
            occupied.setdefault(proposal['record'], []).append((f.start, f.end))
            value = (overrides or {}).get(proposal['id'], proposal['proposed_value'])
            new = edit_record(p['ctl'], new, proposal['record'], proposal['field'], value, **options(p))
            new_row = physical_lines(p['ctl'], new, p['record_format'], p['record_length'])[proposal['record'] - 1][0]
            report = inspect_record(compile_ctl(p['ctl']), new_row, proposal['record'], p['encoding'])
            previous_report = inspect_record(compile_ctl(p['ctl']), row, proposal['record'], p['encoding'])
            if any(x['field'] == f.name for x in report['findings']) or set(report['issues']) - set(previous_report['issues']) or report['branches'] != previous_report['branches']: raise ValueError('Proposed value fails deterministic field validation or changes routing')
            proposal['approved_value'] = value
            proposal['expected_serialized_value'] = value.ljust(f.width)
        proposal['status'] = 'APPROVED'; event(p, 'proposal approved', proposal_id=proposal['id'], record=proposal['record'], record_checksum=checksum(row))
    commit_bytes(p, new, 'approved proposals applied', ids)
    # Approval of a no-op still completes the state transition.
    for proposal in proposals: proposal['status'] = 'APPLIED'


def record_detail(p, n, layout_branch=None):
    lines, original = lines_of(p), lines_of(p, True)
    if not 1 <= n <= len(lines): raise ValueError('record out of range')
    row, ending = lines[n - 1]; old = original[n - 1][0]
    schema = compile_ctl(p['ctl']); info = inspect_record(schema, row, n, p['encoding'])
    fields = []
    selection = selected_fields(p['ctl'], row, p['encoding'])
    if layout_branch is not None:
        if not 0 <= layout_branch < len(schema.branches): raise ValueError('Unknown assumed layout')
        b = schema.branches[layout_branch]
        selection = [(layout_branch, b, f) for f in b.fields]
    for bi, b, f in selection:
        raw = row[f.start - 1:f.end] if f.start else b''; orig = old[f.start - 1:f.end] if f.start else b''
        fields.append({**f.to_dict(), 'branch': bi, 'table': b.table, 'raw': raw.decode(p['encoding']),
                       'original': orig.decode(p['encoding']), 'value': field_value(f, row, b, schema.branches, p['encoding']),
                       'hypothetical_layout': layout_branch is not None and layout_branch not in [m['index'] for m in info['branches']],
                       'pending_proposal': any(x['record'] == n and x.get('field') == f.name and x['status'] in ('PROPOSED', 'PENDING_REVIEW') for x in p.get('proposals', [])),
                       'modified': raw != orig, 'missing_bytes': max(0, f.end - len(row)), 'readonly': not f.start or bool(f.sql_expression)})
    return {**info, 'fields': fields, 'raw': row[:16384].decode(p['encoding']), 'original_raw': old[:16384].decode(p['encoding']),
            'hex': row[:16384].hex(), 'original_hex': old[:16384].hex(), 'preview_truncated': len(row) > 16384,
            'line_ending': {b'\r\n': 'CRLF', b'\n': 'LF', b'\r': 'CR', b'': 'none'}[ending], 'checksum': checksum(row)}


def ai_context(p, capability, record, share_values=False):
    schema = compile_ctl(p['ctl'])
    if capability == 'schema': return {'branches': schema.to_dict(), 'unresolved': [x for b in schema.branches for x in b.unsupported] + schema.diagnostics}
    if capability == 'mapping':
        return {'allowed_fields': sorted({f.name for b in schema.branches for f in b.fields}),
                'sheets': [{'name': s['name'], 'headers': s['headers']} for s in p['sheet'].get('sheets', [])]}
    detail = record_detail(p, record)
    unresolved = detail['issues'] or [c for c in p['reconciliation'].get('conflicts', []) if c['record'] == record]
    if capability in ('recover', 'investigate') and not unresolved: return None
    evidence = [e for e in p['reconciliation'].get('evidence', []) if e['record'] == record] if share_values else []
    candidates = [x for x in p['proposals'] if x['record'] == record and x['status'] in ('PROPOSED', 'PENDING_REVIEW')][:5]
    return {'record': record, 'findings': detail['issues'], 'classification': detail['classification'], 'destinations': detail['branches'],
            'fields': [{'name': f['name'], 'start': f['start'], 'end': f['end'], 'datatype': f['dtype'],
                        'value': f['value'] if share_values else '[MASKED]', 'width': f['width']} for f in detail['fields'][:120]],
            'evidence': evidence[:100], 'deterministic_candidates': [{'id': x['id'], 'score': x['candidate_score'],
                'changes': x.get('changes', []) if share_values else [{'field': c['field'], 'start': c['start'], 'end': c['end']} for c in x.get('changes', [])]} for x in candidates],
            'values_shared_by_user': share_values}


def accept_ai_result(p, run, capability, record, share_values):
    result = run['result']
    if capability != 'recover' and result['proposals']: raise AIError('UNAUTHORIZED_OUTPUT', 'This capability cannot propose data modifications')
    if capability != 'mapping' and result['mappings']: raise AIError('UNAUTHORIZED_OUTPUT', 'This capability cannot propose mappings')
    if result['status'] == 'INSUFFICIENT_EVIDENCE' and (result['proposals'] or result['mappings']): raise AIError('INVALID_OUTPUT', 'Insufficient-evidence response cannot contain changes')
    names = {f.name for b in compile_ctl(p['ctl']).branches for f in b.fields}
    for m in result['mappings']:
        sheet = next((s for s in p['sheet'].get('sheets', []) if s['name'] == m['sheet']), None)
        if not sheet or m['header'] not in sheet['headers'] or m['field_name'] not in names: raise AIError('INVALID_MAPPING', 'AI mapping refers to an unknown field or header')
    pending = []
    physical = lines_of(p)
    if result['proposals'] and not 1 <= record <= len(physical): raise AIError('UNAUTHORIZED_OUTPUT', 'AI proposal refers to an out-of-scope record')
    row = physical[record - 1][0] if result['proposals'] else b''
    for proposal in result['proposals']:
        if not share_values or proposal['record'] != record: raise AIError('UNAUTHORIZED_OUTPUT', 'AI proposal is outside the selected record or sharing scope')
        try: f = resolve_field(p['ctl'], row, proposal['field_name'], p['encoding'])
        except ValueError: raise AIError('UNKNOWN_FIELD', 'AI proposal refers to an unknown or ambiguous field') from None
        old = row[f.start - 1:f.end].decode(p['encoding']).strip(' ')
        if proposal['original_value'] != old: raise AIError('STALE_OUTPUT', 'AI original value does not match the current DAT')
        allowed = [e for e in p['reconciliation'].get('evidence', []) if e['record'] == record and e['field'] == f.name and e['value'] == proposal['proposed_value']]
        if not allowed or any(not any(e['source_reference'] == r['source_reference'] and e['value'] == r['matched_value'] == proposal['proposed_value'] for e in allowed) for r in proposal['evidence']):
            raise AIError('INSUFFICIENT_EVIDENCE', 'AI proposed value is not backed by exact key-matched source evidence')
        try:
            new = edit_record(p['ctl'], data_of(p), record, f.name, proposal['proposed_value'], **options(p))
            new_row = physical_lines(p['ctl'], new, p['record_format'], p['record_length'])[record - 1][0]
            diag = inspect_record(compile_ctl(p['ctl']), new_row, record, p['encoding'])
            old_diag = inspect_record(compile_ctl(p['ctl']), row, record, p['encoding'])
            if diag['issues'] or diag['branches'] != old_diag['branches']: raise ValueError()
        except ValueError: raise AIError('CONSTRAINT_VIOLATION', 'AI proposal violates field constraints or changes routing') from None
        score, breakdown = score_candidate({'preservation': 0., 'datatype': 1., 'routing': 1., 'spreadsheet': 1., 'edit_cost': 0., 'cross_record': 0.}, p['weights'])
        pending.append({'id': secrets.token_hex(12), 'record': record, 'kind': 'semantic_recovery', 'source': run.get('provider', 'BEDROCK'),
                        'field': f.name, 'start': f.start, 'end': f.end, 'original_value': old, 'proposed_value': proposal['proposed_value'],
                        'evidence': allowed, 'reason': proposal['reason'], 'confidence_label': proposal['confidence_label'],
                        'requires_approval': True, 'status': 'PENDING_REVIEW', 'candidate_score': score, 'score_breakdown': breakdown,
                        'record_checksum': checksum(row), 'record_version': p.get('record_versions', {}).get(str(record), 1), 'data_version': p['data_version'],
                        'expected_serialized_value': proposal['proposed_value'].ljust(f.width), 'destinations': diag['branches']})
    p['proposals'].extend(pending)
    p['ai_runs'].append({**run, 'timestamp': now(), 'record': record, 'data_version': p['data_version']})
    event(p, 'AI review completed', provider=run.get('provider', 'BEDROCK'), capability=capability, record=record, proposals=len(pending), usage=run['usage'], estimated_cost_usd=run['estimated_cost_usd'])


def verify_generation(p):
    generated, report = round_trip(p['ctl'], data_of(p), **options(p))
    current, original, schema = lines_of(p), lines_of(p, True), compile_ctl(p['ctl'])
    comparisons = []
    for n, ((row, ending), (old, old_ending)) in enumerate(zip(current, original), 1):
        if row == old and ending == old_ending: continue
        before = inspect_record(schema, old, n, p['encoding']); after = inspect_record(schema, row, n, p['encoding'])
        fields = {(f.name, f.start, f.end): f for _, _, f in selected_fields(p['ctl'], row, p['encoding']) if f.start}
        changes = [{'field': f.name, 'start': f.start, 'end': f.end, 'original_raw': old[f.start - 1:f.end].decode(p['encoding']),
                    'serialized_raw': row[f.start - 1:f.end].decode(p['encoding'])} for f in fields.values() if old[f.start - 1:f.end] != row[f.start - 1:f.end]]
        comparisons.append({'record': n, 'original_checksum': checksum(old), 'serialized_checksum': checksum(row),
                            'original_length': len(old), 'serialized_length': len(row), 'routing_before': before['branches'],
                            'routing_after': after['branches'], 'field_changes': changes, 'line_ending_preserved': ending == old_ending})
    approval_checks = []
    for proposal in p['proposals']:
        if proposal['status'] != 'APPLIED': continue
        row = current[proposal['record'] - 1][0]
        if 'candidate_hex' in proposal: matches = row == bytes.fromhex(proposal['candidate_hex'])
        else:
            f = resolve_field(p['ctl'], row, proposal['field'], p['encoding'])
            matches = row[f.start - 1:f.end] == proposal['expected_serialized_value'].encode(p['encoding'])
        approval_checks.append({'proposal_id': proposal['id'], 'record': proposal['record'], 'approved_bytes_match': matches})
        if not matches: raise ValueError('Approved value does not match physical serialized bytes')
    report.update(original_checksum=checksum(decode(p['original'])), record_comparisons=comparisons,
                  approval_checks=approval_checks, unchanged_records=len(current) - len(comparisons),
                  original_round_trip_identical=b''.join(row + ending for row, ending in original) == decode(p['original']))
    return generated, supplement_diagnostics(p, report)


def supplement_diagnostics(p, report):
    reconciliation = p.get('reconciliation', {})
    warnings = []
    for c in reconciliation.get('conflicts', []):
        warnings.append({'record': c['record'], 'field': c['field'], 'source_reference': c['source_reference'],
                         'message': f'{c["field"]}: spreadsheet value conflicts with DAT ({c["source_reference"]}); DAT preserved until approval',
                         'classification': 'semantic_inconsistency', 'severity': 'warning'})
    for group in reconciliation.get('duplicate_dat_keys', []):
        for n in group['records']:
            warnings.append({'record': n, 'message': 'Duplicate DAT join key; supplemental matching is ambiguous',
                             'classification': 'semantic_inconsistency', 'severity': 'warning'})
    warnings.extend({'message': 'Field specification conflicts with CTL: ' + c['field'], 'source_reference': c['source_reference'],
                     'classification': 'semantic_inconsistency', 'severity': 'warning'} for c in reconciliation.get('spec_conflicts', []))
    if not warnings: return report
    result = {**report, 'records': [{**r, 'issues': r['issues'][:], 'findings': r['findings'][:]} for r in report['records']], 'counts': report['counts'].copy(), 'validation_warnings': warnings}
    for record in result['records']:
        relevant = [w for w in warnings if w.get('record') == record['record']]
        if not relevant: continue
        if record['classification'] == 'valid':
            result['counts']['valid'] -= 1; result['counts']['semantic_inconsistency'] += 1
            record['classification'] = 'semantic_inconsistency'
        record['issues'].extend(w['message'] for w in relevant)
        record['findings'].extend(relevant); record['status'] = 'needs_review'
    result['records_with_findings'] = sum(bool(r['issues']) for r in result['records'])
    if report['export_status'] == 'structurally_validated': result['export_status'] = 'validated_with_warnings'
    return result
