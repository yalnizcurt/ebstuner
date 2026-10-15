"""Bounded spreadsheet import and exact, key-based reconciliation."""
from __future__ import annotations
import csv
import io
import re
import zipfile
from decimal import Decimal
from collections import Counter
from datetime import date, datetime
from engine import compile_ctl, physical_lines, selected_fields

MAX_ROWS, MAX_CELLS, MAX_UNCOMPRESSED = 20_000, 400_000, 80_000_000
ROLES = {'reference_values', 'field_specifications', 'example_records', 'transaction_values'}


def header_name(value): return re.sub(r'[^A-Z0-9]+', '_', value.strip().upper()).strip('_')


def cell_text(value, number_format='General'):
    if value is None: return ''
    if isinstance(value, str): return value
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, bool): return 'TRUE' if value else 'FALSE'
    if isinstance(value, (float, int, Decimal)):
        dec = Decimal(str(value))
        if re.fullmatch(r'0+', number_format) and dec == dec.to_integral_value(): return str(int(dec)).zfill(len(number_format))
        if re.fullmatch(r'0\.0+', number_format):
            places = len(number_format.split('.')[1])
            formatted = format(dec, f'.{places}f')
            if Decimal(formatted) == dec: return formatted
        return format(dec, 'f')
    return str(value)


def import_spreadsheet(name, data, role='reference_values'):
    if role not in ROLES: raise ValueError('Invalid spreadsheet role')
    sheets, warnings = [], []
    if name.lower().endswith('.csv'):
        text = data.decode('utf-8-sig', 'strict')
        try: dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error: dialect = csv.excel
        rows = []
        for i, row in enumerate(csv.reader(io.StringIO(text, newline=''), dialect)):
            if i >= MAX_ROWS + 20: raise ValueError('Spreadsheet exceeds 20,000 rows')
            rows.append([(v, '') for v in row])
        raw_sheets = [('CSV', rows)]
    elif name.lower().endswith('.xlsx'):
        from openpyxl import load_workbook
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(x.file_size for x in z.infolist()) > MAX_UNCOMPRESSED: raise ValueError('XLSX expands beyond 80 MB')
            if len(z.infolist()) > 2000: raise ValueError('XLSX has too many parts')
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        raw_sheets, cells = [], 0
        try:
            if len(wb.worksheets) > 30: raise ValueError('At most 30 sheets are supported')
            for ws in wb.worksheets:
                rows = []
                for i, row in enumerate(ws.iter_rows()):
                    if i >= MAX_ROWS + 20: raise ValueError('Spreadsheet exceeds 20,000 rows per sheet')
                    cells += len(row)
                    if cells > MAX_CELLS: raise ValueError('Spreadsheet exceeds 400,000 cells')
                    converted = []
                    for column_index, c in enumerate(row, 1):
                        from openpyxl.utils import get_column_letter
                        coordinate = getattr(c, 'coordinate', f'{get_column_letter(column_index)}{i + 1}')
                        if c.data_type == 'f':
                            warnings.append(f'{ws.title}!{coordinate}: formula ignored; supply literal evidence')
                            converted.append(('', coordinate)); continue
                        if isinstance(c.value, float):
                            warnings.append(f'{ws.title}!{coordinate}: Excel numeric precision applies; text cells are required for exact long identifiers/decimals')
                        converted.append((cell_text(c.value, c.number_format), coordinate))
                    rows.append(converted)
                raw_sheets.append((ws.title, rows))
        finally: wb.close()
    else: raise ValueError('Supplement must be CSV or XLSX')
    for sheet_name, rows in raw_sheets:
        nonempty = [(i, r) for i, r in enumerate(rows[:20]) if any(v.strip() for v, _ in r)]
        if not nonempty: continue
        # Prefer the first row with the greatest number of distinct textual headers.
        hi, header = max(nonempty, key=lambda x: (sum(bool(re.search('[A-Za-z_]', v)) for v, _ in x[1]), len({v for v, _ in x[1] if v}), -x[0]))
        headers, seen = [], {}
        for j, (v, _) in enumerate(header):
            base = header_name(v) or f'COLUMN_{j + 1}'
            seen[base] = seen.get(base, 0) + 1
            headers.append(base if seen[base] == 1 else f'{base}_{seen[base]}')
        if len(headers) > 200: raise ValueError('At most 200 spreadsheet columns are supported')
        records = []
        for i, row in enumerate(rows[hi + 1:], hi + 2):
            if not any(v for v, _ in row): continue
            records.append({'row': i, 'values': {h: row[j][0] if j < len(row) else '' for j, h in enumerate(headers)},
                            'references': {h: f'{sheet_name}!{row[j][1] or str(i) + ":" + str(j + 1)}' if j < len(row) else f'{sheet_name}!{i}:{j + 1}' for j, h in enumerate(headers)}})
        if len(records) > MAX_ROWS or len(records) * len(headers) > MAX_CELLS: raise ValueError('Spreadsheet exceeds import limits')
        sheets.append({'name': sheet_name, 'headers': headers, 'header_row': hi + 1,
                       'kind': 'mapping' if {'FIELD_NAME', 'START_POS', 'END_POS'}.intersection(headers) else 'transaction_data',
                       'rows': records})
    return {'name': name, 'role': role, 'sheets': sheets, 'warnings': list(dict.fromkeys(warnings))[:100],
            'precision_policy': 'Text cells and CSV preserve exact strings. Excel numeric cells retain stored/displayed precision only.'}


def default_mapping(ctl, sheet):
    names = {f.name for b in compile_ctl(ctl).branches for f in b.fields if f.start}
    return {h: h for h in sheet['headers'] if h in names}


def reconcile(ctl, raw, supplement, mapping, keys, sheet_name, encoding='ascii', record_format=None, record_length=None):
    sheet = next((s for s in supplement.get('sheets', []) if s['name'] == sheet_name), None)
    if not sheet: raise ValueError('Select an imported sheet')
    names = {f.name for b in compile_ctl(ctl).branches for f in b.fields if f.start}
    if not mapping or any(k not in sheet['headers'] or v not in names for k, v in mapping.items()): raise ValueError('Mapping contains an unknown header or CTL field')
    if len(set(mapping.values())) != len(mapping): raise ValueError('Multiple columns cannot target the same CTL field')
    if not keys or any(k not in mapping.values() for k in keys): raise ValueError('Choose mapped key fields; row order is never used as a join key')
    inverse = {v: k for k, v in mapping.items()}
    index, duplicate_keys = {}, []
    for r in sheet['rows']:
        key = tuple(r['values'].get(inverse[k], '') for k in keys)
        if any(not x for x in key): continue
        index.setdefault(key, []).append(r)
    duplicate_keys = [{'key': list(k), 'rows': [r['row'] for r in rows]} for k, rows in index.items() if len(rows) > 1]
    evidence, conflicts, missing, matched_rows = [], [], [], set()
    dat_keys = {}
    for n, (row, _) in enumerate(physical_lines(ctl, raw, record_format, record_length), 1):
        by_name = {}
        for _, _, f in selected_fields(ctl, row, encoding):
            if f.name in keys and f.start: by_name.setdefault(f.name, set()).add((f.start, f.end))
        if all(k in by_name and len(by_name[k]) == 1 for k in keys):
            values = {k: row[next(iter(by_name[k]))[0] - 1:next(iter(by_name[k]))[1]].decode(encoding).strip(' ') for k in keys}
            if all(values.values()): dat_keys[n] = tuple(values[k] for k in keys)
    dat_counts = Counter(dat_keys.values())
    duplicate_dat_keys = [{'key': list(k), 'records': [n for n, key in dat_keys.items() if key == k]} for k, count in dat_counts.items() if count > 1]
    for n, (row, _) in enumerate(physical_lines(ctl, raw, record_format, record_length), 1):
        fields = {f.name: f for _, _, f in selected_fields(ctl, row, encoding) if f.start}
        # Repeated names at different offsets cannot participate in reconciliation.
        occurrences = {}
        for _, _, f in selected_fields(ctl, row, encoding): occurrences.setdefault(f.name, set()).add((f.start, f.end))
        fields = {k: v for k, v in fields.items() if len(occurrences[k]) == 1}
        values = {name: row[f.start - 1:f.end].decode(encoding).strip(' ') for name, f in fields.items()}
        if not all(k in values and values[k] for k in keys): missing.append(n); continue
        key = tuple(values[k] for k in keys)
        if dat_counts[key] > 1: missing.append(n); continue
        rows = index.get(key, [])
        if len(rows) != 1: missing.append(n); continue
        ref = rows[0]; matched_rows.add(ref['row'])
        for header, field in mapping.items():
            val = ref['values'].get(header, '')
            if not val or field not in fields: continue
            e = {'record': n, 'field': field, 'value': val, 'source_type': 'CSV' if supplement['name'].lower().endswith('.csv') else 'XLSX',
                 'source_reference': ref['references'][header], 'sheet': sheet_name, 'source_row': ref['row'],
                 'key_fields': keys, 'matched_key': list(key)}
            evidence.append(e)
            if values[field] != val: conflicts.append({**e, 'original_value': values[field], 'proposed_value': val, 'requires_approval': True})
    return {'mapping': mapping, 'keys': keys, 'sheet': sheet_name, 'evidence': evidence, 'conflicts': conflicts,
            'duplicate_keys': duplicate_keys, 'duplicate_dat_keys': duplicate_dat_keys, 'missing_supplement_records': missing,
            'unmatched_sheet_rows': [r['row'] for r in sheet['rows'] if r['row'] not in matched_rows], 'matched_records': len({e['record'] for e in evidence})}


def compare_field_specifications(ctl, supplement, sheet_name, mapping):
    sheet = next((s for s in supplement.get('sheets', []) if s['name'] == sheet_name), None)
    attributes = {'FIELD_NAME', 'START', 'END', 'DATATYPE', 'TARGET_TABLE'}
    if not sheet or any(h not in sheet['headers'] or a not in attributes for h, a in mapping.items()): raise ValueError('Invalid specification column mapping')
    if 'FIELD_NAME' not in mapping.values() or len(set(mapping.values())) != len(mapping): raise ValueError('Map one field-name column and distinct optional start, end, type and table columns')
    inverse = {a: h for h, a in mapping.items()}
    entries, conflicts = [], []
    for row in sheet['rows']:
        name = row['values'].get(inverse['FIELD_NAME'], '').upper()
        if not name: continue
        table = row['values'].get(inverse.get('TARGET_TABLE', ''), '').upper()
        fields = [(b, f) for b in compile_ctl(ctl).branches for f in b.fields if f.name == name and (not table or b.table == table)]
        ref = row['references'][inverse['FIELD_NAME']]
        issues = []
        if not fields: issues.append('Unknown CTL field or destination')
        if len({(f.start, f.end, f.dtype) for _, f in fields}) > 1: issues.append('Repeated field has multiple CTL layouts; provide a target table')
        for attribute, getter in [('START', lambda f: str(f.start)), ('END', lambda f: str(f.end)), ('DATATYPE', lambda f: f.dtype)]:
            expected = row['values'].get(inverse.get(attribute, ''), '').strip().upper()
            if expected and fields and any(getter(f) != expected for _, f in fields): issues.append(f'{attribute} conflicts with CTL')
        entry = {'field': name, 'table': table, 'source_reference': ref, 'issues': issues, 'declarations': [f.source for _, f in fields]}
        entries.append(entry)
        if issues: conflicts.append(entry)
    return {'sheet': sheet_name, 'mapping': mapping, 'keys': [], 'field_specs': entries, 'spec_conflicts': conflicts,
            'evidence': [], 'conflicts': [], 'duplicate_keys': [], 'missing_supplement_records': [],
            'matched_records': sum(not e['issues'] for e in entries), 'unmatched_sheet_rows': []}
