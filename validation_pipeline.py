"""Compile approved source specifications into ordered, executable byte checks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from engine import DATE_FORMATS
from specs import SpecError, _format, _layouts, _route, normalize_spec, validate_spec


@dataclass(frozen=True)
class RuleStep:
    field_id: str
    code: str
    start: int  # inclusive, one-based byte position
    end: int
    argument: object = None


@dataclass(frozen=True)
class LayoutPipeline:
    id: str
    width: int
    when: tuple[dict, ...]
    steps: tuple[RuleStep, ...]


@dataclass(frozen=True)
class ValidationPipeline:
    version: int
    encoding: str
    layouts: tuple[LayoutPipeline, ...]
    coverage_limitations: tuple[dict, ...]


# Every non-metadata constraint accepted by the normalized specification is listed here.
_RULE_KEYS = {'mapping_type', 'start', 'end', 'length', 'required', 'layout_id', 'datatype',
              'conversion', 'date_format', 'padding', 'constant', 'allowed_values',
              'allow_internal_spaces', 'allow_internal_whitespace', 'shared_source',
              'filler_value', 'unsupported_conversion'}


def compile_pipeline(spec: dict) -> ValidationPipeline:
    """Reject unresolved/unsupported constraints before producing a runnable plan."""
    spec = normalize_spec(spec)
    errors = [item for item in validate_spec(spec) if item.get('severity', 'error') == 'error']
    for field in spec['fields']:
        extra = set(field['rules']) - _RULE_KEYS
        if extra:
            errors.append({'code': 'UNSUPPORTED_RULE', 'field_id': field['id'],
                           'message': 'Unsupported rule keys: ' + ', '.join(sorted(extra)), 'severity': 'error'})
    if errors:
        raise SpecError('Specification has unresolved or unsupported validation rules', errors)
    by_id = {field['id']: field for field in spec['fields']}
    layouts = []
    for layout in _layouts(spec):
        steps = []
        for identifier in layout['field_ids']:
            field = by_id[identifier]
            rule = field['rules']
            if rule['mapping_type'] == 'system' or (rule['mapping_type'] == 'constant' and not rule.get('start')):
                continue
            start, end = rule['start'], rule['end']
            def add(code, argument=None):
                steps.append(RuleStep(identifier, code, start, end, argument))
            add('WIDTH', rule['length'])
            add('MANDATORY_UNVERIFIED' if rule['required'] is None else 'REQUIRED', rule['required'])
            add('CONTROL_CHARACTERS')
            if rule.get('allow_internal_spaces', rule.get('allow_internal_whitespace')) is False:
                add('INTERNAL_WHITESPACE')
            add('PADDING', {'length': rule['length'], 'padding': rule['padding'],
                            'allow_internal_spaces': rule.get('allow_internal_spaces', rule.get('allow_internal_whitespace'))})
            if rule['mapping_type'] == 'filler' and rule.get('filler_value'):
                add('FILLER', rule['filler_value'])
            if rule.get('constant') is not None: add('CONSTANT', rule['constant'])
            if rule.get('allowed_values') is not None: add('ALLOWED_VALUES', (tuple(rule['allowed_values']), rule['required']))
            if rule['datatype'] != 'char': add('DATATYPE', (rule['datatype'], rule.get('date_format')))
            if rule['conversion'] == 'to_number' and rule['datatype'] == 'char': add('CONVERSION', 'to_number')
        layouts.append(LayoutPipeline(layout['id'], layout['record_length'], tuple(layout.get('when', [])), tuple(steps)))
    return ValidationPipeline(spec.get('version', 1), spec['options'].get('encoding', 'ascii'), tuple(layouts),
                              tuple(spec.get('coverage_limitations', [])))


def _execute(step: RuleStep, data: bytes, value: str, text: str, encoding: str):
    code, arg = step.code, step.argument
    if code == 'WIDTH': return len(data) == arg
    if code == 'MANDATORY_UNVERIFIED': return None
    if code == 'REQUIRED': return bool(value) or not arg
    if code == 'CONTROL_CHARACTERS': return all(ord(c) >= 32 and ord(c) != 127 for c in text)
    if code == 'INTERNAL_WHITESPACE': return not any(c.isspace() for c in value)
    if code == 'PADDING':
        try: return _format(value, arg, encoding)[0] == data
        except SpecError: return False
    if code == 'CONSTANT': return value == arg
    if code == 'FILLER': return value == arg
    if code == 'ALLOWED_VALUES':
        choices, required = arg
        return value in choices or (not value and not required)
    if code == 'DATATYPE':
        if not value: return True
        datatype, mask = arg
        if datatype == 'integer': return bool(re.fullmatch(r'[+-]?[0-9]+', value))
        if datatype in ('decimal', 'float'):
            pattern = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)' + (r'(?:[eE][+-]?[0-9]+)?' if datatype == 'float' else '')
            if not re.fullmatch(pattern, value): return False
            try: return Decimal(value).is_finite()
            except InvalidOperation: return False
        if datatype == 'date':
            try:
                if len(value) != len(mask): return False
                datetime.strptime(value, DATE_FORMATS[mask])
                return True
            except (ValueError, KeyError): return False
    if code == 'CONVERSION':
        if not value: return True
        try: return Decimal(value).is_finite()
        except InvalidOperation: return False
    raise SpecError('Unsupported compiled rule: ' + code)


def validate_record(pipeline: ValidationPipeline, row: bytes, layout_id: str | None = None) -> dict:
    """Validate exact serialized record bytes; never formats or changes them."""
    if not isinstance(row, bytes): raise SpecError('Record must be bytes')
    try:
        matches = [layout for layout in pipeline.layouts if _route({'when': layout.when}, row, pipeline.encoding) is True]
    except UnicodeDecodeError:
        return {'valid': False, 'layout_id': None, 'checks': [], 'issues': [{'code': 'ENCODING'}]}
    layout = next((item for item in pipeline.layouts if item.id == layout_id), None) if layout_id else (matches[0] if len(matches) == 1 else None)
    if layout is None or len(matches) != 1 or matches[0].id != layout.id:
        return {'valid': False, 'layout_id': None, 'checks': [], 'issues': [{'code': 'AMBIGUOUS_LAYOUT' if len(matches) > 1 else 'UNKNOWN_LAYOUT'}]}
    checks = [{'field_id': None, 'code': 'RECORD_LENGTH', 'start': 1, 'end': layout.width, 'passed': len(row) == layout.width}]
    try:
        decoded = {(step.start, step.end): row[step.start - 1:step.end].decode(pipeline.encoding, 'strict') for step in layout.steps}
    except UnicodeDecodeError:
        return {'valid': False, 'layout_id': layout.id, 'checks': checks, 'issues': [{'code': 'ENCODING'}]}
    for step in layout.steps:
        data = row[step.start - 1:step.end]
        text = decoded[step.start, step.end]
        passed = _execute(step, data, text.strip(' '), text, pipeline.encoding)
        checks.append({'field_id': step.field_id, 'code': step.code, 'start': step.start, 'end': step.end, 'passed': passed})
    issues = [check for check in checks if check['passed'] is not True]
    return {'valid': not issues, 'layout_id': layout.id, 'checks': checks, 'issues': issues,
            'coverage_limitations': list(pipeline.coverage_limitations)}
