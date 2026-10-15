from pathlib import Path
import pytest
from compiler import compile_ctl, tokenize
from engine import inspect, edit_record, physical_lines, round_trip, parse_lines, rule_match


def ctl(fields, when='', prefix='LOAD DATA', suffix=''):
    return f'{prefix} INTO TABLE T {when} ({fields}) {suffix}'


def test_inline_quoted_nested_and_source():
    source = '''LOAD DATA /* comment INTO TABLE BAD */ APPEND
    INTO TABLE "T" WHEN ((RT = '1') AND ((2:3) != 'XX'))
    ( RT POSITION(1:1) CHAR, "REF" POSITION(2:6) CHAR,
      GEN CONSTANT 'INTO TABLE X ( -- ''quoted'' )')'''
    schema=compile_ctl(source)
    assert len(schema.branches)==1 and schema.branches[0].mode=='APPEND'
    assert schema.branches[0].fields[2].constant=="INTO TABLE X ( -- 'quoted' )"
    f=schema.branches[0].fields[1]
    assert source[f.source['start']:f.source['end']]==f.source['declaration']
    assert inspect(source,b'1ABCDE\r\n')['records'][0]['status']=='valid'


def test_unconditional_constants_fillers_relative_and_gap():
    source=ctl("RT POSITION(1:1) CHAR, UNUSED FILLER POSITION(*:3) CHAR, X POSITION(*+2) CHAR(2), C CONSTANT '000'")
    schema=compile_ctl(source)
    assert schema.compatibility=='fully_supported'
    assert schema.branches[0].fields[2].start==6
    assert schema.branches[0].to_dict()['unmapped_intervals']==[{'start':4,'end':5}]
    assert not inspect(source,b'1xx??AB')['records'][0]['issues']


def test_multiple_destinations_and_local_repeated_names():
    source=ctl("RT POSITION(1:1) CHAR, V POSITION(2:3) CHAR", "WHEN RT='1'")+" INTO TABLE U WHEN RT='1' (RT POSITION(1:1) CHAR, V POSITION(4:5) CHAR)"
    assert len(inspect(source,b'1ABCD')['records'][0]['branches'])==2
    with pytest.raises(ValueError,match='Ambiguous'): edit_record(source,b'1ABCD',1,'V','X')
    assert edit_record(source,b'1ABCD',1,'V','X',branch_index=1)==b'1ABX '


@pytest.mark.parametrize('condition',["WHEN RT='1' OR RT='2'", "WHEN RT > '1'", "WHEN UNKNOWN='1'"])
def test_unknown_conditions_never_match(condition):
    report=inspect(ctl('RT POSITION(1:1) CHAR',condition),b'1')['records'][0]
    assert not report['branches'] and report['uncertain_branches']


def test_raw_range_compares_without_trimming():
    source=ctl('RT POSITION(1:1) CHAR, V POSITION(2:4) CHAR',"WHEN (2:4) = 'A'")
    assert not inspect(source,b'1A  ')['records'][0]['branches']
    source=source.replace("(2:4)","V")
    assert inspect(source,b'1A  ')['records'][0]['branches']


def test_null_and_default_interpretation_preserves_raw():
    from projects import record_detail,encode
    source=ctl('RT POSITION(1:1) CHAR, X POSITION(2:4) CHAR NULLIF X=BLANKS, N POSITION(5:6) INTEGER EXTERNAL DEFAULTIF X=BLANKS')
    p={'ctl':source,'data':encode(b'1   99'),'original':encode(b'1   99'),'encoding':'ascii','record_format':None,'record_length':None}
    detail=record_detail(p,1)
    assert detail['fields'][1]['value'] is None
    assert detail['fields'][2]['value']=='0'
    assert detail['fields'][2]['raw']=='99'


@pytest.mark.parametrize('mask,good,bad',[('YYYYMMDD',b'20240229',b'20230229'),('RRMMDD',b'240229',b'230229'),('YYYY-MM-DD',b'2024-02-29',b'2024-04-31')])
def test_calendar_dates(mask,good,bad):
    source=ctl(f"D POSITION(1:{len(good)}) DATE '{mask}'")
    assert not inspect(source,good)['records'][0]['issues']
    assert inspect(source,bad)['records'][0]['issues']


@pytest.mark.parametrize('typ,good,bad',[('INTEGER EXTERNAL',b'-0012',b'12.30'),('DECIMAL EXTERNAL',b'-.012',b' 1,20')])
def test_numeric_external(typ,good,bad):
    source=ctl(f'N POSITION(1:5) {typ}')
    assert not inspect(source,good)['records'][0]['issues']
    assert inspect(source,bad)['records'][0]['issues']


@pytest.mark.parametrize('modifier',["FIELDS TERMINATED BY ','",'CONCATENATE 2',"OPTIONS (SKIP=1)"])
def test_unknown_global_behavior_blocks_export(modifier):
    report=inspect(ctl('X POSITION(1:1) CHAR',prefix='LOAD DATA '+modifier),b'X')
    assert report['export_status']=='not_fully_verifiable'


def test_transform_is_metadata_and_blocks_validation():
    schema=compile_ctl(ctl('X POSITION(1:5) CHAR "NVL(:X, \'a,b()\')"'))
    assert schema.branches[0].fields[0].sql_expression=="NVL(:X, 'a,b()')"
    assert schema.compatibility=='partially_supported'


def test_fixed_records_and_partial_final_record():
    source=ctl('V POSITION(1:4) CHAR',prefix='LOAD DATA INFILE \'a.dat\' "FIX 4"')
    assert physical_lines(source,b'ABCDWXYZ')==[(b'ABCD',b''),(b'WXYZ',b'')]
    assert round_trip(source,b'ABCDWXYZ')[0]==b'ABCDWXYZ'
    assert inspect(source,b'ABCDWX')['export_status']=='blocked_by_errors'
    with pytest.raises(ValueError,match='conflict'): inspect(source,b'ABCD',record_format='stream')


def test_stream_fidelity_and_unmapped_data():
    source=ctl('V POSITION(3:4) CHAR')
    raw=b'??AB!!\r\n  CD**\n__EF$$\rGHJK'
    assert round_trip(source,raw)[0]==raw
    out=edit_record(source,raw,2,'V','XY')
    assert out==raw.replace(b'  CD**',b'  XY**')
    assert parse_lines(b'A\vB\fC\x1cD')==[(b'A\vB\fC\x1cD',b'')]


def test_single_byte_encodings_and_newline_guard():
    source=ctl('X POSITION(1:3) CHAR')
    with pytest.raises(ValueError,match='Invalid ascii'):inspect(source,b'\xe9  ')
    assert edit_record(source,b'\xe9  ',1,'X','café'[:3],encoding='latin1')==b'caf'
    with pytest.raises(ValueError):edit_record(source,b'abc',1,'X','\n')
    with pytest.raises(ValueError):inspect(source,b'abc',encoding='utf-8')


def test_no_silent_truncation_and_record_zero_guard():
    source=ctl('X POSITION(1:3) CHAR')
    with pytest.raises(ValueError,match='width'):edit_record(source,b'abc',1,'X','1234')
    from engine import pad_record
    with pytest.raises(ValueError,match='range'):pad_record(source,b'ab',0)


def test_overlap_and_unsupported_binary_are_diagnostic():
    assert compile_ctl(ctl('X POSITION(1:3) CHAR,Y POSITION(2:4) CHAR')).compatibility=='partially_supported'
    assert compile_ctl(ctl('X POSITION(1:4) INTEGER')).compatibility=='partially_supported'


def test_large_field_exact_offsets():
    source=ctl('A POSITION(1:1) CHAR, X POSITION(5000:12000) CHAR')
    raw=b'1'+b'?'*11999
    out=edit_record(source,raw,1,'X','000123')
    assert out[:4999]==raw[:4999] and out[4999:5005]==b'000123' and len(out)==12000


def test_quoted_field_reference_in_when_is_not_a_string_literal():
    source=ctl('"RT" POSITION(1:1) CHAR, X POSITION(2:3) CHAR', 'WHEN "RT" = \'1\'')
    assert inspect(source,b'1AB')['records'][0]['branches']
    assert not inspect(source,b'2AB')['records'][0]['branches']
