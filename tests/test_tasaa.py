import random

import pytest

from tasaa import AlignmentOptions, analyze, verify_preservation


def certification_level(result):
    certification = result['certification']
    return certification.get('level') if isinstance(certification, dict) else certification


def setup(widths, *, kinds=None, pads=None, values=None):
    kinds = kinds or ['char'] * len(widths)
    pads = pads or ['right'] * len(widths)
    values = values or [None] * len(widths)
    fields = []
    cursor = 1
    for i, width in enumerate(widths):
        rules = {'start': cursor, 'end': cursor + width - 1, 'length': width,
                 'datatype': kinds[i], 'conversion': 'identity', 'required': False,
                 'mapping_type': 'physical', 'padding': {'side': pads[i], 'char': ' '}}
        if values[i] is not None:
            rules['allowed_values'] = [values[i]]
        fields.append({'id': f'f{i}', 'field_name': f'Field {i}', 'rules': rules})
        cursor += width
    return {'fields': fields}, {'field_ids': [f['id'] for f in fields], 'record_length': cursor - 1}


def test_valid_initial_positions_and_ledger():
    spec, layout = setup([4, 3], kinds=['char', 'integer'])
    result = analyze(spec, layout, b'AB  123')
    assert result['status'] == 'already_valid'
    assert result['candidate'] == b'AB  123'
    assert len(result['ledger']) == 7


def test_missing_padding_unique_with_anchor():
    spec, layout = setup([5, 3], values=['AB', 'XYZ'])
    result = analyze(spec, layout, b'ABXYZ')
    assert result['status'] == 'certified'
    assert result['candidate'] == b'AB   XYZ'
    assert [item['offset'] for item in result['ledger']] == list(range(5))
    assert all(item['disposition'] == 'retained' for item in result['ledger'])


def test_extra_padding_is_accounted_for():
    spec, layout = setup([4, 3], values=['AB', 'XYZ'])
    result = analyze(spec, layout, b'AB    XYZ')
    assert result['status'] == 'certified'
    assert result['candidate'] == b'AB  XYZ'
    assert [x['offset'] for x in result['ledger']] == list(range(9))
    assert sum(x['disposition'] == 'declared_padding' for x in result['ledger']) == 4


def test_ambiguous_adjacent_char_fields_never_reconstruct_automatically():
    spec, layout = setup([3, 3])
    result = analyze(spec, layout, b'    ')
    assert result['status'] == 'ambiguous'
    assert result['candidate'] is None
    assert len(result['alternatives']) > 1


def test_type_mismatch_and_oversize_preserve_source():
    spec, layout = setup([3], kinds=['integer'])
    assert analyze(spec, layout, b'ABC')['candidate'] is None
    assert analyze(spec, layout, b'12345')['candidate'] is None


def test_180_fields_and_more_than_20000_positions():
    spec, layout = setup([112] * 179 + [113])
    row = b''.join(b'X'.ljust(f['rules']['length'], b' ') for f in spec['fields'])
    assert len(row) > 20000
    result = analyze(spec, layout, row)
    assert result['status'] == 'already_valid'
    assert len(result['signatures']) == 180
    assert len(result['ledger']) == len(row)


def test_randomized_certified_repair_preserves_every_value_byte():
    rng = random.Random(9102)
    for _ in range(50):
        left, right = rng.randint(3, 12), rng.randint(3, 12)
        first = ''.join(rng.choices('ABCDEFGH', k=2))
        second = ''.join(rng.choices('JKLMNPQR', k=2))
        spec, layout = setup([left, right], values=[first, second])
        row = (first + ' ' * rng.randrange(left - 1) + second).encode()
        result = analyze(spec, layout, row, max_shift=12)
        if result['status'] == 'certified':
            assert result['candidate'].replace(b' ', b'') == row.replace(b' ', b'')
            assert [item['offset'] for item in result['ledger']] == list(range(len(row)))
        else:
            assert result['candidate'] is None


def test_duplicate_display_names_do_not_merge_byte_owners():
    spec, layout = setup([3, 3], values=['ABC', 'XYZ'])
    spec['fields'][1]['field_name'] = spec['fields'][0]['field_name']
    result = analyze(spec, layout, b'ABCXYZ')
    assert result['status'] == 'already_valid'
    assert {item['owner'] for item in result['ledger']} == {'f0', 'f1'}


def test_distinct_selected_layouts_for_mixed_records():
    first, first_layout = setup([2, 3], values=['AA', '123'])
    second, second_layout = setup([3, 2], values=['BBB', '45'])
    assert analyze(first, first_layout, b'AA123')['status'] == 'already_valid'
    assert analyze(second, second_layout, b'BBB45')['status'] == 'already_valid'


def test_unknown_mandatory_prevents_certification():
    spec, layout = setup([4], values=['AB'])
    spec['fields'][0]['rules']['required'] = None
    result = analyze(spec, layout, b'AB')
    assert result['candidate'] is None
    assert result['status'] == 'needs_review'


@pytest.mark.parametrize('missing', [3, 20, 100])
def test_adaptive_search_finds_large_missing_padding_from_exact_anchor(missing):
    spec, layout = setup([missing + 1, 3], values=['A', 'END'])
    result = analyze(spec, layout, b'AEND')
    assert any(
        mapping['source_start'] == 1
        for alternative in result['alternatives']
        for mapping in alternative['mapping']
        if mapping['field_id'] == 'f1'
    )
    assert result['resource_limit_reached'] is False
    if result['candidate'] is not None:
        assert result['candidate'] == b'A' + b' ' * missing + b'END'


def test_positionally_compliant_char_fields_do_not_claim_semantic_proof():
    spec, layout = setup([4, 4])
    row = b'AB CD   '
    result = analyze(spec, layout, row)
    assert result['candidate'] == row
    assert result['structural_status'] == 'POSITIONALLY_COMPLIANT'
    assert certification_level(result) != 'GLOBALLY_CERTIFIED_WITHIN_FORMAL_CONSTRAINT_MODEL'


def test_meaningful_trailing_spaces_are_not_silently_discarded():
    spec, layout = setup([4, 3], values=[None, 'END'])
    row = b'AB   END'  # An extra space may be part of the CHAR value.
    result = analyze(spec, layout, row)
    assert result['candidate'] is None
    assert result['status'] in {'ambiguous', 'needs_review'}
    assert any(item['offset'] == 4 for item in result['ledger'])


def test_type_conflict_retains_structural_mapping_and_original_value():
    spec, layout = setup([8], kinds=['integer'])
    row = b'AB123456'
    result = analyze(spec, layout, row)
    assert result['structural_status'] == 'POSITIONALLY_COMPLIANT'
    assert result['validation_status'] != 'VALID'
    assert any(finding['code'] == 'TYPE_MISMATCH' for finding in result['field_findings'])
    assert result['candidate'] in {None, row}
    assert result['provenance']['source_bytes_accounted_for'] == len(row)


def test_unknown_requiredness_does_not_suppress_alignment_search():
    spec, layout = setup([5, 3], values=['AB', 'END'])
    spec['fields'][0]['rules']['required'] = None
    result = analyze(spec, layout, b'ABEND')
    assert result['alternatives']
    assert any(finding['code'] == 'MANDATORY_UNVERIFIED' for finding in result['field_findings'])
    assert result['candidate'] is None


def test_invalid_date_is_a_rule_finding_not_a_value_rewrite():
    spec, layout = setup([8])
    spec['fields'][0]['rules'].update(datatype='date', date_format='YYYYMMDD')
    row = b'20230229'
    result = analyze(spec, layout, row)
    assert result['candidate'] in {None, row}
    assert result['validation_status'] != 'VALID'
    assert result['field_findings']


def test_conflicting_exact_anchors_do_not_certify_repair():
    spec, layout = setup([3, 3], values=['ABC', 'XYZ'])
    result = analyze(spec, layout, b'XYZABC')
    assert result['candidate'] is None
    assert result['status'] != 'certified'
    assert result['spec_conflicts'] or result['field_findings']


def test_beam_pruning_cannot_claim_exhaustive_or_global_uniqueness():
    spec, layout = setup([4, 4, 4])
    result = analyze(spec, layout, b'A B C D E', beam_width=1)
    assert result['candidate'] is None
    assert result['exhaustive'] is False or result['resource_limit_reached']
    assert certification_level(result) != 'GLOBALLY_CERTIFIED_WITHIN_FORMAL_CONSTRAINT_MODEL'


def test_randomized_preservation_and_idempotence():
    rng = random.Random(161803)
    for _ in range(80):
        left = rng.randint(3, 15)
        right = rng.randint(3, 15)
        first = ''.join(rng.choices('ABCDEFGH', k=2))
        second = ''.join(rng.choices('JKLMNPQR', k=2))
        spec, layout = setup([left, right], values=[first, second])
        valid = (first.ljust(left) + second.ljust(right)).encode()
        assert analyze(spec, layout, valid)['candidate'] == valid
        delta = rng.randint(-min(left - 2, 3), 3)
        row = (first + ' ' * (left - len(first) + delta) + second.ljust(right)).encode()
        result = analyze(spec, layout, row)
        if result['status'] == 'certified':
            repaired = result['candidate']
            assert repaired is not None
            assert repaired.replace(b' ', b'') == row.replace(b' ', b'')
            again = analyze(spec, layout, repaired)
            assert again['candidate'] == repaired
        else:
            assert result['candidate'] in {None, row}
        assert [item['offset'] for item in result['ledger']] == list(range(len(row)))


def test_overwidth_value_is_never_truncated():
    spec, layout = setup([4, 3], values=['ABCD', 'END'])
    result = analyze(spec, layout, b'ABCDEEND')
    assert result['candidate'] is None
    assert result['status'] != 'certified'
    assert result['provenance']['source_bytes_accounted_for'] == 8


def test_explicit_filler_region_owns_its_bytes():
    spec, layout = setup([3, 4, 3], values=['HDR', None, 'END'])
    spec['fields'][1]['rules']['mapping_type'] = 'filler'
    row = b'HDR    END'
    result = analyze(spec, layout, row)
    assert result['candidate'] == row
    assert {item['owner'] for item in result['ledger']} == {'f0', 'f1', 'f2'}
    assert [item['offset'] for item in result['ledger']] == list(range(len(row)))


def test_multibyte_text_uses_byte_positions():
    spec, layout = setup([2, 3])
    row = 'é'.encode('utf-8') + b'END'
    result = analyze(spec, layout, row, encoding='utf-8')
    assert result['candidate'] == row
    assert [item['offset'] for item in result['ledger']] == list(range(5))


def test_compensating_padding_changes_do_not_prove_char_boundaries():
    spec, layout = setup([4, 4, 3], values=[None, None, 'END'])
    row = b'AB   C  END'  # One byte moves between the two free-text regions.
    result = analyze(spec, layout, row)
    assert result['candidate'] in {None, row}
    assert certification_level(result) != 'GLOBALLY_CERTIFIED_WITHIN_FORMAL_CONSTRAINT_MODEL'


def test_preservation_verifier_rejects_same_bytes_in_wrong_order():
    spec, layout = setup([5, 3], values=['AB', 'XYZ'])
    row = b'ABXYZ'
    result = analyze(spec, layout, row)
    mapping = result['alternatives'][0]['mapping']
    output = result['candidate']
    assert verify_preservation(row, output, mapping)['source_bytes_accounted_for'] == len(row)
    assert verify_preservation(row, output[:5] + b'XZY', mapping) is None


def test_certified_output_has_ordered_one_to_one_value_provenance():
    spec, layout = setup([5, 3], values=['AB', 'XYZ'])
    row = b'ABXYZ'
    result = analyze(spec, layout, row)
    assert result['status'] == 'certified'
    source = result['provenance']['source']
    assert [entry['offset'] for entry in source] == list(range(len(row)))
    target_offsets = [entry['target_offset'] for entry in source if entry['classification'] == 'VALUE']
    assert target_offsets == sorted(set(target_offsets))
    assert all(result['candidate'][entry['target_offset']] == entry['byte'] for entry in source if entry['classification'] == 'VALUE')
    assert {entry['classification'] for entry in result['provenance']['output']} == {'INSERTED_PADDING'}


def test_search_resource_limit_requires_review():
    spec, layout = setup([5, 3], values=['AB', 'XYZ'])
    result = analyze(spec, layout, b'ABXYZ', options=AlignmentOptions(max_states=1))
    assert result['resource_limit_reached'] is True
    assert result['candidate'] is None
    assert result['classification'] == 'HUMAN_REVIEW_REQUIRED'
    assert 'limit' in result['reason'].lower()
    assert certification_level(result) == 'HUMAN_REVIEW_REQUIRED'
