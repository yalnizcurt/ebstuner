import pytest

from specs import SpecError, normalize_spec
from validation_pipeline import compile_pipeline, validate_record


def field(name, start, width, kind='CHAR', mandatory='NO', **rules):
    return {'id': name, 'field_name': name, 'mapping_type': 'POSITION', 'start_pos': str(start),
            'end_pos': str(start + width - 1), 'length': str(width),
            'datatype_conversion': kind, 'padding_alignment': 'Fixed Text (Exact Width)',
            'mandatory': mandatory, 'rules': rules}


def approved(fields, layouts=None, **options):
    return normalize_spec({'fields': fields, 'layouts': layouts or [], 'options': options})


def test_compiled_steps_cover_exact_bytes_and_unknown_mandatory_is_visible():
    spec = approved([field('CODE', 1, 2, mandatory='UNKNOWN', allowed_values=['AA']),
                     field('DATE', 3, 8, kind='DATE "YYYYMMDD"')],
                    acknowledge_unknown_mandatory=True)
    pipeline = compile_pipeline(spec)
    assert pipeline.version == 1
    assert [step.code for step in pipeline.layouts[0].steps if step.field_id == 'CODE'] == [
        'WIDTH', 'MANDATORY_UNVERIFIED', 'CONTROL_CHARACTERS', 'PADDING', 'ALLOWED_VALUES']
    assert pipeline.coverage_limitations[0]['code'] == 'UNKNOWN_MANDATORY'
    good = validate_record(pipeline, b'AA20240229')
    assert not good['valid']
    assert next(c for c in good['checks'] if c['code'] == 'MANDATORY_UNVERIFIED')['passed'] is None
    bad = validate_record(pipeline, b'BB20230229')
    assert not bad['valid']
    assert {'ALLOWED_VALUES', 'DATATYPE'} <= {x['code'] for x in bad['issues']}
    assert not validate_record(pipeline, b'AA2024022')['valid']


def test_rejects_unsupported_constraints_and_unknown_without_acknowledgment():
    with pytest.raises(SpecError) as exc:
        compile_pipeline(approved([field('A', 1, 1, conditional_rules='if B then required')]))
    assert 'UNSUPPORTED_RULE' in {x['code'] for x in exc.value.issues}
    with pytest.raises(SpecError) as exc:
        compile_pipeline(approved([field('A', 1, 1, mandatory='UNKNOWN')]))
    assert 'REQUIRED_RULE' in {x['code'] for x in exc.value.issues}


def test_more_than_180_fields_and_20000_byte_positions_have_no_step_limit():
    fields = [field(f'F{i}', 1 + i * 120, 120) for i in range(181)]
    pipeline = compile_pipeline(approved(fields))
    row = b'A' * (181 * 120)
    result = validate_record(pipeline, row)
    assert result['valid']
    assert pipeline.layouts[0].width == 21720
    assert len(result['checks']) == 1 + 181 * 4


def test_mixed_layout_routing_and_duplicate_field_names_use_stable_ids():
    fields = [field('header', 1, 2), field('detail', 1, 2)]
    fields[0]['field_name'] = fields[1]['field_name'] = 'TYPE'
    layouts = [{'id': 'H', 'field_ids': ['header'], 'record_length': 2,
                'when': [{'start': 1, 'end': 1, 'equals': 'H'}]},
               {'id': 'D', 'field_ids': ['detail'], 'record_length': 2,
                'when': [{'start': 1, 'end': 1, 'equals': 'D'}]}]
    pipeline = compile_pipeline(approved(fields, layouts))
    assert validate_record(pipeline, b'H1')['layout_id'] == 'H'
    assert validate_record(pipeline, b'D2')['layout_id'] == 'D'
    assert not validate_record(pipeline, b'X3')['valid']


def master_routed_spec():
    fields = [field('KIND', 1, 2, mandatory='YES'),
              field('QUANTITY', 3, 4, kind='INTEGER EXTERNAL', mandatory='YES'),
              field('CHANNEL', 7, 1, mandatory='YES')]
    for item in fields:
        item.update(record_type='layout-one', alignment='EXACT', padding_direction='NONE',
                    conditional_rules="KIND = 'AB' AND CHANNEL = 'Q'")
    fields[1].update(padding_alignment='Leading Space Padded (Right Aligned)',
                     alignment='RIGHT', padding_direction='LEFT', padding_byte='SPACE (0x20)')
    return approved(fields)


def test_compiles_master_routing_conditions_into_executable_layout_checks():
    spec = master_routed_spec()
    assert spec['issues'] == []
    pipeline = compile_pipeline(spec)
    assert validate_record(pipeline, b'AB  12Q')['valid'] is True
    unknown = validate_record(pipeline, b'AX  12Q')
    assert unknown['valid'] is False
    assert unknown['issues'] == [{'code': 'UNKNOWN_LAYOUT'}]
    mismatch = validate_record(pipeline, b'AB  X2Q')
    assert any(item['code'] == 'DATATYPE' for item in mismatch['issues'])


@pytest.mark.parametrize('change', ['unsupported_or', 'tampered_when'])
def test_rejects_uncertified_master_routing_conditions(change):
    spec = master_routed_spec()
    if change == 'unsupported_or':
        for item in spec['fields']:
            item['conditional_rules'] = "KIND = 'AB' OR CHANNEL = 'Q'"
    else:
        spec['layouts'][0]['when'][0]['equals'] = 'ZZ'
    with pytest.raises(SpecError) as exc:
        compile_pipeline(spec)
    assert 'UNSUPPORTED_CONDITION' in {item['code'] for item in exc.value.issues}
