"""Tests for Milestone 1 TASAA correctness, ambiguity certifier classifications,
advanced anchor detection, beam state compactness, and byte provenance ledger.
"""
from datetime import datetime
import pytest

from tasaa import (
    AlignmentOptions,
    AlignmentState,
    AnchorDetector,
    analyze,
    verify_preservation,
    _signature,
    _formal_exact_count,
    POSITIONALLY_COMPLIANT,
    BEST_RANKED_CANDIDATE,
    UNIQUE_WITHIN_SEARCH,
    CERTIFIED_SAFE_REPAIR,
    HUMAN_REVIEW_REQUIRED,
    SPEC_CONFLICT,
    UNRECOVERABLE,
)


def make_field(field_id, name, start, width, kind='char', pad_side='right', pad_char=' ',
               required=False, constant=None, allowed_values=None, date_format=None,
               mapping_type='physical', source=None, conditional_rules=''):
    return {
        'id': field_id,
        'field_name': name,
        'source': source or {'row': 10, 'cell': 'B10'},
        'source_reference': 'CSV!10',
        'conditional_rules': conditional_rules,
        'rules': {
            'start': start,
            'end': start + width - 1,
            'length': width,
            'datatype': kind,
            'conversion': 'identity',
            'required': required,
            'mapping_type': mapping_type,
            'padding': {'side': pad_side, 'char': pad_char},
            'constant': constant,
            'allowed_values': allowed_values,
            'date_format': date_format,
        }
    }


def test_field_signature_compiler_detailed_metadata():
    field = make_field('f1', 'PAYMENT_DATE', 5, 8, kind='date', pad_side='right',
                       required=True, date_format='YYYYMMDD', source={'row': 5, 'col': 2})
    sig = _signature(field, sequence=2)
    assert sig['id'] == 'f1'
    assert sig['sequence'] == 2
    assert sig['start'] == 5
    assert sig['end'] == 12
    # 0-based end-exclusive internal positions
    assert sig['byte_start'] == 4
    assert sig['byte_end'] == 12
    assert sig['target_start'] == 4
    assert sig['target_end'] == 12
    assert sig['length'] == 8
    assert sig['datatype'] == 'date'
    assert sig['alignment_direction'] == 'left'  # right padding = left alignment
    assert sig['mandatory_status'] == 'YES'
    assert sig['source_ref'] == 'CSV!10'
    assert sig['validation_formats']['date_format'] == 'YYYYMMDD'


def test_anchor_detector_types_and_neighboring_relationships():
    f0 = make_field('f0', 'RECORD_TYPE', 1, 2, constant='01')
    f1 = make_field('f1', 'TRX_DATE', 3, 8, kind='date', date_format='YYYYMMDD')
    f2 = make_field('f2', 'AMOUNT', 11, 6, kind='decimal')
    fields = [f0, f1, f2]
    row = b'0120231018 12.34'
    detector = AnchorDetector(fields, row, 'ascii')
    assert len(detector.all) >= 3
    # Verify exact constant / record identifier
    f0_anchors = detector.by_field['f0']
    assert any(a['anchorType'] in ('EXACT_CONSTANT', 'RECORD_IDENTIFIER') and a['reliability'] == 'HIGH' for a in f0_anchors)
    # Verify strict date
    f1_anchors = detector.by_field['f1']
    assert any(a['anchorType'] == 'STRICT_DATE' and a['matchedValue'] == '20231018' for a in f1_anchors)
    # Verify numeric decimal
    f2_anchors = detector.by_field['f2']
    assert any(a['anchorType'] == 'NUMERIC_DECIMAL' and a['matchedValue'] == '12.34' for a in f2_anchors)
    # Verify neighboring field traceable evidence
    assert any('Consistent sequence' in ev for a in f0_anchors for ev in a['evidence'])


def test_ambiguity_certifier_seven_classifications():
    # 1. POSITIONALLY_COMPLIANT
    f0 = make_field('f0', 'CODE', 1, 3, constant='ABC')
    f1 = make_field('f1', 'NUM', 4, 3, kind='integer')
    spec = {'fields': [f0, f1]}
    layout = {'field_ids': ['f0', 'f1'], 'record_length': 6}
    res_compliant = analyze(spec, layout, b'ABC123')
    assert res_compliant['classification'] == POSITIONALLY_COMPLIANT
    assert res_compliant['structural_status'] == 'POSITIONALLY_COMPLIANT'
    assert res_compliant['status'] == 'already_valid'

    # 2. CERTIFIED_SAFE_REPAIR
    f0_c = make_field('f0', 'A', 1, 5, constant='AB')
    f1_c = make_field('f1', 'B', 6, 3, constant='XYZ')
    res_certified = analyze({'fields': [f0_c, f1_c]}, {'field_ids': ['f0', 'f1'], 'record_length': 8}, b'ABXYZ')
    assert res_certified['classification'] == CERTIFIED_SAFE_REPAIR
    assert res_certified['structural_status'] == 'STRUCTURALLY_REPAIRABLE'
    assert res_certified['status'] == 'certified'
    assert res_certified['candidate'] == b'AB   XYZ'

    # 3. BEST_RANKED_CANDIDATE (multiple plausible paths, ambiguous spaces)
    f0_amb = make_field('f0', 'A', 1, 4)
    f1_amb = make_field('f1', 'B', 5, 4)
    res_amb = analyze({'fields': [f0_amb, f1_amb]}, {'field_ids': ['f0', 'f1'], 'record_length': 8}, b'   ')
    assert res_amb['classification'] in (BEST_RANKED_CANDIDATE, HUMAN_REVIEW_REQUIRED)
    assert res_amb['status'] == 'ambiguous'

    # 4. SPEC_CONFLICT (positionally compliant bytes with hard type failure)
    f_int = make_field('f0', 'NUM', 1, 6, kind='integer')
    res_conflict = analyze({'fields': [f_int]}, {'field_ids': ['f0'], 'record_length': 6}, b'ABCDEF')
    assert res_conflict['classification'] == SPEC_CONFLICT
    assert res_conflict['structural_status'] == 'POSITIONALLY_COMPLIANT'
    assert res_conflict['validation_status'] == 'FAILED'

    # 5. UNRECOVERABLE (empty or conflicting layout)
    res_unrec = analyze({'fields': [make_field('f0', 'A', 1, 2), make_field('f1', 'B', 2, 2)]},
                        {'field_ids': ['f0', 'f1'], 'record_length': 3}, b'ABC')
    assert res_unrec['classification'] == UNRECOVERABLE
    assert res_unrec['status'] == 'unsupported'

    # 6. HUMAN_REVIEW_REQUIRED (unknown mandatory status preventing certified repair)
    f0_unk = make_field('f0', 'A', 1, 5, constant='AB', required=None)
    f1_unk = make_field('f1', 'B', 6, 3, constant='XYZ', required=None)
    res_unk = analyze({'fields': [f0_unk, f1_unk]}, {'field_ids': ['f0', 'f1'], 'record_length': 8}, b'ABXYZ')
    assert res_unk['classification'] == HUMAN_REVIEW_REQUIRED
    assert res_unk['status'] == 'needs_review'
    assert res_unk['candidate'] is None


def test_formal_exact_count_with_strict_dates_and_constants():
    f0 = make_field('f0', 'HEADER', 1, 4, constant='HEAD')
    f1 = make_field('f1', 'DT', 5, 8, kind='date', date_format='YYYYMMDD')
    f2 = make_field('f2', 'TAIL', 13, 3, constant='END')
    fields = [f0, f1, f2]
    row = b'HEAD20261018END'
    count, path = _formal_exact_count(fields, row, 'ascii', 1000)
    assert count == 1
    assert path == ((0, 4), (4, 12), (12, 15))


def test_record_separator_in_provenance_ledger():
    f0 = make_field('f0', 'DATA', 1, 4, constant='TEST')
    mapping = [{
        'field_id': 'f0',
        'source_start': 0,
        'source_end': 4,
        'target_start': 0,
        'target_end': 4,
        'value_offsets': (0, 1, 2, 3),
        'padding_offsets': (),
        'unresolved_offsets': (),
        'left_insert': 0,
        'padding_byte': 32,
        'padding_evidence': [],
    }]
    row = b'TEST\r\n'
    output = b'TEST'
    provenance = verify_preservation(row, output, mapping)
    assert provenance is not None
    assert provenance['source_bytes_accounted_for'] == 6
    delimiters = [e for e in provenance['source'] if e['classification'] == 'RECORD_SEPARATOR']
    assert len(delimiters) == 2
    assert [d['byte'] for d in delimiters] == [13, 10]


def test_downstream_anchor_displacement_propagation():
    # Field 0 has missing padding (width 6, but only 2 bytes present)
    # Field 1 is free text (width 4)
    # Field 2 has an exact anchor 'END' (width 3)
    f0 = make_field('f0', 'A', 1, 6, constant='AA')
    f1 = make_field('f1', 'B', 7, 4)
    f2 = make_field('f2', 'C', 11, 3, constant='END')
    spec = {'fields': [f0, f1, f2]}
    layout = {'field_ids': ['f0', 'f1', 'f2'], 'record_length': 13}
    # Row: 'AA' (missing 4 spaces) + 'DATA' + 'END' = 2 + 4 + 3 = 9 bytes
    row = b'AADATAEND'
    result = analyze(spec, layout, row)
    assert any(
        mapping['field_id'] == 'f2' and mapping['source_start'] == 6
        for alt in result['alternatives'] for mapping in alt['mapping']
    )
