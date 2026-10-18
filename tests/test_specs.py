import csv
import io
from pathlib import Path

import pytest

from specs import DISPLAY_COLUMNS, MASTER_COLUMNS, SpecError, extract_spec, infer_layouts, infer_rules, inspect_dat, normalize_spec, render_dat, validate_spec


def field(name, start, width, datatype='CHAR', padding='Trailing Space Padded (Left Aligned)', **rules):
    row = {c['key']: '' for c in DISPLAY_COLUMNS}
    row.update(id=name, field_name=name, mapping_type='POSITION', start_pos=str(start), end_pos=str(start + width - 1),
               length=str(width), datatype_conversion=datatype, padding_alignment=padding)
    row['rules'] = {**infer_rules(row), 'required': False, **rules}
    return row


def spec(*fields, **options):
    return normalize_spec({'fields': list(fields), 'options': options, 'layouts': []})


def csv_bytes(rows):
    stream = io.StringIO(newline=''); writer = csv.writer(stream); writer.writerows(rows)
    return stream.getvalue().encode()


def test_markdown_preserves_ten_exact_columns_source_locations_and_sections():
    headers = [c['label'] for c in DISPLAY_COLUMNS]
    values = ['CUSTOMER_NAME', 'Common', 'POSITION', '1', '20', '20', 'CHAR / RTRIM',
              'Trailing Space Padded (Left Aligned)', '"ABC Trading         "', 'ABC Trading']
    table = '| ' + ' | '.join(headers) + ' |\n|' + '|'.join(['---'] * 10) + '|\n| ' + ' | '.join(values) + ' |\n'
    raw = ('# Customer\nA descriptive paragraph.\n' + table + '\nA note after the table.\n# Other\n' + table).encode()
    result = extract_spec('unfamiliar.md', raw)
    assert len(result['fields']) == 2
    assert result['sections'][0]['headers'] == headers
    assert [result['fields'][0][c['key']] for c in DISPLAY_COLUMNS] == values
    assert result['fields'][0]['source']['cells']['raw_sample']['reference'].startswith('Markdown!line')
    assert result['fields'][0]['id'] != result['fields'][1]['id']
    assert 'A note after the table.' in str(result['sections'][0]['metadata'])
    assert result['fields'][0]['rules']['conversion'] == 'rtrim'
    assert result['source_text'] == raw.decode()


def test_csv_preamble_and_additional_columns_do_not_replace_field_header():
    headers = [c['label'] for c in DISPLAY_COLUMNS] + ['Bank note']
    values = ['ID', 'Header', 'POSITION', '1', '6', '6', 'CHAR', 'Fixed Text (Exact Width)', '000123', '000123', 'keep this']
    raw = csv_bytes([['Scenario: unfamiliar'], ["Routing: ID='000123'"], headers, values])
    document = extract_spec('source.csv', raw)
    assert document['fields'][0]['raw_sample'] == '000123'
    assert document['fields'][0]['source']['extra_columns'] == {'Bank note': 'keep this'}
    assert len(document['sections'][0]['metadata']) == 2
    assert document['fields'][0]['source']['row'] == 4
    assert document['fields'][0]['source']['cells']['length']['reference'] == 'CSV!4:6'


def test_master_csv_explicit_padding_notation_and_record_condition():
    condition = "RECORD_TYPE = '04' AND PROCESS = 'STD'"
    rows = []
    for name, start, end, padding, alignment, direction in (
        ('RECORD_TYPE', 1, 2, '', 'EXACT', 'NONE'),
        ('DATA', 3, 8, 'SPACE (0x20)', 'LEFT', 'RIGHT'),
        ('PROCESS', 9, 11, '', 'EXACT', 'NONE'),
    ):
        rows.append({'id': name, 'field_name': name, 'mapping_type': 'POSITION',
                     'start_pos': str(start), 'end_pos': str(end), 'length': str(end - start + 1),
                     'datatype_conversion': 'CHAR', 'padding_alignment': 'Fixed Text (Exact Width)' if alignment == 'EXACT' else 'Trailing Space Padded (Left Aligned)',
                     'sequence': str(len(rows) + 1), 'record_type': '4', 'mandatory': 'YES',
                     'alignment': alignment, 'padding_byte': padding, 'padding_direction': direction,
                     'conditional_rules': condition})
    raw = csv_bytes([[''] * len(MASTER_COLUMNS), [c['label'] for c in MASTER_COLUMNS],
                     *[[row.get(c['key'], '') for c in MASTER_COLUMNS] for row in rows]])
    document = extract_spec('master.csv', raw)
    approved = normalize_spec({'fields': document['fields'], 'layouts': [],
                               'options': {'encoding': 'ascii', 'record_format': 'stream'}})
    assert approved['issues'] == []
    assert approved['layouts'][0]['id'] == '4'
    assert approved['layouts'][0]['when'] == [
        {'start': 1, 'end': 2, 'equals': '04', 'trim': True},
        {'start': 9, 'end': 11, 'equals': 'STD', 'trim': True},
    ]
    assert approved['fields'][1]['rules']['padding'] == {'side': 'right', 'char': ' '}
    assert inspect_dat(approved, b'04ABC   STD\n')['valid'] is True
    rows[0]['conditional_rules'] = "RECORD_TYPE = '04' OR PROCESS = 'STD'"
    invalid = normalize_spec({'fields': rows, 'layouts': []})
    assert any(item['code'] == 'UNSUPPORTED_CONDITION' for item in invalid['issues'])


def test_allowed_values_must_be_compatible_with_routing_bytes():
    source = field('KIND', 1, 2, padding='Fixed Text (Exact Width)', allowed_values=['4'])
    layout = {'id': 'detail', 'field_ids': ['KIND'], 'record_length': 2,
              'when': [{'start': 1, 'end': 2, 'equals': '04'}]}
    invalid = normalize_spec({'fields': [source], 'layouts': [layout]})
    conflicts = [item for item in invalid['issues'] if item['code'] == 'ROUTING_ALLOWED_VALUES']
    assert len(conflicts) == 1 and conflicts[0]['field_id'] == 'KIND'
    assert "'4'" in conflicts[0]['message'] and "'04'" in conflicts[0]['message']
    assert invalid['fields'][0]['rules']['allowed_values'] == ['4']
    source['rules']['allowed_values'] = ['04']
    assert not normalize_spec({'fields': [source], 'layouts': [layout]})['issues']


@pytest.mark.parametrize('layouts,code', [('invalid', 'LAYOUTS'), ([None], 'LAYOUT_TYPE'),
                                          ([{'source_condition': 'KIND = 1', 'field_ids': [[]]}], 'LAYOUT_FIELDS')])
def test_malformed_layouts_are_reported_without_crashing(layouts, code):
    invalid = normalize_spec({'fields': [field('KIND', 1, 2)], 'layouts': layouts})
    assert any(item['code'] == code for item in invalid['issues'])


def test_routing_conflicts_use_the_actual_zero_padded_value_check():
    source = field('CODE', 1, 4, padding='Leading Zero Padded', allowed_values=['12'])
    layout = {'id': 'detail', 'field_ids': ['CODE'], 'record_length': 4,
              'when': [{'start': 1, 'end': 4, 'equals': '0012'}]}
    invalid = normalize_spec({'fields': [source], 'layouts': [layout]})
    assert any(item['code'] == 'ROUTING_ALLOWED_VALUES' for item in invalid['issues'])
    source['rules']['allowed_values'] = ['0012']
    assert not normalize_spec({'fields': [source], 'layouts': [layout]})['issues']


@pytest.mark.parametrize('equals,start,end,trim,values,required', [
    ('A', 1, 1, False, ['AB'], True),
    ('A', 1, 2, True, ['A'], True),
    ('A ', 1, 2, False, ['A'], True),
    ('  ', 1, 2, False, ['A'], False),
])
def test_routing_allowed_values_respects_partial_spans_padding_and_optional_blank(equals, start, end, trim, values, required):
    source = field('KIND', 1, 2, allowed_values=values, required=required)
    layout = {'id': 'detail', 'field_ids': ['KIND'], 'record_length': 2,
              'when': [{'start': start, 'end': end, 'equals': equals, 'trim': trim}]}
    approved = normalize_spec({'fields': [source], 'layouts': [layout]})
    assert not approved['issues']


def test_actual_uploaded_example_specs_keep_all_63_rows_and_routing_source():
    examples = Path(__file__).resolve().parents[1] / 'fixtures' / 'specifications'
    paths = sorted(examples.glob('*.csv'))
    assert len(paths) == 3
    for path in paths:
        result = extract_spec(path.name, path.read_bytes())
        assert len(result['fields']) == 63
        assert [x['label'] for x in DISPLAY_COLUMNS] == result['sections'][0]['headers']
        assert any('CTL Routing Condition:' in str(row['values']) for row in result['sections'][0]['metadata'])
        assert result['fields'][0]['rules']['mapping_type'] == 'system'
        assert next(f for f in result['fields'] if f['field_name'] == 'CM_STATUS')['rules']['constant'] == 'NEW'
        assert next(f for f in result['fields'] if f['field_name'] == 'AMOUNT_APPLIED1')['rules']['datatype'] == 'decimal'
        assert not any(f['rules'].get('unsupported_conversion') for f in result['fields'])
        routing = infer_layouts(result)
        assert not routing['issues']
        assert len(routing['layouts'][0]['when']) >= 2
        assert routing['layouts'][0]['when'][0] == {'start': 1, 'end': 2, 'equals': '04', 'trim': True}


def test_unknown_source_routing_never_becomes_a_silently_unconditional_layout():
    approved = field('CODE', 1, 2)
    approved['source'] = {'section': 'one'}
    document = {'fields': [approved], 'sections': [{'id': 'one', 'name': 'Other bank', 'metadata': [
        {'values': ["CTL Routing Condition: CODE = 'AA' OR CODE = 'BB'"], 'references': ['CSV!1:1']}]}]}
    result = infer_layouts(document)
    assert result['issues'][0]['code'] == 'SOURCE_ROUTING'
    assert result['layouts'][0]['source_conditions'][0]['references'] == ['CSV!1:1']
    document['sections'][0]['metadata'][0]['values'] = ["CTL Routing Condition: CODE = 'AA'"]
    result = infer_layouts(document)
    assert not result['issues']
    assert result['layouts'][0]['when'] == [{'start': 1, 'end': 2, 'equals': 'AA', 'trim': True}]


def test_xlsx_all_sheets_exact_zero_format_and_formula_is_not_executed():
    from openpyxl import Workbook
    workbook = Workbook(); workbook.active.title = 'First'
    for sheet in (workbook.active, workbook.create_sheet('Second')):
        sheet.append([c['label'] for c in DISPLAY_COLUMNS])
        sheet.append(['ID', 'Header', 'POSITION', 1, 6, 6, 'CHAR', 'Fixed Text (Exact Width)', 123, '000123'])
        sheet['I2'].number_format = '000000'
    workbook['Second']['J2'] = '=1+1'
    stream = io.BytesIO(); workbook.save(stream)
    result = extract_spec('source.xlsx', stream.getvalue())
    assert len(result['fields']) == 2
    assert result['fields'][0]['raw_sample'] == '000123'
    assert result['fields'][1]['cleansed_output'] == '=1+1'
    assert any(x['code'] == 'FORMULA' for x in result['issues'])
    assert any(x['code'] == 'FORMULA' for x in validate_spec(result))


def test_position_edits_are_authoritative_and_inconsistent_lengths_block_approval():
    original = spec(field('ID', 1, 4))
    original['fields'][0]['start_pos'] = '2'
    changed = normalize_spec(original)
    assert changed['fields'][0]['rules']['start'] == 2
    assert any(x['code'] == 'POSITIONS' for x in changed['issues'])
    missing = field('ID', 1, 4); missing['rules']['required'] = None
    assert any(x['code'] == 'REQUIRED_RULE' for x in validate_spec(spec(missing)))


def test_4800_byte_formatting_preserves_originals_internal_spaces_and_exact_bytes():
    approved = spec(field('NAME', 1, 20), field('AMOUNT', 21, 12, 'DECIMAL EXTERNAL', 'Leading Space Padded (Right Aligned)'), field('NOTE', 33, 4768))
    raw = b'   ABC Trading      ' + b'123.4500    ' + b'KEEP  TWO SPACES'.ljust(4768) + b'\r\n'
    assert len(raw) == 4802
    report = inspect_dat(approved, raw)
    assert not report['valid'] and any(i['code'] == 'UNAPPROVED_REFORMAT' for i in report['issues'])
    name = report['records'][0]['fields'][0]
    assert name['original_percentage'] < 100 and name['percentage'] is None
    generated = render_dat(approved, raw, [{'record': 1, 'field_id': 'NAME', 'value': '  XYZ Trading   '},
                                            {'record': 1, 'field_id': 'AMOUNT', 'value': '123.4500'}],
                           {'confirmed_records': [1]})
    assert generated[:20] == b'XYZ Trading         '
    assert generated[20:32] == b'    123.4500'
    assert generated[32:-2] == raw[32:-2]
    assert raw[:20] == b'   ABC Trading      '
    assert generated.endswith(b'\r\n') and len(generated) == 4802


def test_char_identifiers_stay_text_and_decimal_date_rules_report_real_checks():
    approved = spec(field('ID', 1, 6), field('AMOUNT', 7, 12, 'DECIMAL EXTERNAL'), field('DAY', 19, 8, "DATE 'YYYYMMDD'", 'Fixed Date Format Mask'))
    raw = b'00AB12' + b'-1234.5000  ' + b'20240229'
    assert render_dat(approved, raw) == raw
    bad = inspect_dat(approved, b'00AB12' + b'12OO.5000   ' + b'20230229')
    assert not bad['valid'] and bad['summary']['percentage'] is not None
    assert bad['records'][0]['fields'][0]['percentage'] == 100
    assert 0 < bad['records'][0]['fields'][1]['percentage'] < 100
    assert any(x['code'] == 'DATATYPE' for x in bad['issues'])
    assert all('does not match' in x['message'] for x in bad['issues'] if x['code'] == 'DATATYPE')
    with pytest.raises(SpecError): render_dat(approved, b'00AB12' + b'12OO.5000   ' + b'20230229')


def test_extra_value_bytes_are_reported_at_their_original_positions_and_never_truncated():
    approved = spec(field('CODE', 1, 2))
    original = b'AB 2'
    report = inspect_dat(approved, original)
    length_issue = next(item for item in report['issues'] if item['code'] == 'RECORD_LENGTH')
    assert 'position 4 (0x32)' in length_issue['message']
    assert report['records'][0]['number'] == 1
    assert not report['valid']
    with pytest.raises(SpecError): render_dat(approved, original)
    assert original == b'AB 2'


@pytest.mark.parametrize('value', ['TOO-LONG', 'café', 'AA\nBB', 'AA\tBB'])
def test_invalid_edits_never_truncate_replace_or_insert_records(value):
    approved = spec(field('ID', 1, 5))
    original = b'ABC  \r\n'
    report = inspect_dat(approved, original, [{'record': 1, 'field_id': 'ID', 'value': value}])
    assert not report['valid']
    with pytest.raises(SpecError): render_dat(approved, original, [{'record': 1, 'field_id': 'ID', 'value': value}])
    assert original == b'ABC  \r\n'


def test_data_only_inputs_keep_permitted_internal_spaces_and_reject_prohibited_ones():
    allowed = spec(field('NAME', 1, 20))
    assert render_dat(allowed, b'ABC'.ljust(20), [{'record': 1, 'field_id': 'NAME', 'value': '  ABC  Trading  '}]) == b'ABC  Trading'.ljust(20)
    prohibited = spec(field('NAME', 1, 20, allow_internal_spaces=False))
    assert not inspect_dat(prohibited, b'ABC Trading'.ljust(20))['valid']


def test_short_records_require_confirmed_boundaries_before_automatic_padding():
    approved = spec(field('NAME', 1, 20))
    raw = b'ABC Trading\n'
    pending = inspect_dat(approved, raw)
    assert not pending['valid'] and pending['summary']['pending_count'] == 1
    assert pending['records'][0]['fields'][0]['percentage'] is None
    with pytest.raises(SpecError): render_dat(approved, raw)
    with pytest.raises(SpecError): render_dat(approved, raw, options={'confirmed_records': [1]})
    assert render_dat(approved, raw, [{'record': 1, 'field_id': 'NAME', 'value': 'ABC Trading'}],
                      {'confirmed_records': [1]}) == b'ABC Trading'.ljust(20) + b'\n'
    with pytest.raises(SpecError): render_dat(approved, b'X' * 21, options={'confirmed_records': [1]})


def test_tasaa_certified_shift_is_the_export_source_and_revalidated():
    left = field('LEFT', 1, 5, allowed_values=['AB'])
    right = field('RIGHT', 6, 3, allowed_values=['XYZ'])
    approved = spec(left, right)
    raw = b'ABXYZ\n'
    report = inspect_dat(approved, raw)
    assert report['valid']
    assert report['records'][0]['alignment']['status'] == 'certified'
    assert report['records'][0]['fields'][1]['original_value'] == 'XYZ'
    assert render_dat(approved, raw) == b'AB   XYZ\n'


def test_unedited_byte_changes_require_record_confirmation():
    approved = spec(field('NAME', 1, 5))
    raw = b' A  B\n'
    report = inspect_dat(approved, raw)
    assert any(i['code'] == 'UNAPPROVED_REFORMAT' for i in report['issues'])
    with pytest.raises(SpecError): render_dat(approved, raw)
    with pytest.raises(SpecError): render_dat(approved, raw, options={'confirmed_records': [1]})
    assert render_dat(approved, raw, [{'record': 1, 'field_id': 'NAME', 'value': 'A  B'}]) == b'A  B \n'


def test_confirming_boundaries_does_not_invent_missing_unmapped_gap_bytes():
    approved = spec(field('LEFT', 1, 2), field('RIGHT', 6, 5))
    report = inspect_dat(approved, b'AA', options={'confirmed_records': [1]})
    assert not report['valid']
    assert any(x['code'] == 'MISSING_UNMAPPED_BYTES' for x in report['issues'])


def routed_spec():
    a, b = field('A', 1, 5), field('B', 1, 5)
    result = spec(a, b)
    result['layouts'] = [
        {'id': 'a', 'field_ids': ['A'], 'record_length': 5, 'when': [{'start': 1, 'end': 1, 'equals': 'A'}]},
        {'id': 'b', 'field_ids': ['B'], 'record_length': 5, 'when': [{'start': 1, 'end': 1, 'equals': 'B'}]},
    ]
    return result


def test_mixed_record_routing_unknown_coverage_and_explicit_preservation():
    approved = routed_spec(); raw = b'A1234\r\nZ9999\nB1234'
    assert not inspect_dat(approved, raw)['valid']
    report = inspect_dat(approved, raw, options={'unknown_records': 'preserve', 'detail_record': 3})
    assert not report['valid'] and len(report['records']) == 1
    assert report['summary']['record_count'] == 3
    assert report['record_summaries'][1]['status'] == 'preserved'
    with pytest.raises(SpecError): render_dat(approved, raw, options={'unknown_records': 'preserve'})
    with pytest.raises(SpecError): render_dat(approved, raw, [{'record': 1, 'field_id': 'A', 'value': 'B1234'}], {'unknown_records': 'preserve'})
    approved['layouts'][1]['when'] = []
    assert not inspect_dat(approved, b'A1234', options={'unknown_records': 'preserve'})['valid']


def test_legitimate_shared_reads_allowed_but_conflicting_writes_are_blocked():
    approved = spec(field('A', 1, 5), field('ALIAS', 1, 5))
    assert not validate_spec(approved)
    assert render_dat(approved, b'ABC  ') == b'ABC  '
    report = inspect_dat(approved, b'ABC  ', [{'record': 1, 'field_id': 'ALIAS', 'value': 'XYZ'}])
    assert not report['valid'] and any(x['code'] == 'CONFLICTING_WRITES' for x in report['issues'])
    assert render_dat(approved, b'ABC  ', [{'record': 1, 'field_id': 'A', 'value': 'XYZ'}, {'record': 1, 'field_id': 'ALIAS', 'value': 'XYZ'}]) == b'XYZ  '


def test_system_constants_filler_and_unmapped_bytes_remain_distinct():
    generated = {'id': 'SYSTEM', 'field_name': 'SYSTEM', 'mapping_type': 'SYSTEM', 'datatype_conversion': 'APP_SEQ.NEXTVAL'}
    constant = {'id': 'STATUS', 'field_name': 'STATUS', 'mapping_type': 'CONSTANT', 'datatype_conversion': "'NEW'"}
    reserved = field('RESERVED', 6, 2); reserved['mapping_type'] = 'FILLER'; reserved['rules'] = {**infer_rules(reserved), 'required': False}
    approved = spec(field('ID', 1, 3), reserved, generated, constant)
    approved['layouts'][0]['record_length'] = 10
    raw = b'ABC!@  XYZ'
    assert render_dat(approved, raw) == raw
    report = inspect_dat(approved, raw)
    system = next(x for x in report['records'][0]['fields'] if x['id'] == 'SYSTEM')
    assert system['percentage'] is None and system['total'] == 0
    assert next(x for x in report['records'][0]['fields'] if x['id'] == 'STATUS')['value'] == 'NEW'
    with pytest.raises(SpecError): render_dat(approved, raw, [{'record': 1, 'field_id': 'RESERVED', 'value': 'AA'}])


def test_ai_rule_overrides_cannot_erase_unknown_conversion_or_required_source_type():
    unknown = field('X', 1, 8, 'UPPER(SOME_DATABASE_FUNCTION(:X))')
    unknown['rules'].update(datatype='char', conversion='identity')
    assert any(x['code'] == 'UNSUPPORTED_CONVERSION' for x in validate_spec(spec(unknown)))
    date = field('DAY', 1, 8, "DATE 'YYYYMMDD'")
    date['rules'].update(datatype='char', conversion='identity')
    assert any(x['code'] == 'SOURCE_DATATYPE' for x in validate_spec(spec(date)))
    binary = spec(field('BINARY', 1, 4, 'INTEGER(4)'))
    assert any(x['code'] == 'UNSUPPORTED_CONVERSION' for x in validate_spec(binary))


def test_fixed_4800_records_and_scanning_all_records_with_one_detail():
    approved = spec(field('DATA', 1, 4800), record_format='fixed', record_length=4800)
    raw = b'A'.ljust(4800) + b'B'.ljust(4800)
    assert render_dat(approved, raw) == raw
    report = inspect_dat(approved, raw, options={'detail_record': 2})
    assert report['summary']['record_count'] == 2 and len(report['records']) == 1
    assert report['records'][0]['number'] == 2
    assert len(report['record_summaries']) == 2


def test_line_endings_preserved_and_explicit_incompatible_requirement_blocks():
    approved = spec(field('DATA', 1, 4))
    raw = b'ABC \r\nDEF \nGHI '
    assert render_dat(approved, raw) == raw
    report = inspect_dat(approved, raw, options={'line_ending': 'CRLF'})
    assert not report['valid']
    assert [x['record'] for x in report['issues'] if x['code'] == 'LINE_ENDING'] == [2, 3]


@pytest.mark.parametrize('options', [
    {'encoding': 'utf-16'}, {'record_format': 'variable'}, {'confirmed_records': ['1']},
    {'unknown_records': 'ignore'}, {'record_format': 'fixed'}, {'detail_record': True},
])
def test_invalid_file_options_fail_closed_without_runtime_type_errors(options):
    approved = spec(field('X', 1, 4))
    with pytest.raises(SpecError): inspect_dat(approved, b'AB  ', options=options)


def test_utf8_positions_are_bytes_and_split_codepoints_are_reported():
    approved = spec(field('NAME', 1, 4), field('CODE', 5, 2), encoding='utf-8')
    raw = 'é'.encode('utf-8') + b'  AA\n'
    report = inspect_dat(approved, raw)
    assert report['valid'] and report['records'][0]['fields'][0]['original_value'] == 'é'
    assert render_dat(approved, raw) == raw
    split = spec(field('LEFT', 1, 1), field('RIGHT', 2, 1), encoding='utf-8')
    failed = inspect_dat(split, 'é'.encode('utf-8'))
    assert not failed['valid']
    assert any(item['code'] == 'ENCODING' for item in failed['issues'])
