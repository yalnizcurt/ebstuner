"""Bounded, value-preserving space insertion with explicit ranking evidence."""
from __future__ import annotations
import hashlib
import re
import time
from difflib import SequenceMatcher
from engine import compile_ctl, inspect_record, physical_lines, check_encoding, field_value, rule_match

DEFAULT_WEIGHTS = {'preservation': .30, 'datatype': .20, 'routing': .20, 'spreadsheet': .15, 'edit_cost': .10, 'cross_record': .05}


def score_candidate(features, weights=None):
    weights = weights or DEFAULT_WEIGHTS
    if set(weights) != set(DEFAULT_WEIGHTS) or any(not isinstance(x, (int, float)) or not 0 <= x <= 1 for x in weights.values()) or abs(sum(weights.values()) - 1) > 1e-8:
        raise ValueError('Scoring weights must name all six features, be nonnegative and sum to one')
    if set(features) != set(weights) or any(not 0 <= x <= 1 for x in features.values()): raise ValueError('Invalid score features')
    breakdown = {k: {'weight': weights[k], 'feature': round(features[k], 6), 'points': round(100 * weights[k] * features[k], 4)} for k in weights}
    return round(sum(x['points'] for x in breakdown.values()), 4), breakdown


def edit_distance(a, b, limit=64):
    if abs(len(a) - len(b)) > limit: return limit + 1
    # A band bounds work on long fixed-width records.
    prev = {j: j for j in range(min(len(b), limit) + 1)}
    for i, x in enumerate(a, 1):
        cur = {0: i} if i <= limit else {}
        for j in range(max(1, i - limit), min(len(b), i + limit) + 1):
            cur[j] = min(prev.get(j, limit + 1) + 1, cur.get(j - 1, limit + 1) + 1, prev.get(j - 1, limit + 1) + (x != b[j - 1]))
        prev = cur
    return prev.get(len(b), limit + 1)


def hard_constraints(schema, old, new, encoding, expected_branches):
    if re.sub(rb'[ \t]', b'', old) != re.sub(rb'[ \t]', b'', new): return False
    report = inspect_record(schema, new, 1, encoding)
    if report['issues']: return False
    intervals = [(f.start, f.end) for bi in expected_branches for f in schema.branches[bi].fields if f.start]
    if any(old[i] != new[i] and not any(a <= i + 1 <= b for a, b in intervals) for i in range(min(len(old), len(new)))): return False
    return {b['index'] for b in report['branches']} == set(expected_branches)


# shortcut: space-insertion recovery, add evidence-backed deletion/substitution search for other corruption patterns.
def reconstruct(ctl, raw, encoding='ascii', record_format=None, record_length=None, weights=None, evidence=None,
                record_numbers=None, max_records=100, beam_width=24, max_missing=64):
    encoding = check_encoding(raw, encoding); schema = compile_ctl(ctl)
    # Validate configuration even when there are no candidates.
    score_candidate({k: 0 for k in DEFAULT_WEIGHTS}, weights)
    lines = physical_lines(ctl, raw, record_format, record_length)
    proposals, visited = [], 0
    deadline = time.monotonic() + 8
    for n, (row, _) in enumerate(lines, 1):
        if time.monotonic() >= deadline: break
        if record_numbers is not None and n not in record_numbers: continue
        report = inspect_record(schema, row, n, encoding)
        if not report['issues']: continue
        visited += 1
        if visited > max_records: break
        match = [b['index'] for b in report['branches']]
        missing = report['minimum_length'] - len(row)
        if not match or report['uncertain_branches'] or report['unsupported'] or not 0 < missing <= max_missing: continue
        fields = {(f.start, f.end, f.name): f for bi in match for f in schema.branches[bi].fields if f.start}
        expected = report['minimum_length']
        if expected > 16_384 or len(fields) > 128: continue
        # Try all missing spaces at every field boundary, plus a bounded insertion beam.
        positions = sorted({len(row), *[min(len(row), f.start - 1) for f in fields.values()], *[min(len(row), f.end) for f in fields.values()]})
        pool = {row[:p] + b' ' * missing + row[p:] for p in positions}
        beam = {row}
        for _ in range(min(missing, 12) if expected <= 2048 else 0):
            if time.monotonic() >= deadline: break
            options = {x[:p] + b' ' + x[p:] for x in beam for p in positions if p <= len(x)}
            def partial_key(x):
                candidate = x.ljust(expected, b' ')
                problems = sum(len(inspect_record(schema, candidate, n, encoding)['findings']) for _ in (0,))
                return (problems, -SequenceMatcher(None, row, x, autojunk=False).ratio(), x)
            beam = set(sorted(options, key=partial_key)[:beam_width])
        pool.update(x.ljust(expected, b' ') for x in beam)
        ranked = []
        neighbors = [r for r, _ in lines[max(0, n - 4):n + 3] if len(r) >= expected and r[:1] == row[:1]]
        for candidate in sorted(pool):
            if len(candidate) != expected or not hard_constraints(schema, row, candidate, encoding, match): continue
            values = {f.name: candidate[f.start - 1:f.end].decode(encoding).strip(' ') for f in fields.values()}
            refs = [e for e in (evidence or []) if e.get('record') == n and values.get(e.get('field')) == e.get('value')]
            cost = edit_distance(row, candidate, max_missing)
            consistency = sum(any(candidate[f.start - 1:f.end] == other[f.start - 1:f.end] for other in neighbors) for f in fields.values()) / max(1, len(fields)) if neighbors else 0
            preserved_fields = sum(row[f.start - 1:f.end].strip(b' \t') == candidate[f.start - 1:f.end].strip(b' \t') for f in fields.values())
            features = {'preservation': preserved_fields / max(1, len(fields)), 'datatype': 1., 'routing': 1., 'spreadsheet': 1. if refs else 0.,
                        'edit_cost': max(0., 1 - cost / max(1, expected)), 'cross_record': consistency}
            score, breakdown = score_candidate(features, weights)
            changes = []
            for f in sorted(fields.values(), key=lambda f: (f.start, f.end, f.name)):
                before = row[f.start - 1:f.end].decode(encoding)
                after = candidate[f.start - 1:f.end].decode(encoding)
                if before != after: changes.append({'field': f.name, 'start': f.start, 'end': f.end, 'original': before, 'proposed': after})
            trailing = candidate == row.ljust(expected, b' ')
            ranked.append({'id': hashlib.sha256(str(n).encode() + row + b'\0' + candidate).hexdigest()[:24],
                           'record': n, 'kind': 'trailing_padding' if trailing else 'internal_alignment', 'source': 'DETERMINISTIC',
                           'candidate_score': score, 'score_breakdown': breakdown, 'insert_spaces': missing,
                           'requires_approval': True, 'status': 'PROPOSED', 'record_checksum': hashlib.sha256(row).hexdigest(),
                           'candidate_hex': candidate.hex(), 'original_hex': row.hex(), 'changes': changes, 'evidence': refs,
                           'reason': 'Preserves every non-whitespace source byte; satisfies supported field types and the original destination matches. Space positions remain hypotheses.',
                           'destinations': report['branches'], 'edit_distance': cost})
        ranked.sort(key=lambda x: (-x['candidate_score'], x['kind'] != 'trailing_padding', x['id']))
        separation = ranked[0]['candidate_score'] - ranked[1]['candidate_score'] if len(ranked) > 1 else None
        selected = ranked[:5]
        trailing = next((x for x in ranked if x['kind'] == 'trailing_padding'), None)
        if trailing and trailing not in selected: selected = ranked[:4] + [trailing]
        for candidate in selected:
            rank = ranked.index(candidate) + 1
            candidate.update(rank=rank, score_separation=separation, ambiguous=len(ranked) > 1 and (separation < 3 or all(f.dtype == 'CHAR' for f in fields.values())),
                             candidate_count=len(ranked), auto_repair_eligible=False)
            proposals.append(candidate)
    return proposals
