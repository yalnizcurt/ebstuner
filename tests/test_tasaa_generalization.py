"""Independent generated specifications exercise the same TASAA entry point."""

import pytest

from fixtures.synthetic_generator import SyntheticFixtureGenerator
from specs import normalize_spec
from tasaa import analyze
from validation_pipeline import compile_pipeline, validate_record


@pytest.mark.parametrize('seed,count,width', [
    (11, 4, 37), (22, 9, 108), (33, 17, 255), (44, 55, 3303), (55, 180, 20016),
])
def test_five_independent_master_specs_use_unchanged_engine(seed, count, width):
    generator = SyntheticFixtureGenerator(seed)
    source = generator.generate_layout(count, width, layout_id=f'generated_{seed}')
    approved = normalize_spec(generator.to_master_spec_dict(source))
    pipeline = compile_pipeline(approved)
    record, ground_truth = generator.generate_record(source)
    assert len(approved['fields']) == count
    assert len(record) == width
    assert {field['rules']['datatype'] for field in approved['fields']} >= {'char', 'integer', 'decimal', 'date'}
    assert {field['rules']['padding']['side'] for field in approved['fields']} >= {'left', 'right'}
    if count >= 9:
        assert any(field['rules']['padding']['side'] == 'none' for field in approved['fields'])
    assert validate_record(pipeline, record, source.layout_id)['valid']

    result = analyze(approved, approved['layouts'][0], record)
    assert result['status'] == 'already_valid'
    assert result['candidate'] == record
    assert result['certification']['semantic_proof'] is False
    assert [entry['offset'] for entry in result['ledger']] == list(range(width))
    assert {entry['owner'] for entry in result['ledger']} == {item['field_id'] for item in ground_truth}

    damaged = record[:ground_truth[0]['value_end']] + record[ground_truth[0]['value_end'] + 1:]
    uncertain = analyze(approved, approved['layouts'][0], damaged)
    assert uncertain['status'] != 'already_valid'
    assert uncertain['provenance']['source_bytes_accounted_for'] == len(damaged)
    assert uncertain['candidate'] is None or validate_record(pipeline, uncertain['candidate'], source.layout_id)['valid']


def test_certified_structural_repair_does_not_claim_semantic_proof():
    fields = []
    for identifier, start, width, value in [('alpha', 1, 5, 'AB'), ('omega', 6, 3, 'XYZ')]:
        fields.append({'id': identifier, 'field_name': identifier, 'rules': {
            'start': start, 'end': start + width - 1, 'length': width,
            'mapping_type': 'physical', 'datatype': 'char', 'conversion': 'identity',
            'required': True, 'allowed_values': [value], 'padding': {'side': 'right', 'char': ' '},
        }})
    result = analyze({'fields': fields}, {'field_ids': ['alpha', 'omega'], 'record_length': 8}, b'ABXYZ')
    assert result['status'] == 'certified'
    assert result['candidate'] == b'AB   XYZ'
    assert result['certification']['semantic_proof'] is False
