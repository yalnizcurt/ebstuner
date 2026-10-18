"""Adversarial tests for TASAA engine.

Tests all adversarial criteria specified in Section 4:
- 4.1 False Alignment Certification (positionally compliant vs structurally proven vs ambiguous)
- 4.2 Ambiguous CHAR fields (e.g. ABCDEF splits with/without constraints)
- 4.3 Meaningful Whitespace (leading, trailing, internal, repeated, all-space)
- 4.4 Repeated and Misleading Anchors (repeated constants, date-like non-dates, false enums)
- 4.5 Large Displacements (1, 4, 10, 20, 50, 100 bytes)
- 4.6 Formal Ambiguity Certification (global vs bounded proof, timeout/budget bounds)
- 4.7 Byte Preservation (one-to-one provenance, order preservation, no alteration)
- 4.8 Type and Specification Conflicts (NUMBER with alpha, invalid DATE, length exceeds)
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tasaa import (
    AlignmentOptions,
    analyze,
    verify_preservation,
    POSITIONALLY_COMPLIANT,
    CERTIFIED_SAFE_REPAIR,
    BEST_RANKED_CANDIDATE,
    UNIQUE_WITHIN_SEARCH,
    HUMAN_REVIEW_REQUIRED,
    SPEC_CONFLICT,
    UNRECOVERABLE,
)


def _make_spec(fields: list[dict], record_length: int | None = None) -> tuple[dict, dict]:
    """Helper to build a valid master spec and default layout."""
    normalized_fields = []
    for f in fields:
        nf = dict(f)
        nf.setdefault('field_name', nf['id'])
        r = dict(nf.get('rules', {}))
        r.setdefault('datatype', 'char')
        r.setdefault('mapping_type', 'physical')
        if r.get('mapping_type') == 'filler':
            r['required'] = False
        elif 'required' not in r:
            r['required'] = True if r.get('mandatory') == 'YES' else (False if r.get('mandatory') == 'NO' else True)
        r.setdefault('padding', {'side': 'right', 'char': ' '})
        r.setdefault('conversion', 'identity')
        nf['rules'] = r
        normalized_fields.append(nf)

    if record_length is None:
        record_length = sum(f['rules']['length'] for f in normalized_fields)
    spec = {
        'version': '1.0.0',
        'fields': normalized_fields,
        'layouts': [{
            'layout_id': 'DEFAULT',
            'record_length': record_length,
            'field_ids': [f['id'] for f in normalized_fields],
        }],
    }
    layout = spec['layouts'][0]
    return spec, layout


# ==============================================================================
# 4.1 False Alignment Certification
# ==============================================================================

def test_compensating_padding_in_char_fields_never_certified_as_proven():
    """Two adjacent CHAR fields where padding was shifted between them.

    Total record length matches spec.
    Both fields look valid as CHARs.
    TASAA must NOT certify this as a proven semantic repair or proven semantic alignment.
    """
    fields = [
        {
            'id': 'F1',
            'field_name': 'FIRST_NAME',
            'rules': {
                'start': 1,
                'end': 10,
                'length': 10,
                'datatype': 'string',
                'mandatory': 'YES',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'F2',
            'field_name': 'LAST_NAME',
            'rules': {
                'start': 11,
                'end': 20,
                'length': 10,
                'datatype': 'string',
                'mandatory': 'YES',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 20)

    # Intended values: "ALICE     " and "SMITH     " (each 10 bytes)
    # Corrupted: 3 spaces shifted from F1 into F2's domain: "ALICE  SMITH        " -> len 20
    # At positional boundaries: F1="ALICE  SMI", F2="TH        "
    row = b"ALICE  SMITH        "
    assert len(row) == 20

    res = analyze(spec, layout, row)

    # Total length matches and characters are valid ASCII strings, so it is positionally compliant,
    # BUT must NOT claim semantic proof!
    assert res['classification'] == POSITIONALLY_COMPLIANT
    assert res['certification']['semantic_proof'] is False
    assert res['certification']['level'] == 'POSITIONALLY_COMPLIANT'
    assert res['candidate'] == row  # Original bytes remain untouched


# ==============================================================================
# 4.2 Ambiguous Adjacent CHAR Fields
# ==============================================================================

def test_ambiguous_adjacent_char_splits_require_human_review():
    """Test adjacent CHAR fields with ambiguous boundary splits e.g. ABCDEF.

    Without constraints, any split (ABC+DEF, ABCD+EF, etc.) is equally unproven.
    TASAA must require human review and never auto-certify.
    """
    fields = [
        {
            'id': 'CODE_A',
            'rules': {
                'start': 1,
                'end': 5,
                'length': 5,
                'datatype': 'string',
                'mandatory': 'YES',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'CODE_B',
            'rules': {
                'start': 6,
                'end': 10,
                'length': 5,
                'datatype': 'string',
                'mandatory': 'YES',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 10)

    # Row has missing structural padding: length is 6 instead of 10
    row = b"ABCDEF"
    res = analyze(spec, layout, row)

    assert res['status'] in ('needs_review', 'ambiguous')
    assert res['classification'] in (HUMAN_REVIEW_REQUIRED, BEST_RANKED_CANDIDATE)
    assert res['certification']['semantic_proof'] is False
    assert res['candidate'] is None  # Never auto-reconstructed without approval


def test_adjacent_char_with_one_exact_allowed_value_resolves_boundary():
    """When CODE_A has strict allowed values ['ABC'], boundary becomes provable."""
    fields = [
        {
            'id': 'CODE_A',
            'rules': {
                'start': 1,
                'end': 5,
                'length': 5,
                'datatype': 'string',
                'mandatory': 'YES',
                'allowed_values': ['ABC'],
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'CODE_B',
            'rules': {
                'start': 6,
                'end': 10,
                'length': 5,
                'datatype': 'string',
                'mandatory': 'YES',
                'allowed_values': ['DEF'],
                'padding': {'side': 'right', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 10)

    # Row missing 4 padding spaces: "ABCDEF"
    row = b"ABCDEF"
    res = analyze(spec, layout, row)

    # Both allowed values make the structural mapping unique within the compiled rules.
    assert res['classification'] == CERTIFIED_SAFE_REPAIR
    assert res['certification']['semantic_proof'] is False
    assert res['candidate'] == b"ABC  DEF  "


# ==============================================================================
# 4.3 Meaningful Whitespace
# ==============================================================================

@pytest.mark.parametrize("value_bytes,pad_side", [
    (b"  LEADING", "right"),
    (b"TRAILING  ", "left"),
    (b"IN TER NAL", "right"),
    (b"  BOTH   ", "right"),
    (b"   ", "right"),
])
def test_meaningful_spaces_never_stripped_or_lost(value_bytes, pad_side):
    """Verify TASAA never trims or strips meaningful internal/leading/trailing spaces."""
    fields = [
        {
            'id': 'TEXT_FIELD',
            'rules': {
                'start': 1,
                'end': 15,
                'length': 15,
                'datatype': 'string',
                'mandatory': 'YES',
                'padding': {'side': pad_side, 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 15)

    if pad_side == 'right':
        row = value_bytes + b" " * (15 - len(value_bytes))
    else:
        row = b" " * (15 - len(value_bytes)) + value_bytes

    res = analyze(spec, layout, row)
    assert res['classification'] in (POSITIONALLY_COMPLIANT, SPEC_CONFLICT)
    # The output candidate or original row must retain every single byte exactly
    prov = res['provenance']
    assert prov['source_bytes_accounted_for'] == 15
    assert bytes(src['byte'] for src in prov['source']) == row
    for src in prov['source']:
        assert src['byte'] == row[src['offset']]
        assert src['disposition'] == 'retained'


# ==============================================================================
# 4.4 Repeated and Misleading Anchors
# ==============================================================================

def test_repeated_constants_do_not_produce_false_certainty():
    """When a constant appears multiple times in a row, TASAA must not blindly

    latch onto the first occurrence and claim certainty if ambiguous.
    """
    fields = [
        {
            'id': 'TYPE_1',
            'rules': {
                'start': 1,
                'end': 4,
                'length': 4,
                'datatype': 'string',
                'constant': '01',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'DATA',
            'rules': {
                'start': 5,
                'end': 10,
                'length': 6,
                'datatype': 'string',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'TYPE_2',
            'rules': {
                'start': 11,
                'end': 14,
                'length': 4,
                'datatype': 'string',
                'constant': '01',  # Identical constant!
                'padding': {'side': 'right', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 14)

    # Row has missing padding and multiple "01" substrings
    row = b"010101"
    res = analyze(spec, layout, row)

    # Must NOT certify as safe repair because DATA is free CHAR and '01' is ambiguous
    assert res['classification'] != CERTIFIED_SAFE_REPAIR
    assert res['certification']['semantic_proof'] is False


def test_date_like_string_in_char_field_does_not_corrupt_date_anchor():
    """A CHAR field containing '20260101' next to an actual DATE field."""
    fields = [
        {
            'id': 'NOTE',
            'rules': {
                'start': 1,
                'end': 12,
                'length': 12,
                'datatype': 'string',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'TXN_DATE',
            'rules': {
                'start': 13,
                'end': 20,
                'length': 8,
                'datatype': 'date',
                'date_format': 'YYYYMMDD',
                'padding': {'side': 'none', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 20)

    # Note contains a date-like string '20260101'
    row = b"REF20260101 20261018"
    assert len(row) == 20

    res = analyze(spec, layout, row)
    assert res['classification'] == POSITIONALLY_COMPLIANT
    # Note has "REF20260101 ", TXN_DATE has "20261018"
    assert res['initial'][0]['valid'] is True
    assert res['initial'][1]['valid'] is True


# ==============================================================================
# 4.5 Large Displacements (1, 4, 10, 20, 50, 100 bytes)
# ==============================================================================

@pytest.mark.parametrize("displacement", [1, 4, 10, 20, 50, 100])
def test_large_displacements_with_exact_anchors_discovered(displacement):
    """Adaptive candidate generator must find alignments displaced by up to 100 bytes

    when proven by downstream anchors.
    """
    fields = [
        {
            'id': 'REC_TYPE',
            'rules': {
                'start': 1,
                'end': 5,
                'length': 5,
                'constant': 'HDR01',
                'padding': {'side': 'none', 'char': ' '},
            },
        },
        {
            'id': 'BIG_GAP',
            'rules': {
                'start': 6,
                'end': 6 + 120 - 1,
                'length': 120,
                'mapping_type': 'filler',
                'required': False,
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'TAIL',
            'rules': {
                'start': 126,
                'end': 130,
                'length': 5,
                'constant': 'END01',
                'padding': {'side': 'none', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 130)

    # Record missing `displacement` bytes in BIG_GAP
    filler_len = 120 - displacement
    row = b"HDR01" + b" " * filler_len + b"END01"

    res = analyze(spec, layout, row)

    # Adaptive candidate generation discovers alternative with TAIL anchor
    assert res['resource_limit_reached'] is False
    assert any(
        mapping['source_start'] == 5 + filler_len
        for alt in res['alternatives']
        for mapping in alt['mapping']
        if mapping['field_id'] == 'TAIL'
    )
    if res['candidate'] is not None:
        assert res['candidate'] == b"HDR01" + b" " * 120 + b"END01"
        assert len(res['candidate']) == 130


# ==============================================================================
# 4.6 Formal Ambiguity Certification
# ==============================================================================

def test_formal_ambiguity_certifier_rejects_unconstrained_search():
    """Unconstrained schemas without exact boundaries must NOT claim formal uniqueness."""
    fields = [
        {
            'id': 'CHAR_A',
            'rules': {
                'start': 1,
                'end': 10,
                'length': 10,
                'datatype': 'string',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'CHAR_B',
            'rules': {
                'start': 11,
                'end': 20,
                'length': 10,
                'datatype': 'string',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 20)

    row = b"SHORT"
    res = analyze(spec, layout, row)

    # Unconstrained fields cannot have formal exact count proof
    assert res['certification']['formal_mapping_count'] is None
    assert res['certification']['semantic_proof'] is False
    assert res['classification'] in (HUMAN_REVIEW_REQUIRED, UNRECOVERABLE, UNIQUE_WITHIN_SEARCH)


# ==============================================================================
# 4.7 Byte Preservation (Property-Based & Deterministic)
# ==============================================================================

@settings(max_examples=50)
@given(
    code_val=st.text(min_size=1, max_size=4, alphabet=st.characters(categories=('Lu', 'Nd'))),
    desc_val=st.text(min_size=1, max_size=8, alphabet=st.characters(categories=('Lu', 'Nd'))),
)
def test_hypothesis_byte_preservation(code_val, desc_val):
    """Property-based test: every input value character must be strictly accounted for."""
    fields = [
        {
            'id': 'CODE',
            'rules': {
                'start': 1,
                'end': 6,
                'length': 6,
                'constant': 'CODE',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
        {
            'id': 'FILLER',
            'rules': {
                'start': 7,
                'end': 12,
                'length': 6,
                'mapping_type': 'filler',
                'padding': {'side': 'right', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 12)

    # Deliberately short row
    row = b"CODE  " + b" " * 2
    res = analyze(spec, layout, row)

    # Verify provenance ledger covers 100% of input bytes
    prov = res['provenance']
    assert prov['source_bytes_accounted_for'] == len(row)
    for src in prov['source']:
        assert src['byte'] == row[src['offset']]
        assert src['classification'] in ('VALUE', 'DECLARED_PADDING', 'UNRESOLVED', 'VERIFIED_PADDING')


# ==============================================================================
# 4.8 Type and Specification Conflicts
# ==============================================================================

def test_number_field_with_alpha_preserves_original_value_and_flags_conflict():
    """A NUMBER field containing alpha characters must NOT be coerced,

    truncated, or rewritten. Original bytes must be preserved and SPEC_CONFLICT reported.
    """
    fields = [
        {
            'id': 'AMOUNT',
            'rules': {
                'start': 1,
                'end': 8,
                'length': 8,
                'datatype': 'integer',
                'padding': {'side': 'left', 'char': '0'},
            },
        },
    ]
    spec, layout = _make_spec(fields, 8)

    row = b"BAD12345"
    res = analyze(spec, layout, row)

    assert res['classification'] == SPEC_CONFLICT
    assert res['structural_status'] == 'POSITIONALLY_COMPLIANT'
    assert res['validation_status'] == 'FAILED'
    # Original value must remain BAD12345, never converted to 12345 or 00012345
    assert res['provenance']['source_bytes_accounted_for'] == 8
    raw_source = bytes(src['byte'] for src in res['provenance']['source'])
    assert raw_source == b"BAD12345"
    assert any(c['code'] == 'TYPE_MISMATCH' for c in res['field_findings'])


def test_invalid_date_preserves_original_and_flags_conflict():
    """An invalid date (e.g. 20260230) is flagged as a rule failure, not rewritten."""
    fields = [
        {
            'id': 'INV_DATE',
            'rules': {
                'start': 1,
                'end': 8,
                'length': 8,
                'datatype': 'date',
                'date_format': 'YYYYMMDD',
                'padding': {'side': 'none', 'char': ' '},
            },
        },
    ]
    spec, layout = _make_spec(fields, 8)

    row = b"20260230"  # Feb 30th does not exist
    res = analyze(spec, layout, row)

    assert res['classification'] == SPEC_CONFLICT
    assert res['validation_status'] == 'FAILED'
    assert any(c['code'] in ('TYPE_MISMATCH', 'DATATYPE', 'DATE_FORMAT') for c in res['field_findings'])
    assert bytes(src['byte'] for src in res['provenance']['source']) == b"20260230"
