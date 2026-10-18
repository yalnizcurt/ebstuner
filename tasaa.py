"""Conservative byte alignment of approved fixed-width records.

Scores rank mappings. Only exact approved values, strict format constraints,
and complete byte provenance can justify a structural repair; ambiguous spaces
remain attached to the source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from time import perf_counter
import re

from engine import DATE_FORMATS
from specs import _field_checks

# Part 6 G Ambiguity Certification Classifications
POSITIONALLY_COMPLIANT = 'POSITIONALLY_COMPLIANT'
BEST_RANKED_CANDIDATE = 'BEST_RANKED_CANDIDATE'
UNIQUE_WITHIN_SEARCH = 'UNIQUE_WITHIN_SEARCH'
CERTIFIED_SAFE_REPAIR = 'CERTIFIED_SAFE_REPAIR'
HUMAN_REVIEW_REQUIRED = 'HUMAN_REVIEW_REQUIRED'
SPEC_CONFLICT = 'SPEC_CONFLICT'
UNRECOVERABLE = 'UNRECOVERABLE'


@dataclass(frozen=True)
class AlignmentOptions:
    beam_width: int = 64
    local_window: int = 4
    max_states: int = 100000
    max_candidates_per_state: int = 256
    max_anchor_matches: int = 10000


@dataclass(frozen=True)
class AlignmentState:
    field_index: int
    source_cursor: int
    previous_displacement: int
    score: float
    predecessor_id: int | None
    candidate_id: int | None


def _signature(field: dict, sequence: int = 0) -> dict:
    """Compile an approved specification field into an ordered physical field signature."""
    r = field['rules']
    pad = r.get('padding') or {'side': 'right', 'char': ' '}
    side = pad.get('side', 'right')
    alignment = 'left' if side == 'right' else ('right' if side == 'left' else 'none')
    req = r.get('required')
    mandatory_status = 'YES' if req is True else ('NO' if req is False else 'UNKNOWN')
    byte_start = r['start'] - 1  # 0-based, inclusive
    byte_end = r['end']          # 0-based, end-exclusive
    return {
        'id': field['id'],
        'field_id': field['id'],
        'name': field.get('field_name', ''),
        'field_name': field.get('field_name', ''),
        'source': field.get('source', {}),
        'source_ref': field.get('source_reference') or field.get('source', {}),
        'sequence': sequence,
        'start': r['start'],
        'end': r['end'],
        'byte_start': byte_start,
        'byte_end': byte_end,
        'target_start': byte_start,
        'target_end': byte_end,
        'length': r['length'],
        'width': r['length'],
        'datatype': r.get('datatype', 'char'),
        'padding': pad,
        'alignment': alignment,
        'alignment_direction': alignment,
        'required': req,
        'mandatory_status': mandatory_status,
        'constant': r.get('constant'),
        'allowed_values': r.get('allowed_values'),
        'date_format': r.get('date_format'),
        'validation_formats': {'date_format': r.get('date_format'), 'conversion': r.get('conversion')},
        'conditional_rules': field.get('conditional_rules', ''),
        'anchor': r.get('constant') is not None or bool(r.get('allowed_values')),
    }


def _values(field: dict, encoding: str, layout_when: list[dict] | None = None) -> list[bytes]:
    r = field['rules']
    values = []
    if r.get('constant') is not None:
        values.append(r['constant'])
    elif r.get('allowed_values'):
        values.extend(r['allowed_values'])
    if layout_when:
        for cond in layout_when:
            if isinstance(cond, dict) and (cond.get('field') == field['id'] or cond.get('field') == field.get('field_name')):
                val = cond.get('value')
                if isinstance(val, str) and val and val not in values:
                    values.append(val)
    return [v.encode(encoding) for v in values if isinstance(v, str) and v]


def _has_exact_boundary(field: dict, encoding: str, layout_when: list[dict] | None = None) -> bool:
    r = field['rules']
    if _values(field, encoding, layout_when):
        return True
    if r.get('mapping_type') == 'filler':
        return True
    if r.get('datatype') == 'date' and r.get('date_format') in DATE_FORMATS:
        return True
    return False


class AnchorDetector:
    def __init__(self, fields: list[dict], row: bytes, encoding: str, max_matches: int = 10000, layout_when: list[dict] | None = None):
        self.by_field: dict[str, list[dict]] = {}
        self.all: list[dict] = []
        self.truncated = False

        for field in fields:
            r = field['rules']
            found: list[dict] = []

            # 1. Exact Constants, Routing Record Identifiers, and Allowed Values
            is_constant = r.get('constant') is not None
            is_routing_marker = False
            if layout_when:
                for cond in layout_when:
                    if isinstance(cond, dict) and (cond.get('field') == field['id'] or cond.get('field') == field.get('field_name')):
                        is_routing_marker = True
                        break
            name_upper = str(field.get('field_name', '')).upper()
            if any(marker in name_upper for marker in ('RECORD_TYPE', 'RECORD_ID', 'PROCESS_CODE', 'BATCH_TYPE')):
                is_routing_marker = True

            for value in _values(field, encoding, layout_when):
                if len(value) > r['length']:
                    continue
                at = row.find(value)
                while at >= 0:
                    if len(self.all) + len(found) >= max_matches:
                        self.truncated = True
                        break
                    anchor_type = ('RECORD_IDENTIFIER' if is_routing_marker else
                                   'EXACT_CONSTANT' if is_constant else 'ALLOWED_VALUE')
                    found.append({
                        'fieldId': field['id'],
                        'sourceStart': at,
                        'sourceEnd': at + len(value),
                        'anchorType': anchor_type,
                        'matchedValue': value.decode(encoding, errors='replace'),
                        'reliability': 'HIGH',
                        'evidence': [f'Exact approved {anchor_type.lower().replace("_", " ")}'],
                    })
                    at = row.find(value, at + 1)

            # 2. Strict Date representations
            if not found and r.get('datatype') == 'date' and r.get('date_format') in DATE_FORMATS:
                mask = r['date_format']
                mask_len = len(mask)
                for at in range(max(0, len(row) - mask_len + 1)):
                    raw = row[at:at + mask_len]
                    try:
                        text = raw.decode(encoding)
                        datetime.strptime(text, DATE_FORMATS[mask])
                    except (UnicodeError, ValueError):
                        continue
                    found.append({
                        'fieldId': field['id'],
                        'sourceStart': at,
                        'sourceEnd': at + mask_len,
                        'anchorType': 'STRICT_DATE',
                        'matchedValue': text,
                        'reliability': 'MEDIUM',
                        'evidence': [f'Matches approved date format ({mask})'],
                    })
                    if len(self.all) + len(found) >= max_matches:
                        self.truncated = True
                        break

            # 3. Numeric formats (integers and decimals)
            elif not found and r.get('datatype') in ('integer', 'decimal', 'float'):
                if r['datatype'] == 'integer':
                    for match in re.finditer(rb'[+-]?[0-9]+', row):
                        if len(match.group()) > r['length']:
                            continue
                        found.append({
                            'fieldId': field['id'],
                            'sourceStart': match.start(),
                            'sourceEnd': match.end(),
                            'anchorType': 'NUMERIC_INTEGER',
                            'matchedValue': match.group().decode('ascii', errors='replace'),
                            'reliability': 'LOW',
                            'evidence': ['Numeric integer shape; ownership unproven'],
                        })
                        if len(self.all) + len(found) >= max_matches:
                            self.truncated = True
                            break
                elif r['datatype'] in ('decimal', 'float'):
                    for match in re.finditer(rb'[+-]?[0-9]+\.[0-9]+', row):
                        if len(match.group()) > r['length']:
                            continue
                        found.append({
                            'fieldId': field['id'],
                            'sourceStart': match.start(),
                            'sourceEnd': match.end(),
                            'anchorType': 'NUMERIC_DECIMAL',
                            'matchedValue': match.group().decode('ascii', errors='replace'),
                            'reliability': 'LOW',
                            'evidence': ['Numeric decimal shape; ownership unproven'],
                        })
                        if len(self.all) + len(found) >= max_matches:
                            self.truncated = True
                            break

            self.by_field[field['id']] = found
            self.all.extend(found)

        # 4. Detect traceable neighboring field relationships
        for i in range(len(fields) - 1):
            curr_field = fields[i]
            next_field = fields[i + 1]
            curr_anchors = self.by_field.get(curr_field['id'], [])
            next_anchors = self.by_field.get(next_field['id'], [])
            for c_anc in curr_anchors:
                for n_anc in next_anchors:
                    gap = n_anc['sourceStart'] - c_anc['sourceEnd']
                    if 0 <= gap <= max(curr_field['rules']['length'], next_field['rules']['length']):
                        c_anc['evidence'].append(f'Consistent sequence with downstream {next_field["id"]}')
                        n_anc['evidence'].append(f'Consistent sequence with upstream {curr_field["id"]}')


def _checks(field: dict, data: bytes, encoding: str) -> list[dict]:
    try:
        checks = _field_checks(field, data, encoding)['checks']
        return [{**check, 'code': 'TYPE_MISMATCH' if check['code'] == 'DATATYPE' and check['passed'] is False else check['code']}
                for check in checks]
    except (UnicodeError, ValueError):
        return [{'code': 'ENCODING', 'passed': False, 'message': 'Bytes cannot be decoded'}]


def _interpret(field: dict, source: bytes, start: int, encoding: str, layout_when: list[dict] | None = None):
    """Yield output bytes and exact source classifications; never trim free CHAR."""
    r, width = field['rules'], field['rules']['length']
    pad = r['padding']['char'].encode(encoding)
    side = r['padding']['side']
    if len(pad) != 1:
        return

    yielded = False
    values = _values(field, encoding, layout_when)
    if values:
        for value in values:
            if len(value) > width:
                continue
            if side == 'right' and source.startswith(value) and source[len(value):] == pad * (len(source) - len(value)):
                output = value + pad * (width - len(value))
                value_offsets = tuple(range(start, start + len(value)))
                padding_offsets = tuple(range(start + len(value), start + len(source)))
                left_insert = 0
            elif side == 'left' and source.endswith(value) and source[:len(source) - len(value)] == pad * (len(source) - len(value)):
                output = pad * (width - len(value)) + value
                value_offsets = tuple(range(start + len(source) - len(value), start + len(source)))
                padding_offsets = tuple(range(start, start + len(source) - len(value)))
                left_insert = max(0, width - len(source))
            elif side == 'none' and source == value and len(source) == width:
                output, value_offsets, padding_offsets, left_insert = source, tuple(range(start, start + len(source))), (), 0
            else:
                continue
            yielded = True
            yield output, value_offsets, padding_offsets, (), left_insert, ['Exact approved value proves the boundary']

    elif r.get('datatype') == 'date' and r.get('date_format') in DATE_FORMATS:
        mask = r['date_format']
        mask_len = len(mask)
        if mask_len <= width and len(source) <= width:
            if side == 'right' and len(source) >= mask_len:
                date_bytes = source[:mask_len]
                pad_bytes = source[mask_len:]
                if pad_bytes == pad * len(pad_bytes):
                    try:
                        datetime.strptime(date_bytes.decode(encoding), DATE_FORMATS[mask])
                        output = date_bytes + pad * (width - mask_len)
                        value_offsets = tuple(range(start, start + mask_len))
                        padding_offsets = tuple(range(start + mask_len, start + len(source)))
                        yielded = True
                        yield output, value_offsets, padding_offsets, (), 0, ['Strict date format proves value and padding boundaries']
                    except (UnicodeError, ValueError):
                        pass
            elif side == 'left' and len(source) >= mask_len:
                date_bytes = source[-mask_len:]
                pad_bytes = source[:-mask_len]
                if pad_bytes == pad * len(pad_bytes):
                    try:
                        datetime.strptime(date_bytes.decode(encoding), DATE_FORMATS[mask])
                        output = pad * (width - mask_len) + date_bytes
                        value_offsets = tuple(range(start + len(source) - mask_len, start + len(source)))
                        padding_offsets = tuple(range(start, start + len(source) - mask_len))
                        left_insert = max(0, width - len(source))
                        yielded = True
                        yield output, value_offsets, padding_offsets, (), left_insert, ['Strict date format proves value and padding boundaries']
                    except (UnicodeError, ValueError):
                        pass
            elif side == 'none' and len(source) == mask_len and mask_len == width:
                try:
                    datetime.strptime(source.decode(encoding), DATE_FORMATS[mask])
                    yielded = True
                    yield source, tuple(range(start, start + len(source))), (), (), 0, ['Strict date format matches field width exactly']
                except (UnicodeError, ValueError):
                    pass

    elif r.get('mapping_type') == 'filler':
        if len(source) <= width:
            output = source + pad * (width - len(source))
            yielded = True
            yield output, (), tuple(range(start, start + len(source))), (), 0, ['Explicit filler field owns its structural padding']

    if not values and not yielded and len(source) <= width and (side != 'none' or len(source) == width):
        output = (source + pad * (width - len(source)) if side == 'right' else
                  pad * (width - len(source)) + source if side == 'left' else source)
        unresolved = tuple(start + i for i, byte in enumerate(source) if bytes([byte]) == pad and side != 'none')
        value_offsets = tuple(i for i in range(start, start + len(source)) if i not in unresolved)
        yield output, value_offsets, (), unresolved, max(0, width - len(source)) if side == 'left' else 0, [
            'Edge padding ownership is unresolved without an exact value constraint']
    elif values and not yielded and start == r['start'] - 1 and len(source) == width:
        output = (source + pad * (width - len(source)) if side == 'right' else
                  pad * (width - len(source)) + source if side == 'left' else source)
        yield output, tuple(range(start, start + len(source))), (), (), 0, [
            'Positional value conflict with declared allowed values or constant']


class CandidateGenerator:
    def __init__(self, fields: list[dict], row: bytes, anchors: AnchorDetector, options: AlignmentOptions, encoding: str, layout_when: list[dict] | None = None):
        self.fields, self.row, self.anchors, self.options, self.encoding = fields, row, anchors, options, encoding
        self.layout_when = layout_when
        self.truncated = False
        self.suffix = [0] * (len(fields) + 1)
        for i in range(len(fields) - 1, -1, -1):
            self.suffix[i] = self.suffix[i + 1] + fields[i]['rules']['length']

    def ends(self, index: int, cursor: int, previous_displacement: int = 0) -> list[int]:
        field = self.fields[index]
        if index == len(self.fields) - 1:
            return [len(self.row)]

        expected = cursor + field['rules']['length']
        delta = len(self.row) - cursor - self.suffix[index]
        window = self.options.local_window
        points = set(range(expected - window, expected + window + 1)) | {expected + delta}

        if previous_displacement != 0:
            points.add(expected + previous_displacement)

        for anchor in self.anchors.by_field.get(field['id'], []):
            points.add(anchor['sourceEnd'])
        for anchor in self.anchors.by_field.get(self.fields[index + 1]['id'], []):
            points.add(anchor['sourceStart'])

        # Propagate downstream anchors back as boundary hypotheses
        for k in range(index + 2, min(len(self.fields), index + 6)):
            downstream_anchors = self.anchors.by_field.get(self.fields[k]['id'], [])
            if downstream_anchors:
                expected_dist = sum(self.fields[j]['rules']['length'] for j in range(index, k))
                for anc in downstream_anchors:
                    shift = (anc['sourceStart'] - cursor) - expected_dist
                    if abs(shift) <= len(self.row):
                        points.add(expected + shift)

        radius = max(1, window)
        while radius < abs(delta):
            radius *= 2
            points.update((expected - radius, expected + radius))

        sorted_points = sorted(x for x in points if cursor <= x <= len(self.row))
        if len(sorted_points) > self.options.max_candidates_per_state:
            self.truncated = True
        return sorted_points[:self.options.max_candidates_per_state]

    def generate(self, index: int, cursor: int, previous_displacement: int = 0):
        field, r = self.fields[index], self.fields[index]['rules']
        for end in self.ends(index, cursor, previous_displacement):
            for output, value, padding, unresolved, left_insert, evidence in _interpret(
                    field, self.row[cursor:end], cursor, self.encoding, self.layout_when):
                checks = _checks(field, output, self.encoding)
                failed = [check for check in checks if check['passed'] is False]
                displacement = end - r['end']

                contributions: dict[str, float] = {
                    'displacement_change': abs(displacement - previous_displacement) * 3,
                    'rule_failures': len(failed) * 25,
                    'unresolved_padding': len(unresolved) * 3,
                    'structural_edit': abs(len(output) - (end - cursor)) * 2,
                }

                if displacement == previous_displacement:
                    contributions['stable_displacement'] = -2

                relevant = [a for a in self.anchors.by_field.get(field['id'], [])
                            if cursor <= a['sourceStart'] and a['sourceEnd'] <= end]
                if relevant:
                    max_reliability = max(a.get('reliability', 'LOW') for a in relevant)
                    if max_reliability == 'HIGH':
                        contributions['anchor_match'] = -10
                    elif max_reliability == 'MEDIUM':
                        contributions['anchor_match'] = -4
                    else:
                        contributions['anchor_match'] = -2
                elif self.anchors.by_field.get(field['id'], []):
                    contributions['anchor_conflict'] = 15

                yield {
                    'field_id': field['id'],
                    'source_start': cursor,
                    'source_end': end,
                    'target_start': r['start'] - 1,
                    'target_end': r['end'],
                    'value_offsets': value,
                    'padding_offsets': padding,
                    'unresolved_offsets': unresolved,
                    'left_insert': left_insert,
                    'padding_byte': r['padding']['char'].encode(self.encoding)[0],
                    'inserted_padding': max(0, len(output) - (end - cursor)),
                    'rebuilt': output,
                    'checks': checks,
                    'hard_valid': not failed,
                    'score_breakdown': contributions,
                    'score': sum(contributions.values()),
                    'anchor_evidence': relevant,
                    'padding_evidence': evidence,
                    'displacement': displacement,
                }


def _path(states: list[AlignmentState], assignments: list[dict], state_id: int) -> list[dict]:
    path = []
    while states[state_id].candidate_id is not None:
        state = states[state_id]
        path.append(assignments[state.candidate_id])
        state_id = state.predecessor_id
    return list(reversed(path))


def verify_preservation(row: bytes, output: bytes, mapping: list[dict]) -> dict | None:
    """Independent source-to-target verification, including order, disposition, and ownership."""
    source: list[dict | None] = [None] * len(row)
    written = set()

    for entry in mapping:
        values = set(entry['value_offsets'])
        pads = set(entry['padding_offsets'])
        for offset in range(entry['source_start'], entry['source_end']):
            classification = 'VALUE' if offset in values else 'VERIFIED_PADDING' if offset in pads else 'UNRESOLVED'
            target = None if classification == 'VERIFIED_PADDING' else entry['target_start'] + offset - entry['source_start'] + entry['left_insert']
            if target is not None and entry['value_offsets'] and entry['padding_offsets'] and entry['left_insert'] == 0:
                if max(entry['padding_offsets']) < min(entry['value_offsets']):
                    target = entry['target_end'] - len(entry['value_offsets']) + offset - min(entry['value_offsets'])
            if target is not None:
                if target in written or target >= len(output) or output[target] != row[offset]:
                    return None
                written.add(target)
            source[offset] = {
                'offset': offset,
                'byte': row[offset],
                'owner': entry['field_id'],
                'classification': classification,
                'target_offset': target,
                'disposition': 'declared_padding' if target is None else 'retained',
                'evidence': entry['padding_evidence'] if target is None else [],
            }

    # Handle trailing record delimiter bytes if present
    mapped_end = max((entry['source_end'] for entry in mapping), default=0)
    if mapped_end < len(row):
        trailing = row[mapped_end:]
        if trailing in (b'\r\n', b'\n', b'\r'):
            for idx, b in enumerate(trailing, start=mapped_end):
                source[idx] = {
                    'offset': idx,
                    'byte': b,
                    'owner': None,
                    'classification': 'RECORD_SEPARATOR',
                    'target_offset': None,
                    'disposition': 'retained_delimiter',
                    'evidence': ['Record delimiter'],
                }

    if any(item is None for item in source):
        return None

    inserted = []
    for i, byte in enumerate(output):
        if i in written:
            continue
        owner = next((entry for entry in mapping if entry['target_start'] <= i < entry['target_end']), None)
        if owner is None or byte != owner['padding_byte']:
            return None
        inserted.append({
            'target_offset': i,
            'byte': byte,
            'owner': owner['field_id'],
            'classification': 'INSERTED_PADDING',
            'evidence': owner['padding_evidence'],
        })

    return {'source': source, 'output': inserted, 'source_bytes_accounted_for': len(source)}


def _formal_exact_count(fields: list[dict], row: bytes, encoding: str, budget: int, layout_when: list[dict] | None = None) -> tuple[int | None, tuple | None]:
    """Count exact-boundary ownership mappings up to two across every byte boundary using exact DP."""
    if not all(_has_exact_boundary(field, encoding, layout_when) for field in fields):
        return None, None

    memo: dict[tuple[int, int], tuple[int, tuple]] = {}
    visits = [0]

    def count(index: int, cursor: int) -> tuple[int, tuple]:
        key = (index, cursor)
        if key in memo:
            return memo[key]
        visits[0] += 1
        if visits[0] > budget:
            raise TimeoutError
        if index == len(fields):
            return (1, ()) if cursor == len(row) else (0, ())

        field = fields[index]
        r = field['rules']
        matches: list[int] = []
        pad = r['padding']['char'].encode(encoding)
        if len(pad) != 1:
            return (0, ())

        values = _values(field, encoding, layout_when)
        if values:
            for value in values:
                if len(value) > r['length']:
                    continue
                if r['padding']['side'] == 'right' and row.startswith(value, cursor):
                    end = cursor + len(value)
                    while end <= len(row) and end - cursor <= len(row) and (
                            end == cursor + len(value) or row[end - 1:end] == pad):
                        matches.append(end)
                        end += 1
                        visits[0] += 1
                        if visits[0] > budget:
                            raise TimeoutError
                elif r['padding']['side'] == 'left':
                    start = cursor
                    while start <= len(row) and (start == cursor or row[start - 1:start] == pad):
                        if row.startswith(value, start):
                            matches.append(start + len(value))
                        start += 1
                        visits[0] += 1
                        if visits[0] > budget:
                            raise TimeoutError
                elif r['padding']['side'] == 'none' and row.startswith(value, cursor):
                    matches.append(cursor + len(value))

        elif r.get('datatype') == 'date' and r.get('date_format') in DATE_FORMATS:
            mask = r['date_format']
            mask_len = len(mask)
            side = r['padding']['side']
            if side == 'right' and cursor + mask_len <= len(row):
                date_bytes = row[cursor:cursor + mask_len]
                try:
                    datetime.strptime(date_bytes.decode(encoding), DATE_FORMATS[mask])
                    end = cursor + mask_len
                    while end <= len(row) and (end == cursor + mask_len or row[end - 1:end] == pad):
                        matches.append(end)
                        end += 1
                        visits[0] += 1
                        if visits[0] > budget:
                            raise TimeoutError
                except (UnicodeError, ValueError):
                    pass
            elif side == 'left':
                start = cursor
                while start + mask_len <= len(row) and (start == cursor or row[start - 1:start] == pad):
                    date_bytes = row[start:start + mask_len]
                    try:
                        datetime.strptime(date_bytes.decode(encoding), DATE_FORMATS[mask])
                        matches.append(start + mask_len)
                    except (UnicodeError, ValueError):
                        pass
                    start += 1
                    visits[0] += 1
                    if visits[0] > budget:
                        raise TimeoutError
            elif side == 'none' and cursor + mask_len <= len(row):
                try:
                    datetime.strptime(row[cursor:cursor + mask_len].decode(encoding), DATE_FORMATS[mask])
                    matches.append(cursor + mask_len)
                except (UnicodeError, ValueError):
                    pass

        elif r.get('mapping_type') == 'filler':
            width = r['length']
            end = cursor + width
            if end <= len(row):
                matches.append(end)

        total, only = 0, ()
        for end in sorted(set(matches)):
            child_count, child_path = count(index + 1, end)
            if child_count:
                total += child_count
                only = ((cursor, end),) + child_path
                if total > 1:
                    memo[key] = (2, ())
                    return memo[key]

        memo[key] = (total, only)
        return memo[key]

    try:
        return count(0, 0)
    except (TimeoutError, RecursionError):
        return None, None


def _base(row: bytes, signatures: list[dict], initial: list[dict], anchors: list[dict],
          status: str = 'needs_review', reason: str = 'Review required',
          classification: str = HUMAN_REVIEW_REQUIRED, structural_status: str = 'REVIEW_REQUIRED') -> dict:
    ledger = [{
        'offset': i,
        'byte': byte,
        'owner': None,
        'classification': 'UNRESOLVED',
        'target_offset': None,
        'disposition': 'unresolved',
    } for i, byte in enumerate(row)]
    return {
        'status': status,
        'reason': reason,
        'classification': classification,
        'structural_status': structural_status,
        'validation_status': 'INCOMPLETE',
        'signatures': signatures,
        'initial': initial,
        'alternatives': [],
        'candidates': [],
        'candidate': None,
        'ledger': ledger,
        'exhaustive': False,
        'search_scope': {},
        'certification': {
            'level': 'HUMAN_REVIEW_REQUIRED',
            'classification': classification,
            'reason': reason,
            'semantic_proof': False,
            'formal_mapping_count': None,
        },
        'field_findings': [],
        'score_breakdown': [],
        'anchor_evidence': anchors,
        'ambiguity_regions': [],
        'spec_conflicts': [],
        'suggested_repairs': [],
        'provenance': {'source': ledger, 'output': [], 'source_bytes_accounted_for': len(row)},
        'resource_limit_reached': False,
        'timings': {key: 0.0 for key in ('initial', 'anchor', 'candidate', 'search', 'certification', 'reconstruction')},
    }


def analyze(spec: dict, layout: dict, row: bytes, *, beam_width: int = 64, max_shift: int = 4, encoding: str = 'ascii', options: AlignmentOptions | None = None) -> dict:
    """Analyze a record. Byte offsets are zero-based and end-exclusive internally."""
    options = options or AlignmentOptions(beam_width=beam_width, local_window=max_shift)
    if not isinstance(row, bytes) or options.beam_width < 1 or options.local_window < 0:
        raise ValueError('Expected bytes, positive beam width and nonnegative search window')

    by_id = {field['id']: field for field in spec['fields']}
    try:
        fields = [by_id[key] for key in layout['field_ids']]
    except (KeyError, TypeError):
        raise ValueError('Layout references a missing field') from None

    fields = [field for field in fields if field['rules'].get('start') is not None]
    fields.sort(key=lambda field: field['rules']['start'])
    signatures = [_signature(field, seq) for seq, field in enumerate(fields, start=1)]

    cursor = 1
    gaps = []
    for signature in signatures:
        if (signature['start'] < cursor or signature['end'] - signature['start'] + 1 != signature['length']
                or signature['padding'].get('side') not in ('left', 'right', 'none')):
            return _base(row, signatures, [], [], 'unsupported', 'Overlapping or incomplete physical layout',
                         classification=UNRECOVERABLE, structural_status='REVIEW_REQUIRED')
        if signature['start'] > cursor:
            gaps.append((cursor - 1, signature['start'] - 1))
        cursor = signature['end'] + 1

    width = layout.get('record_length')
    if not isinstance(width, int) or width < cursor - 1:
        return _base(row, signatures, [], [], 'unsupported', 'Invalid record length or field beyond layout',
                     classification=UNRECOVERABLE, structural_status='REVIEW_REQUIRED')
    if width > cursor - 1:
        gaps.append((cursor - 1, width))
    if gaps and len(row) > width:
        gaps.append((width, len(row)))

    try:
        row.decode(encoding, 'strict')
    except UnicodeError:
        return _base(row, signatures, [], [], 'unsupported', 'Record cannot be decoded with approved encoding',
                     classification=UNRECOVERABLE, structural_status='REVIEW_REQUIRED')

    layout_when = layout.get('when')

    t = perf_counter()
    initial = []
    for field in fields:
        r = field['rules']
        checks = _checks(field, row[r['start'] - 1:r['end']], encoding)
        initial.append({
            'field_id': field['id'],
            'start': r['start'] - 1,
            'end': min(r['end'], len(row)),
            'valid': all(c['passed'] is True for c in checks),
            'checks': checks,
        })
    initial_time = perf_counter() - t

    t = perf_counter()
    anchors = AnchorDetector(fields, row, encoding, options.max_anchor_matches, layout_when=layout_when)
    anchor_time = perf_counter() - t

    base = _base(row, signatures, initial, anchors.all)
    base['timings']['initial'], base['timings']['anchor'] = initial_time, anchor_time
    base['field_findings'] = [{
        'field_id': item['field_id'],
        **c
    } for item in initial for c in item['checks'] if c['passed'] is not True]

    if gaps:
        owners: list[str | None] = [None] * len(row)
        for field in fields:
            r = field['rules']
            owners[r['start'] - 1:min(r['end'], len(row))] = [field['id']] * max(0, min(r['end'], len(row)) - r['start'] + 1)
        ledger = []
        for i, byte in enumerate(row):
            owner = owners[i]
            ledger.append({
                'offset': i,
                'byte': byte,
                'owner': owner,
                'classification': 'VALUE' if owner else 'UNRESOLVED',
                'target_offset': i if owner else None,
                'disposition': 'retained' if owner else 'unresolved',
            })
        base.update(
            status='needs_review',
            reason='Positioned layout contains source bytes without an approved owner',
            classification=HUMAN_REVIEW_REQUIRED,
            structural_status='REVIEW_REQUIRED',
            ledger=ledger,
            provenance={'source': ledger, 'output': [], 'source_bytes_accounted_for': len(row)},
            ambiguity_regions=[{'source_start': start, 'source_end': end, 'field_id': None, 'reason': 'Unowned gap'} for start, end in gaps],
            search_scope={'domain': 'Positioned fields only; alignment search blocked by unowned gaps'},
            certification={
                'level': 'HUMAN_REVIEW_REQUIRED',
                'classification': HUMAN_REVIEW_REQUIRED,
                'reason': 'Positioned layout contains source bytes without an approved owner',
                'semantic_proof': False,
                'formal_mapping_count': None,
            },
            suggested_repairs=[{
                'kind': 'SPECIFICATION_CHANGE',
                'requires_review': True,
                'reason': 'Define filler or reserved ownership for each gap',
            }],
        )
        return base

    if len(row) == width and all(item['valid'] for item in initial):
        owners = [field['id'] for field in fields for _ in range(field['rules']['length'])]
        ledger = [{
            'offset': i,
            'byte': byte,
            'owner': owners[i],
            'classification': 'VALUE',
            'target_offset': i,
            'disposition': 'retained',
        } for i, byte in enumerate(row)]
        base.update(
            status='already_valid',
            reason='Positions satisfy evaluated rules; semantic value boundaries are not proven',
            classification=POSITIONALLY_COMPLIANT,
            candidate=row,
            ledger=ledger,
            exhaustive=True,
            structural_status='POSITIONALLY_COMPLIANT',
            validation_status='PASSED',
            certification={
                'level': 'POSITIONALLY_COMPLIANT',
                'classification': POSITIONALLY_COMPLIANT,
                'reason': 'Original bytes unchanged',
                'semantic_proof': False,
                'formal_mapping_count': None,
            },
            candidates=[{
                'id': 'initial',
                'fields': [{'id': x['field_id'], 'hard_valid': True} for x in initial],
                'certified': False,
            }],
            provenance={'source': ledger, 'output': [], 'source_bytes_accounted_for': len(row)},
        )
        return base

    t = perf_counter()
    generator = CandidateGenerator(fields, row, anchors, options, encoding, layout_when=layout_when)
    states = [AlignmentState(0, 0, 0, 0.0, None, None)]
    assignments: list[dict] = []
    frontier: list[int] = [0]
    limited = False
    base['timings']['candidate'] = perf_counter() - t

    t = perf_counter()
    for index in range(len(fields)):
        next_ids: list[int] = []
        # Diverse state retention: track up to 2 best paths per (source_cursor, displacement)
        bucketed: dict[tuple[int, int], list[int]] = {}

        for state_id in frontier:
            state = states[state_id]
            for entry in generator.generate(index, state.source_cursor, state.previous_displacement):
                if len(states) >= options.max_states:
                    limited = True
                    break
                assignments.append(entry)
                new_state = AlignmentState(
                    index + 1,
                    entry['source_end'],
                    entry['displacement'],
                    state.score + entry['score'],
                    state_id,
                    len(assignments) - 1,
                )
                states.append(new_state)
                new_state_id = len(states) - 1

                sig = (new_state.source_cursor, new_state.previous_displacement)
                if sig not in bucketed:
                    bucketed[sig] = [new_state_id]
                elif len(bucketed[sig]) < 2:
                    bucketed[sig].append(new_state_id)
                else:
                    # Replace worst in bucket if this state is better
                    worst_idx = max(range(len(bucketed[sig])), key=lambda k: states[bucketed[sig][k]].score)
                    if new_state.score < states[bucketed[sig][worst_idx]].score:
                        bucketed[sig][worst_idx] = new_state_id

            if limited:
                break

        for state_list in bucketed.values():
            next_ids.extend(state_list)

        next_ids.sort(key=lambda identifier: (states[identifier].score, states[identifier].source_cursor))
        if len(next_ids) > options.beam_width:
            limited = True
        frontier = next_ids[:options.beam_width]
        if not frontier:
            break

    base['timings']['search'] = perf_counter() - t

    t = perf_counter()
    alternatives = []
    for state_id in frontier:
        if states[state_id].source_cursor != len(row):
            continue
        mapping = _path(states, assignments, state_id)
        output = b''.join(entry['rebuilt'] for entry in mapping)
        provenance = verify_preservation(row, output, mapping)
        if provenance is not None:
            alternatives.append({
                'score': states[state_id].score,
                'candidate': output,
                'mapping': [{k: v for k, v in entry.items() if k != 'rebuilt'} for entry in mapping],
                'provenance': provenance,
            })

    alternatives.sort(key=lambda x: x['score'])
    best = alternatives[0] if alternatives else None
    limited = limited or generator.truncated or anchors.truncated
    exhaustive = not limited
    unique = exhaustive and len(alternatives) == 1
    fully_owned = bool(best) and not any(x['classification'] == 'UNRESOLVED' for x in best['provenance']['source'])
    hard_valid = bool(best) and all(x['hard_valid'] for x in best['mapping'])

    exact_ownership = bool(best) and all(
        _has_exact_boundary(field, encoding, layout_when) or
        (best['mapping'][i]['source_start'] == field['rules']['start'] - 1 and
         best['mapping'][i]['source_end'] == field['rules']['end'])
        for i, field in enumerate(fields)
    )

    complete_rules = bool(best) and all(c['passed'] is not None for e in best['mapping'] for c in e['checks'])
    formal_count, formal_path = _formal_exact_count(fields, row, encoding, options.max_states, layout_when=layout_when)
    formal_unique = (formal_count == 1 and bool(best) and
                     formal_path == tuple((entry['source_start'], entry['source_end']) for entry in best['mapping']))

    if formal_count is None and all(_has_exact_boundary(field, encoding, layout_when) for field in fields):
        limited = True
        exhaustive = False
        unique = False

    repair = unique and formal_unique and fully_owned and hard_valid and complete_rules and exact_ownership and best['candidate'] != row
    base['timings']['certification'] = perf_counter() - t

    # Resolve exact Ambiguity Certifier Classification
    if repair:
        classification = CERTIFIED_SAFE_REPAIR
        status = 'certified'
        structural_status = 'STRUCTURALLY_REPAIRABLE'
        validation_status = 'PASSED'
        cert_level = 'GLOBALLY_CERTIFIED_WITHIN_FORMAL_CONSTRAINT_MODEL'
        reason = 'Unique exact-value mapping with complete byte ownership'
    elif not best or len(alternatives) == 0:
        classification = UNRECOVERABLE
        status = 'needs_review'
        structural_status = 'REVIEW_REQUIRED'
        validation_status = 'INCOMPLETE'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'No feasible mapping found within search domain'
    elif not hard_valid:
        classification = SPEC_CONFLICT
        status = 'needs_review'
        structural_status = 'SPEC_CONFLICT'
        validation_status = 'FAILED'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'Best candidate violates specification type or value constraints'
    elif limited:
        classification = HUMAN_REVIEW_REQUIRED
        status = 'needs_review'
        structural_status = 'REVIEW_REQUIRED'
        validation_status = 'INCOMPLETE'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'Search resource limit reached before certifying uniqueness'
    elif len(alternatives) > 1:
        classification = BEST_RANKED_CANDIDATE
        status = 'ambiguous'
        structural_status = 'AMBIGUOUS'
        validation_status = 'INCOMPLETE'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'Multiple plausible ownership mappings found; human review required'
    elif not complete_rules:
        classification = HUMAN_REVIEW_REQUIRED
        status = 'needs_review'
        structural_status = 'REVIEW_REQUIRED'
        validation_status = 'INCOMPLETE'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'Mandatory status is unknown; rule coverage needs review'
    elif not fully_owned:
        classification = HUMAN_REVIEW_REQUIRED
        status = 'needs_review'
        structural_status = 'REVIEW_REQUIRED'
        validation_status = 'INCOMPLETE'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'Ambiguous whitespace or padding ownership needs review'
    elif unique:
        classification = UNIQUE_WITHIN_SEARCH
        status = 'needs_review'
        structural_status = 'REVIEW_REQUIRED'
        validation_status = 'INCOMPLETE'
        cert_level = 'UNIQUE_WITHIN_BOUNDED_SEARCH'
        reason = 'Candidate is unique within search domain; global formal uniqueness unproven'
    else:
        classification = HUMAN_REVIEW_REQUIRED
        status = 'needs_review'
        structural_status = 'REVIEW_REQUIRED'
        validation_status = 'INCOMPLETE'
        cert_level = 'HUMAN_REVIEW_REQUIRED'
        reason = 'Byte ownership or rule coverage needs review'

    # If positionally compliant
    if (len(row) == cursor - 1 and best and
            all(e['source_start'] == fields[i]['rules']['start'] - 1 and
                e['source_end'] == fields[i]['rules']['end'] for i, e in enumerate(best['mapping']))):
        structural_status = 'POSITIONALLY_COMPLIANT'
        if hard_valid:
            classification = POSITIONALLY_COMPLIANT
            cert_level = 'POSITIONALLY_COMPLIANT'
            status = 'already_valid'
            validation_status = 'PASSED'
            reason = 'Original bytes occupy expected positions and pass all checks'
        else:
            classification = SPEC_CONFLICT
            validation_status = 'FAILED'

    base.update(
        status=status,
        reason=reason,
        classification=classification,
        structural_status=structural_status,
        validation_status=validation_status,
        alternatives=alternatives,
        candidates=[{
            'id': f'path-{i + 1}',
            'fields': [{
                'id': e['field_id'],
                'hard_valid': e['hard_valid'],
                'source_start': e['source_start'],
                'source_end': e['source_end'],
            } for e in alt['mapping']],
            'certified': repair and i == 0,
        } for i, alt in enumerate(alternatives)],
        candidate=best['candidate'] if repair else None,
        ledger=best['provenance']['source'] if best else base['ledger'],
        provenance=best['provenance'] if best else base['provenance'],
        exhaustive=exhaustive,
        resource_limit_reached=limited,
        certification={
            'level': cert_level,
            'classification': classification,
            'reason': reason,
            'semantic_proof': False,
            'formal_mapping_count': formal_count,
        },
        search_scope={
            'local_window': options.local_window,
            'beam_width': options.beam_width,
            'states_explored': len(states),
            'domain': 'anchor and adaptive boundary hypotheses',
        },
        score_breakdown=[{'field_id': e['field_id'], **e['score_breakdown']} for e in best['mapping']] if best else [],
        ambiguity_regions=[{
            'field_id': fields[i]['id'],
            'source_start': min(a['mapping'][i]['source_start'] for a in alternatives),
            'source_end': max(a['mapping'][i]['source_end'] for a in alternatives),
        } for i in range(len(fields)) if len({(a['mapping'][i]['source_start'], a['mapping'][i]['source_end']) for a in alternatives}) > 1],
        suggested_repairs=[{
            'candidate_id': 'path-1',
            'kind': 'STRUCTURAL_PADDING',
            'requires_review': not repair,
        }] if best and best['candidate'] != row else [],
    )

    if best:
        base['field_findings'] = [{
            'field_id': e['field_id'],
            **c
        } for e in best['mapping'] for c in e['checks'] if c['passed'] is not True]

    base['spec_conflicts'] = [
        x for x in base['field_findings']
        if x['code'] in ('TYPE_MISMATCH', 'CONSTANT', 'ALLOWED_VALUES', 'CONVERSION')
    ]
    return base
