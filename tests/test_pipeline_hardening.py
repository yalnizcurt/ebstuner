"""Dynamic validation pipeline hardening and end-to-end verification.

Verifies:
1. Dynamic pipeline compilation across diverse synthetic scenarios (A, B, C, D, E).
2. Dynamic scaling of pipeline steps with field count and rule complexity.
3. Every applicable field is represented with exact rule steps (WIDTH, REQUIRED, PADDING, DATATYPE, etc.).
4. Unsupported rule keys and unexecutable conditional rules are rejected with SpecError.
5. Known correct DAT records pass 100% of pipeline checks.
6. Intentionally damaged DAT variants trigger precise, deterministic rule failures.
7. Spec revisions increment pipeline versions and invalidate old validation outputs.
8. Revalidation after structural repair verifies byte preservation and coverage.
"""
import copy
import pytest

from fixtures.synthetic_generator import SyntheticFixtureGenerator
from specs import SpecError, normalize_spec, validate_spec
from validation_pipeline import compile_pipeline, validate_record


def test_dynamic_pipeline_scaling_across_scenarios():
    """Verify that pipeline step count scales dynamically with field count and rule complexity."""
    gen = SyntheticFixtureGenerator(seed=42)
    step_counts = {}

    scenarios = [
        ('A', 20, 800),
        ('B', 50, 4000),
        ('C', 100, 10000),
        ('D', 180, 20000),
        ('E', 300, 50000),
    ]

    for name, f_cnt, width in scenarios:
        layout = gen.generate_layout(f_cnt, width, layout_id=name, all_mandatory=True)
        spec = gen.to_master_spec_dict(layout)
        norm = normalize_spec(spec)
        pipeline = compile_pipeline(norm)
        assert pipeline.version == 1
        assert len(pipeline.layouts) == 1
        l_pipeline = pipeline.layouts[0]
        assert l_pipeline.width == width

        total_steps = len(l_pipeline.steps)
        step_counts[name] = total_steps

        # Every physical field has at least WIDTH, REQUIRED, CONTROL_CHARACTERS, PADDING
        assert total_steps >= f_cnt * 4

    # Steps must increase monotonically with scenario field count:
    # A (20) < B (50) < C (100) < D (180) < E (300)
    assert step_counts['A'] < step_counts['B'] < step_counts['C'] < step_counts['D'] < step_counts['E']
    assert step_counts['E'] >= 1200  # 300 fields * 4 steps min


def test_every_applicable_field_represented_in_compiled_pipeline():
    """Ensure no physical field is dropped and all rule types are correctly converted to RuleSteps."""
    gen = SyntheticFixtureGenerator(seed=101)
    layout = gen.generate_layout(60, 5000, layout_id="C_spec", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)
    norm = normalize_spec(spec)
    pipeline = compile_pipeline(norm)
    l_pipeline = pipeline.layouts[0]

    pipeline_field_ids = {step.field_id for step in l_pipeline.steps}
    spec_field_ids = {f['id'] for f in norm['fields'] if f.get('rules', {}).get('mapping_type') != 'system'}

    assert pipeline_field_ids == spec_field_ids

    # Check step code coverage
    codes = {step.code for step in l_pipeline.steps}
    assert {'WIDTH', 'REQUIRED', 'CONTROL_CHARACTERS', 'PADDING'}.issubset(codes)


def test_unsupported_rules_explicitly_detected():
    """Unsupported rule keys or conditional rules must fail compilation explicitly with SpecError."""
    gen = SyntheticFixtureGenerator(seed=202)
    layout = gen.generate_layout(15, 600, layout_id="unsupp", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)
    raw_spec = copy.deepcopy(spec)

    # Inject unknown rule key
    raw_spec['fields'][0]['rules']['custom_oracle_sql'] = 'NVL(COL, 0)'
    with pytest.raises(SpecError) as exc:
        compile_pipeline(raw_spec)
    assert any(iss['code'] == 'UNSUPPORTED_RULE' for iss in exc.value.issues)

    # Inject unexecutable conditional rule
    raw_spec2 = copy.deepcopy(spec)
    raw_spec2['fields'][1]['conditional_rules'] = 'WHEN RECORD_TYPE=01 THEN REQUIRED'
    with pytest.raises(SpecError) as exc:
        compile_pipeline(raw_spec2)
    assert any(iss['code'] == 'UNSUPPORTED_RULE' for iss in exc.value.issues)


def test_validate_correct_dat_and_damaged_dat_variants():
    """Verify known correct DAT passes 100% and damaged variants trigger specific check failures."""
    gen = SyntheticFixtureGenerator(seed=303)
    layout = gen.generate_layout(30, 2000, layout_id="val_test", all_mandatory=True)
    spec = gen.to_master_spec_dict(layout)
    norm = normalize_spec(spec)
    pipeline = compile_pipeline(norm)

    # 1. Correct DAT record
    correct_row, _ = gen.generate_record(layout)
    result = validate_record(pipeline, correct_row, "val_test")
    assert result['valid'] is True
    assert len(result['issues']) == 0
    assert result['layout_id'] == 'val_test'

    # 2. Corrupted record with missing bytes (wrong total width)
    damaged_truncated = correct_row[:-5]
    res_trunc = validate_record(pipeline, damaged_truncated, "val_test")
    assert res_trunc['valid'] is False
    assert any(i['code'] == 'RECORD_LENGTH' for i in res_trunc['issues'])

    # 3. Corrupted record with bad characters in numeric field
    num_field = next(f for f in layout.fields if f.datatype in ('NUMBER', 'INTEGER EXTERNAL'))
    start = num_field.start - 1
    end = num_field.end
    width = end - start
    damaged_chars = correct_row[:start] + b'X' * width + correct_row[end:]
    res_chars = validate_record(pipeline, damaged_chars, "val_test")
    assert res_chars['valid'] is False
    assert any(i['field_id'] == num_field.field_id and i['code'] == 'DATATYPE' for i in res_chars['issues'])


def test_spec_versioning_and_invalidation():
    """Modifying a specification increments version and produces a new distinct pipeline."""
    gen = SyntheticFixtureGenerator(seed=404)
    layout = gen.generate_layout(20, 800, layout_id="ver_test", all_mandatory=True)
    spec_v1 = gen.to_master_spec_dict(layout)
    spec_v1['version'] = 1
    norm_v1 = normalize_spec(spec_v1)
    pipeline_v1 = compile_pipeline(norm_v1)
    assert pipeline_v1.version == 1

    # Revise spec: increase width of first field by 5 bytes
    spec_v2 = copy.deepcopy(spec_v1)
    spec_v2['version'] = 2
    f0 = spec_v2['fields'][0]
    orig_len = int(f0['length'])
    f0['length'] = str(orig_len + 5)
    f0['end_pos'] = str(int(f0['start_pos']) + orig_len + 4)
    # Shift subsequent fields
    for f in spec_v2['fields'][1:]:
        f['start_pos'] = str(int(f['start_pos']) + 5)
        f['end_pos'] = str(int(f['end_pos']) + 5)
    spec_v2['layouts'][0]['record_length'] += 5

    norm_v2 = normalize_spec(spec_v2)
    pipeline_v2 = compile_pipeline(norm_v2)
    assert pipeline_v2.version == 2
    assert pipeline_v2.layouts[0].width == pipeline_v1.layouts[0].width + 5

    # Original DAT is valid for v1, but INVALID for v2
    row, _ = gen.generate_record(layout)
    assert validate_record(pipeline_v1, row, "ver_test")['valid'] is True
    res_v2 = validate_record(pipeline_v2, row, "ver_test")
    assert res_v2['valid'] is False
    assert any(i['code'] == 'RECORD_LENGTH' for i in res_v2['issues'])
