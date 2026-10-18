from tasaa import analyze


def field(identifier, start, width, kind='physical'):
    return {'id': identifier, 'field_name': identifier, 'rules': {
        'start': start, 'end': start + width - 1, 'length': width,
        'datatype': 'char', 'conversion': 'identity', 'required': False,
        'mapping_type': kind, 'padding': {'side': 'right', 'char': ' '}}}


def test_unowned_gap_is_accounted_for_and_blocks_repair():
    fields = [field('A', 1, 2), field('B', 5, 2)]
    result = analyze({'fields': fields}, {'field_ids': ['A', 'B'], 'record_length': 6}, b'AB!?CD')
    assert result['status'] == 'needs_review'
    assert result['candidate'] is None
    assert result['ambiguity_regions'][0]['source_start'] == 2
    assert result['ambiguity_regions'][0]['source_end'] == 4
    assert [x['classification'] for x in result['ledger']] == [
        'VALUE', 'VALUE', 'UNRESOLVED', 'UNRESOLVED', 'VALUE', 'VALUE']
    assert [x['owner'] for x in result['ledger']] == ['A', 'A', None, None, 'B', 'B']
    assert result['provenance']['source_bytes_accounted_for'] == 6


def test_explicit_filler_owns_gap_without_discarding_original_bytes():
    fields = [field('A', 1, 2), field('RESERVED', 3, 2, 'filler'), field('B', 5, 2)]
    row = b'AB!?CD'
    result = analyze({'fields': fields},
                     {'field_ids': ['A', 'RESERVED', 'B'], 'record_length': 6}, row)
    assert result['status'] == 'already_valid'
    assert result['candidate'] == row
    assert [x['owner'] for x in result['ledger']] == ['A', 'A', 'RESERVED', 'RESERVED', 'B', 'B']


def test_trailing_unowned_region_and_overlap_are_distinct():
    first = field('A', 1, 2)
    trailing = analyze({'fields': [first]}, {'field_ids': ['A'], 'record_length': 4}, b'AB!?')
    assert trailing['status'] == 'needs_review'
    assert trailing['ambiguity_regions'][0]['source_start'] == 2
    overlapping = analyze({'fields': [first, field('B', 2, 2)]},
                          {'field_ids': ['A', 'B'], 'record_length': 3}, b'ABC')
    assert overlapping['status'] == 'unsupported'
