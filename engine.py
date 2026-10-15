#!/usr/bin/env python3
"""Byte-preserving fixed-width SQL*Loader compiler, validator and explicit patch CLI.

Supports the documented subset in docs/CTL_COMPATIBILITY.md. Never executes
CTL expressions, invents business values or certifies Oracle EBS load acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

from compiler import Field, Branch, compile_ctl, evaluate_condition
from datetime import datetime
from functools import lru_cache

def strip_sql_comments(source: str) -> str:
    out=[]; quote=None; i=0
    while i < len(source):
        c=source[i]
        if quote:
            out.append(c)
            if c == quote:
                if i+1<len(source) and source[i+1]==quote:
                    out.append(source[i+1]);i+=1
                else: quote=None
        elif c in ('"',"'"):
            quote=c;out.append(c)
        elif c=='-' and source[i:i+2]=='--':
            while i<len(source) and source[i] not in '\r\n': i+=1
            continue
        else: out.append(c)
        i+=1
    return ''.join(out)


def split_top_level(src: str) -> list[str]:
    parts=[];buf=[];depth=0;quote=None;i=0
    while i<len(src):
        c=src[i]
        if quote:
            buf.append(c)
            if c==quote:
                if i+1<len(src) and src[i+1]==quote:
                    i+=1;buf.append(src[i])
                else:quote=None
        elif c in ("'",'"'):
            quote=c;buf.append(c)
        elif c=='(':
            depth+=1;buf.append(c)
        elif c==')':
            depth-=1;buf.append(c)
        elif c==',' and depth==0:
            parts.append(''.join(buf).strip());buf=[]
        else:buf.append(c)
        i+=1
    if ''.join(buf).strip(): parts.append(''.join(buf).strip())
    return parts


def extract_parens(src: str, start: int) -> tuple[str,int]:
    if src[start]!='(':raise ValueError('Expected opening parenthesis')
    depth=0;quote=None;i=start
    while i<len(src):
        c=src[i]
        if quote:
            if c==quote:
                if i+1<len(src) and src[i+1]==quote: i+=1
                else:quote=None
        elif c in ("'",'"'): quote=c
        elif c=='(':depth+=1
        elif c==')':
            depth-=1
            if depth==0:return src[start+1:i],i+1
        i+=1
    raise ValueError('Unclosed CTL field list')


def parse_ctl(source: str) -> list[Branch]:
    return compile_ctl(source).branches


def rule_match(branch, row, branches, encoding='ascii'):
    return evaluate_condition(branch.when, row, branch, branches, encoding)


ENCODINGS = {'ascii': 'ascii', 'latin1': 'latin1', 'iso-8859-1': 'latin1', 'cp1252': 'cp1252'}
CHARSETS = {'US7ASCII': 'ascii', 'WE8ISO8859P1': 'latin1', 'WE8MSWIN1252': 'cp1252'}
DATE_FORMATS = {'RRMMDD': '%y%m%d', 'YYMMDD': '%y%m%d', 'MMDDRR': '%m%d%y',
                'MMDDYY': '%m%d%y', 'YYYYMMDD': '%Y%m%d', 'YYYY-MM-DD': '%Y-%m-%d',
                'DD-MON-YYYY': '%d-%b-%Y', 'DDMMYYYY': '%d%m%Y', 'MM/DD/YYYY': '%m/%d/%Y'}


def check_encoding(raw, encoding='ascii'):
    if encoding.lower() not in ENCODINGS: raise ValueError('Supported encodings: ASCII, latin1, cp1252 (single byte only)')
    encoding = ENCODINGS[encoding.lower()]
    try: raw.decode(encoding, 'strict')
    except UnicodeDecodeError as exc: raise ValueError(f'Invalid {encoding} byte at offset {exc.start + 1}') from None
    return encoding


def parse_lines(raw: bytes, record_format='stream', record_length=None) -> list[tuple[bytes, bytes]]:
    if record_format == 'fixed':
        if not record_length or not 1 <= record_length <= 1_000_000: raise ValueError('Fixed records require a positive byte length')
        if (len(raw) + record_length - 1) // record_length > 20000: raise ValueError('Record limit: at most 20,000 physical records')
        return [(raw[i:i + record_length], b'') for i in range(0, len(raw), record_length)]
    if record_format != 'stream': raise ValueError('Unsupported record format')
    # Only CR and LF delimit physical stream records; vertical tabs are data.
    result, start, i = [], 0, 0
    while i < len(raw):
        if raw[i] in (10, 13):
            stop = i + (2 if raw[i:i + 2] == b'\r\n' else 1)
            if len(result) >= 20000: raise ValueError('Record limit: at most 20,000 physical records')
            result.append((raw[start:i], raw[i:stop])); start = stop; i = stop
        else: i += 1
    if start < len(raw):
        if len(result) >= 20000: raise ValueError('Record limit: at most 20,000 physical records')
        result.append((raw[start:], b''))
    return result


@lru_cache(maxsize=4)
def _physical_lines(ctl, raw, record_format=None, record_length=None):
    schema = compile_ctl(ctl)
    fmt = record_format or schema.record_format
    length = record_length or schema.record_length
    if schema.record_format == 'fixed' and (fmt != 'fixed' or length != schema.record_length):
        raise ValueError('Record settings conflict with CTL FIX declaration')
    return tuple(parse_lines(raw, fmt, length))


def physical_lines(ctl, raw, record_format=None, record_length=None):
    # Return an independent list: edit operations must never mutate cached source records.
    return list(_physical_lines(ctl, raw, record_format, record_length))


def field_value(f, row, branch=None, branches=None, encoding='ascii'):
    if f.constant is not None: return f.constant
    raw = row[f.start - 1:f.end].decode(encoding)
    value = raw if branch and branch.preserve_blanks else raw.strip(' \t')
    if branch and f.nullif and evaluate_condition(f.nullif, row, branch, branches or [branch], encoding) is True: return None
    if not value: return None
    if branch and f.defaultif and evaluate_condition(f.defaultif, row, branch, branches or [branch], encoding) is True:
        return '0' if f.dtype in ('INTEGER EXTERNAL', 'DECIMAL EXTERNAL') else None
    return value


def field_issues(f: Field, row: bytes, encoding='ascii', branch=None, branches=None) -> list[str]:
    if not f.start: return []
    if len(row) < f.end: return [f'{f.name}: truncated at {len(row)}; requires byte {f.end}']
    value = field_value(f, row, branch, branches, encoding)
    if value is None: return []
    s = value.strip(' \t')
    pattern = r'[+-]?\d+' if f.dtype == 'INTEGER EXTERNAL' else r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
    if f.dtype in ('INTEGER EXTERNAL', 'DECIMAL EXTERNAL') and not re.fullmatch(pattern, s, re.ASCII):
        return [f'{f.name}: invalid {f.dtype}']
    if f.dtype == 'DATE' and f.date_mask in DATE_FORMATS:
        if len(s) != len(f.date_mask): return [f'{f.name}: invalid date width for {f.date_mask}']
        try: datetime.strptime(s, DATE_FORMATS[f.date_mask])
        except ValueError: return [f'{f.name}: invalid calendar date for {f.date_mask}']
    return []


def inspect_record(schema, row, idx, encoding='ascii', fixed_length=None):
    matching, uncertain, unmatched = [], [], []
    for bi, b in enumerate(schema.branches):
        decision = rule_match(b, row, schema.branches, encoding)
        (matching if decision is True else uncertain if decision is None else unmatched).append(bi)
    issues, findings = [], []
    for bi in matching:
        b = schema.branches[bi]
        for f in b.fields:
            for issue in field_issues(f, row, encoding, b, schema.branches):
                if b.trailing_nullcols and len(row) < f.end: continue
                issues.append(issue)
                findings.append({'code': 'MISSING_BYTES' if len(row) < f.end else 'INVALID_FORMAT', 'field': f.name,
                                 'branch': bi, 'start': f.start, 'end': f.end, 'message': issue,
                                 'classification': 'recoverable_structural_error' if len(row) < f.end else 'formatting_discrepancy', 'severity': 'error'})
    if not matching: issues.append('No definitely matching CTL destination')
    if uncertain: issues.append('Unresolved WHEN condition(s): ' + ', '.join(map(str, uncertain)))
    unsupported = schema.diagnostics + [p for bi in matching for p in schema.branches[bi].unsupported]
    if schema.charset and CHARSETS.get(schema.charset) != encoding: unsupported.append('CTL CHARACTERSET is unsupported or conflicts with selected encoding')
    issues.extend('Unsupported: ' + x for x in unsupported)
    if fixed_length and len(row) != fixed_length: issues.append(f'Fixed record requires exactly {fixed_length} bytes')
    if any(x < 32 and x not in (9,) for x in row): issues.append('Control bytes in record; binary input is unsupported')
    minimum = max((schema.branches[bi].minimum_length for bi in matching), default=0)
    return {'record': idx, 'length': len(row), 'record_type': row[:1].decode(encoding),
            'branches': [{'index': x, 'table': schema.branches[x].table} for x in matching],
            'uncertain_branches': uncertain, 'unmatched_branches': unmatched, 'minimum_length': minimum,
            'issues': list(dict.fromkeys(issues)), 'findings': findings, 'unsupported': unsupported,
            'classification': 'unsupported_operation' if unsupported else 'ambiguous_corruption' if uncertain or not matching else
                              'recoverable_structural_error' if minimum > len(row) and issues else 'formatting_discrepancy' if issues else 'valid',
            'status': 'needs_review' if issues else 'valid'}


def inspect(ctl: str, raw: bytes, encoding='ascii', record_format=None, record_length=None) -> dict:
    encoding = check_encoding(raw, encoding)
    schema = compile_ctl(ctl); lines = physical_lines(ctl, raw, record_format, record_length)
    fixed = (record_length or schema.record_length) if (record_format or schema.record_format) == 'fixed' else None
    records = [inspect_record(schema, row, i, encoding, fixed) for i, (row, _) in enumerate(lines, 1)]
    counts = {k: sum(r['classification'] == k for r in records) for k in
              ('valid', 'formatting_discrepancy', 'recoverable_structural_error', 'semantic_inconsistency', 'ambiguous_corruption', 'unsupported_operation')}
    errors = sum(bool(r['issues']) for r in records)
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'branch_count': len(schema.branches), 'record_count': len(records),
            'byte_length': len(raw), 'encoding': encoding, 'record_format': record_format or schema.record_format,
            'line_endings': sorted({e.decode('ascii').replace('\r', 'CR').replace('\n', 'LF') or 'none' for _, e in lines}),
            'records': records, 'counts': counts, 'records_with_findings': errors, 'compatibility': schema.compatibility,
            'schema_diagnostics': schema.diagnostics, 'export_status': 'not_fully_verifiable' if schema.compatibility != 'fully_supported' or counts['unsupported_operation'] else
            'blocked_by_errors' if errors or not records else 'structurally_validated',
            'validation_scope': 'Physical fixed-width subset only. Oracle SQL*Loader and EBS business validity have not been tested.',
            'limitations': ['No SQL expression execution or Oracle database business validation; see CTL compatibility matrix.']}


def selected_fields(ctl, row, encoding='ascii'):
    branches = parse_ctl(ctl)
    return [(bi, b, f) for bi, b in enumerate(branches) if rule_match(b, row, branches, encoding) is True for f in b.fields]


def resolve_field(ctl, row, name, encoding='ascii', branch_index=None):
    found = [(bi, b, f) for bi, b, f in selected_fields(ctl, row, encoding) if f.name == name.upper() and (branch_index is None or bi == branch_index)]
    if not found and branch_index is not None:
        branches = parse_ctl(ctl)
        if not 0 <= branch_index < len(branches): raise ValueError('Unknown assumed layout')
        found = [(branch_index, branches[branch_index], f) for f in branches[branch_index].fields if f.name == name.upper()]
    if not found or len({(f.start, f.end) for _, _, f in found}) != 1: raise ValueError('Ambiguous or unknown field for selected record')
    f = found[0][2]
    if not f.start or f.sql_expression: raise ValueError('Generated or transformed fields are read-only')
    return f


def edit_record(ctl, raw, record_num, field_name, replacement, align='left', encoding='ascii', record_format=None, record_length=None, branch_index=None):
    if align not in ('left', 'right'): raise ValueError('align must be left or right')
    encoding = check_encoding(raw, encoding); lines = physical_lines(ctl, raw, record_format, record_length)
    if not 1 <= record_num <= len(lines): raise ValueError('record out of range')
    row, ending = lines[record_num - 1]
    f = resolve_field(ctl, row, field_name, encoding, branch_index)
    try: payload = replacement.encode(encoding)
    except UnicodeEncodeError: raise ValueError(f'Value is not encodable as {encoding}') from None
    if any(x in payload for x in (b'\r', b'\n', b'\x00')): raise ValueError('Record separators and NUL are prohibited in field values')
    if len(payload) > f.width: raise ValueError(f'Value requires {len(payload)} bytes but {field_name} has width {f.width}')
    padded = payload.ljust(f.width, b' ') if align == 'left' else payload.rjust(f.width, b' ')
    row = row.ljust(f.end, b' ')
    lines[record_num - 1] = (row[:f.start - 1] + padded + row[f.end:], ending)
    return b''.join(a + b for a, b in lines)


def suggestions(ctl, raw, **kwargs):
    from reconstruction import reconstruct
    return reconstruct(ctl, raw, **kwargs)


def pad_record(ctl, raw, record_num, **kwargs):
    diagnosis = inspect(ctl, raw, **kwargs)
    if not 1 <= record_num <= diagnosis['record_count']: raise ValueError('record out of range')
    info = diagnosis['records'][record_num - 1]
    if info['uncertain_branches'] or not info['branches'] or info['minimum_length'] <= info['length']:
        raise ValueError('No supported trailing pad operation for this record')
    lines = physical_lines(ctl, raw, kwargs.get('record_format'), kwargs.get('record_length')); row, end = lines[record_num - 1]
    lines[record_num - 1] = (row.ljust(info['minimum_length'], b' '), end)
    return b''.join(a + b for a, b in lines)


def round_trip(ctl, raw, **kwargs):
    lines = physical_lines(ctl, raw, kwargs.get('record_format'), kwargs.get('record_length'))
    generated = b''.join(row + end for row, end in lines)
    report = inspect(ctl, generated, **kwargs)
    report['round_trip_identical'] = generated == raw
    report['serialized_sha256'] = hashlib.sha256(generated).hexdigest()
    if generated != raw: raise ValueError('Serializer round-trip failed')
    return generated, report


def main()->None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ctl',type=Path,required=True)
    ap.add_argument('--dat',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--report',type=Path)
    ap.add_argument('--edit',nargs=3,metavar=('RECORD_NUMBER','FIELD','VALUE'))
    ap.add_argument('--align',choices=['left','right'],default='left')
    ap.add_argument('--encoding', choices=['ascii', 'latin1', 'cp1252'], default='ascii')
    ap.add_argument('--record-format', choices=['stream', 'fixed'])
    ap.add_argument('--record-length', type=int)
    ap.add_argument('--allow-draft-export', action='store_true')
    ap.add_argument('--approve-trailing-pad',type=int,metavar='RECORD_NUMBER')
    args=ap.parse_args()
    ctl=args.ctl.read_text(encoding='utf-8-sig');raw=args.dat.read_bytes()
    original_checksum = hashlib.sha256(raw).hexdigest()
    opts = {'encoding': args.encoding, 'record_format': args.record_format, 'record_length': args.record_length}
    if args.output and args.output.resolve() in (args.dat.resolve(), args.ctl.resolve()): ap.error('Output must differ from source CTL and DAT paths')
    if args.edit:
        if not args.output:ap.error('--output is required with --edit')
        raw=edit_record(ctl,raw,int(args.edit[0]),args.edit[1],args.edit[2],args.align,**opts)
    if args.approve_trailing_pad:
        if not args.output:ap.error('--output is required with --approve-trailing-pad')
        raw=pad_record(ctl,raw,args.approve_trailing_pad,**opts)
    raw, report = round_trip(ctl, raw, **opts)
    report['original_checksum'] = original_checksum
    if args.output:
        if report['export_status'] != 'structurally_validated' and not args.allow_draft_export: ap.error('Output is not structurally validated; inspect findings or explicitly enable --allow-draft-export')
        args.output.write_bytes(raw)
    report['suggestions']=suggestions(ctl,raw,**opts)
    if args.report:args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
