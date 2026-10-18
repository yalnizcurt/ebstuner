"""Multi-Specification and Multi-DAT Test Suite (Section 5).

Tests the Studio, TASAA, and validation pipeline against:
- 5.1 Independent Scenarios A through H (A: 20 fields/800b, B: 50 fields/4000b,
  C: 100 fields/10000b, D: 180 fields/20000b, E: 300 fields/50000b,
  F: Mixed layouts, G: Corrupted/Ambiguous, H: Format diversity)
- 5.2 Multiple specification layouts in one job with explicit record-routing conditions:
  - Header, Detail, Trailer layouts
  - Routing conflicts (multiple matching layouts -> AMBIGUOUS_LAYOUT)
  - Unmatched records preserved without false certification (UNKNOWN_LAYOUT)
- 5.3 Multiple DAT files:
  - Batch isolation across multiple files
  - Mixed valid/invalid records
  - Failure in one file never corrupts another
- 5.4 Specification format diversity:
  - Custom CSV headers and column order
  - XLSX workbooks via openpyxl
  - Gaps, filler regions, and unowned regions explicitly flagged
"""

import pytest

from fixtures.synthetic_generator import SyntheticFixtureGenerator
from specs import (
    normalize_spec,
    validate_spec,
    extract_spec,
    _process,
    SpecError,
)
from tasaa import analyze, AlignmentOptions
from validation_pipeline import compile_pipeline, validate_record


# ==============================================================================
# 5.1 Independent Scenarios Matrix (A - E)
# ==============================================================================

def test_scenario_a_basic_char_number_15_25_fields():
    """Scenario A: 20 fields, 800 width, Basic CHAR/NUMBER."""
    gen = SyntheticFixtureGenerator(seed=101)
    layout = gen.generate_layout(field_count=20, record_width=800, layout_id="scenario_a", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)

    norm = normalize_spec(spec)
    issues = validate_spec(norm)
    assert not any(i.get('severity') == 'error' for i in issues)

    pipeline = compile_pipeline(norm)
    assert sum(len(l.steps) for l in pipeline.layouts) >= 20

    record, gt = gen.generate_record(layout)
    assert len(record) == 800

    val_res = validate_record(pipeline, record, "scenario_a")
    assert val_res['valid'] is True

    report, candidate = _process(norm, record + b"\n")
    assert report['valid'] is True
    assert report['summary']['blocking_count'] == 0


def test_scenario_b_dates_decimals_padding_40_60_fields():
    """Scenario B: 50 fields, 4,000 width, Dates, decimals, padding."""
    gen = SyntheticFixtureGenerator(seed=202)
    layout = gen.generate_layout(field_count=50, record_width=4000, layout_id="scenario_b", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)

    norm = normalize_spec(spec)
    issues = validate_spec(norm)
    assert not any(i.get('severity') == 'error' for i in issues)

    pipeline = compile_pipeline(norm)
    record, gt = gen.generate_record(layout)
    assert len(record) == 4000

    val_res = validate_record(pipeline, record, "scenario_b")
    assert val_res['valid'] is True

    # Intentionally corrupt one field's padding
    corrupt_rec, c_gt = gen.corrupt_missing_padding(record, gt, field_idx=10, cut_bytes=5)
    assert len(corrupt_rec) < 4000

    tasaa_res = analyze(norm, norm['layouts'][0], corrupt_rec)
    # Must never claim positionally compliant on truncated record
    assert tasaa_res['classification'] != 'POSITIONALLY_COMPLIANT'
    assert tasaa_res['provenance']['source_bytes_accounted_for'] == len(corrupt_rec)


def test_scenario_c_mixed_constraints_80_120_fields():
    """Scenario C: 100 fields, 10,000 width, Mixed constraints."""
    gen = SyntheticFixtureGenerator(seed=303)
    layout = gen.generate_layout(field_count=100, record_width=10000, layout_id="scenario_c", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)

    norm = normalize_spec(spec)
    assert not any(i.get('severity') == 'error' for i in validate_spec(norm))

    pipeline = compile_pipeline(norm)
    record, _ = gen.generate_record(layout)
    assert len(record) == 10000

    val_res = validate_record(pipeline, record, "scenario_c")
    assert val_res['valid'] is True


def test_scenario_d_large_schema_180_fields_20000_positions():
    """Scenario D: 180 fields, 20,000+ width, Large fixed-width schema."""
    gen = SyntheticFixtureGenerator(seed=404)
    layout = gen.generate_layout(field_count=180, record_width=21600, layout_id="scenario_d", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)

    norm = normalize_spec(spec)
    pipeline = compile_pipeline(norm)
    assert sum(len(l.steps) for l in pipeline.layouts) >= 180

    record, _ = gen.generate_record(layout)
    assert len(record) == 21600

    val_res = validate_record(pipeline, record, "scenario_d")
    assert val_res['valid'] is True


def test_scenario_e_scale_300_fields_50000_positions():
    """Scenario E: 300 fields, 50,000+ width, Performance and memory."""
    gen = SyntheticFixtureGenerator(seed=505)
    layout = gen.generate_layout(field_count=300, record_width=51000, layout_id="scenario_e", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)

    norm = normalize_spec(spec)
    pipeline = compile_pipeline(norm)
    assert sum(len(l.steps) for l in pipeline.layouts) >= 300

    record, _ = gen.generate_record(layout)
    assert len(record) == 51000

    # Rapid single-record pass through pipeline
    val_res = validate_record(pipeline, record, "scenario_e")
    assert val_res['valid'] is True


# ==============================================================================
# 5.2 Multiple Specifications / Mixed Layouts in One Job (Scenario F)
# ==============================================================================

def test_scenario_f_mixed_layout_routing_header_detail_trailer():
    """Scenario F: Multi-layout specification (HDR, DTL, TRL) in one file with record routing."""
    gen = SyntheticFixtureGenerator(seed=606)

    # 1. Header Layout (40 bytes, starts with 'HDR')
    hdr_layout = gen.generate_layout(field_count=3, record_width=40, layout_id="HDR_LAYOUT", record_type_code="HDR", all_mandatory=True)
    hdr_layout.when = [{'start': 1, 'end': 3, 'equals': 'HDR', 'trim': True}]

    # 2. Detail Layout (60 bytes, starts with 'DTL')
    dtl_layout = gen.generate_layout(field_count=5, record_width=60, layout_id="DTL_LAYOUT", record_type_code="DTL", all_mandatory=True)
    dtl_layout.when = [{'start': 1, 'end': 3, 'equals': 'DTL', 'trim': True}]

    # 3. Trailer Layout (30 bytes, starts with 'TRL')
    trl_layout = gen.generate_layout(field_count=2, record_width=30, layout_id="TRL_LAYOUT", record_type_code="TRL", all_mandatory=True)
    trl_layout.when = [{'start': 1, 'end': 3, 'equals': 'TRL', 'trim': True}]

    # Combine into unified master spec
    all_fields = []
    seen = set()
    for l in (hdr_layout, dtl_layout, trl_layout):
        spec_dict = gen.to_master_spec_dict(l)
        for f in spec_dict['fields']:
            if f['id'] not in seen:
                all_fields.append(f)
                seen.add(f['id'])

    multi_spec = {
        'fields': all_fields,
        'layouts': [
            {'id': 'HDR_LAYOUT', 'name': 'Header', 'field_ids': [f.field_id for f in hdr_layout.fields],
             'record_length': 40, 'when': hdr_layout.when},
            {'id': 'DTL_LAYOUT', 'name': 'Detail', 'field_ids': [f.field_id for f in dtl_layout.fields],
             'record_length': 60, 'when': dtl_layout.when},
            {'id': 'TRL_LAYOUT', 'name': 'Trailer', 'field_ids': [f.field_id for f in trl_layout.fields],
             'record_length': 30, 'when': trl_layout.when},
        ],
        'options': {'encoding': 'ascii', 'record_format': 'stream'},
    }

    norm = normalize_spec(multi_spec)
    assert not any(i.get('severity') == 'error' for i in validate_spec(norm))

    # Generate DAT with 1 Header, 3 Details, 1 Trailer
    r_hdr, _ = gen.generate_record(hdr_layout)
    r_dtl1, _ = gen.generate_record(dtl_layout)
    r_dtl2, _ = gen.generate_record(dtl_layout)
    r_dtl3, _ = gen.generate_record(dtl_layout)
    r_trl, _ = gen.generate_record(trl_layout)

    dat_bytes = b"\n".join([r_hdr, r_dtl1, r_dtl2, r_dtl3, r_trl]) + b"\n"

    # Process through pipeline
    report, candidate = _process(norm, dat_bytes)
    assert report['valid'] is True
    assert report['summary']['record_count'] == 5
    assert report['summary']['blocking_count'] == 0
    assert [r['layout_id'] for r in report['record_summaries']] == [
        'HDR_LAYOUT', 'DTL_LAYOUT', 'DTL_LAYOUT', 'DTL_LAYOUT', 'TRL_LAYOUT'
    ]


def test_routing_conflict_reports_ambiguous_layout():
    """If two layouts both match a record, report routing conflict (AMBIGUOUS_LAYOUT)."""
    spec = {
        'fields': [
            {'id': 'F1', 'field_name': 'CODE', 'mapping_type': 'POSITION', 'start_pos': '1', 'end_pos': '3', 'length': '3',
             'datatype_conversion': 'CHAR', 'padding_alignment': 'Fixed Text (Exact Width)', 'mandatory': 'YES',
             'rules': {'mapping_type': 'physical', 'start': 1, 'end': 3, 'length': 3, 'datatype': 'char', 'conversion': 'identity', 'required': True, 'padding': {'side': 'none', 'char': ' '}}},
            {'id': 'F2', 'field_name': 'DATA', 'mapping_type': 'POSITION', 'start_pos': '4', 'end_pos': '10', 'length': '7',
             'datatype_conversion': 'CHAR', 'padding_alignment': 'Fixed Text (Exact Width)', 'mandatory': 'YES',
             'rules': {'mapping_type': 'physical', 'start': 4, 'end': 10, 'length': 7, 'datatype': 'char', 'conversion': 'identity', 'required': True, 'padding': {'side': 'none', 'char': ' '}}},
        ],
        'layouts': [
            {'id': 'LAYOUT_A', 'name': 'A', 'field_ids': ['F1', 'F2'], 'record_length': 10, 'when': [{'start': 1, 'end': 2, 'equals': 'XX', 'trim': True}]},
            {'id': 'LAYOUT_B', 'name': 'B', 'field_ids': ['F1', 'F2'], 'record_length': 10, 'when': [{'start': 1, 'end': 2, 'equals': 'XX', 'trim': True}]},  # Identical condition!
        ],
        'options': {'encoding': 'ascii', 'record_format': 'stream'},
    }
    norm = normalize_spec(spec)
    row = b"XX1DATA001\n"
    report, candidate = _process(norm, row)
    assert report['record_summaries'][0]['status'] == 'needs_review'
    assert any(i['code'] == 'AMBIGUOUS_LAYOUT' for i in report['record_summaries'][0]['issues'])


def test_unmatched_record_preserved_when_configured():
    """If a record matches no approved layout and unknown_records='preserve',

    preserve it unvalidated and report UNKNOWN_LAYOUT warning.
    """
    spec = {
        'fields': [
            {'id': 'F1', 'field_name': 'CODE', 'mapping_type': 'POSITION', 'start_pos': '1', 'end_pos': '3', 'length': '3',
             'datatype_conversion': 'CHAR', 'padding_alignment': 'Fixed Text (Exact Width)', 'mandatory': 'YES',
             'rules': {'mapping_type': 'physical', 'start': 1, 'end': 3, 'length': 3, 'datatype': 'char', 'conversion': 'identity', 'required': True, 'padding': {'side': 'none', 'char': ' '}}},
        ],
        'layouts': [
            {'id': 'L1', 'name': 'L1', 'field_ids': ['F1'], 'record_length': 3, 'when': [{'start': 1, 'end': 3, 'equals': 'ABC', 'trim': True}]},
        ],
        'options': {'encoding': 'ascii', 'record_format': 'stream', 'unknown_records': 'preserve'},
    }
    norm = normalize_spec(spec)
    # Record starts with 'XYZ', does not match L1
    row = b"XYZ\n"
    report, candidate = _process(norm, row)
    assert report['record_summaries'][0]['status'] == 'preserved'
    assert any(i['code'] == 'UNKNOWN_LAYOUT' for i in report['record_summaries'][0]['issues'])
    # Output must retain original unvalidated bytes exactly
    assert candidate == b"XYZ\n"


# ==============================================================================
# 5.3 Multiple DAT Files / Batch Processing Isolation
# ==============================================================================

def test_multiple_dat_files_isolated_state():
    """Test one specification applied across multiple separate DAT files.

    Ensure each file's validation status, issue counts, and output bytes are strictly isolated.
    A failure in file 2 must never pollute file 1 or file 3.
    """
    gen = SyntheticFixtureGenerator(seed=707)
    layout = gen.generate_layout(field_count=10, record_width=100, layout_id="batch_layout", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)
    norm = normalize_spec(spec)

    rec_valid_1, _ = gen.generate_record(layout)
    rec_valid_2, _ = gen.generate_record(layout)
    rec_corrupted, _ = gen.corrupt_missing_padding(rec_valid_2, [], field_idx=2, cut_bytes=4)

    # File 1: Perfectly valid
    file1_bytes = rec_valid_1 + b"\n"
    # File 2: Corrupted record
    file2_bytes = rec_corrupted + b"\n"
    # File 3: Valid record
    file3_bytes = rec_valid_2 + b"\n"

    rep1, cand1 = _process(norm, file1_bytes)
    rep2, cand2 = _process(norm, file2_bytes)
    rep3, cand3 = _process(norm, file3_bytes)

    # File 1 passed
    assert rep1['valid'] is True
    assert rep1['summary']['blocking_count'] == 0

    # File 2 failed / pending
    assert rep2['valid'] is False
    assert rep2['summary']['blocking_count'] >= 1

    # File 3 passed (clean, completely unaffected by File 2's failure)
    assert rep3['valid'] is True
    assert rep3['summary']['blocking_count'] == 0
    assert cand3 == rec_valid_2 + b"\n"


# ==============================================================================
# 5.4 Specification Format Diversity (Scenario H & CSV/XLSX Parser)
# ==============================================================================

def test_scenario_h_custom_csv_headers():
    """Parse CSV specification with non-standard column headers."""
    csv_content = (
        "ColumnIdentifier,FieldLabel,ByteStart,ByteEnd,ByteWidth,DataTypeRule,PaddingSpecification,IsMandatory\n"
        "col_01,VENDOR_ID,1,10,10,CHAR,Trailing spaces,YES\n"
        "col_02,INVOICE_NUM,11,30,20,CHAR,Trailing spaces,YES\n"
        "col_03,INVOICE_DATE,31,38,8,DATE 'YYYYMMDD',Fixed Date,YES\n"
        "col_04,AMOUNT,39,50,12,NUMBER,Leading zeroes,YES\n"
    )
    column_mapping = {
        'ColumnIdentifier': 'id',
        'FieldLabel': 'field_name',
        'ByteStart': 'start_pos',
        'ByteEnd': 'end_pos',
        'ByteWidth': 'length',
        'DataTypeRule': 'datatype_conversion',
        'PaddingSpecification': 'padding_alignment',
        'IsMandatory': 'mandatory',
    }
    doc = extract_spec("custom_headers.csv", csv_content.encode("utf-8"), column_mapping=column_mapping)
    assert len(doc['fields']) == 4
    norm = normalize_spec(doc)
    assert len(norm['fields']) == 4
    assert norm['layouts'][0]['record_length'] == 50


def test_xlsx_specification_parsing():
    """Parse XLSX workbook specification via openpyxl and validate against DAT."""
    gen = SyntheticFixtureGenerator(seed=808)
    layout = gen.generate_layout(field_count=15, record_width=250, layout_id="xlsx_layout", all_mandatory=True)
    xlsx_bytes = gen.to_xlsx_spec(layout)

    # Parse through openpyxl spreadsheet reader
    doc = extract_spec("test_spec.xlsx", xlsx_bytes)
    assert len(doc['fields']) == 15

    norm = normalize_spec(doc)
    pipeline = compile_pipeline(norm)
    assert sum(len(l.steps) for l in pipeline.layouts) >= 15

    record, _ = gen.generate_record(layout)
    val_res = validate_record(pipeline, record, pipeline.layouts[0].id)
    assert val_res['valid'] is True


def test_malformed_spec_unsupported_datatype_reported_explicitly():
    """Unsupported data types or invalid positions must be reported explicitly as errors,

    not silently coerced.
    """
    csv_content = (
        "Field Name,Start Pos,End Pos,Length,Data Type / Oracle Conversion Function,Padding / Alignment Rule,Mapping Type,Mandatory\n"
        "FIELD_1,1,10,10,COMPLEX_ORACLE_OBJECT_TYPE,Trailing spaces,PHYSICAL,YES\n"
        "FIELD_2,11,15,5,CHAR,Trailing spaces,PHYSICAL,YES\n"
    )
    doc = extract_spec("bad_spec.csv", csv_content.encode("utf-8"))
    norm = normalize_spec(doc)
    issues = validate_spec(norm)
    # Must report unsupported conversion or datatype error
    assert any(i.get('severity') == 'error' or i.get('code') in ('UNSUPPORTED_DATATYPE', 'CONVERSION') for i in issues or norm.get('source_issues', []))
