"""Scenario 3 approval coverage and master-template round trip."""
import csv
import io
from pathlib import Path

import specs
from validation_pipeline import compile_pipeline


SCENARIO = Path(__file__).parents[1] / 'fixtures/specifications/Scenario_3_Over_Payment_OVP.csv'
OVP = Path(__file__).parents[1] / 'outputs/lockbox-examples/DAT_Files/SYN_OVP_OVERPAYMENT.dat'


def test_scenario_3_all_unresolved_mandatory_metadata_stays_unknown():
    source = specs.extract_spec(SCENARIO.name, SCENARIO.read_bytes())
    draft = specs.normalize_spec(source)
    questions = [finding for finding in draft['issues'] if finding['code'] == 'REQUIRED_RULE']
    assert len(source['fields']) == 63
    assert len(questions) == 55
    assert all(finding['severity'] == 'error' for finding in questions)
    assert {finding['field_id'] for finding in questions} == {
        field['id'] for field in draft['fields'] if field['mandatory'] == 'UNKNOWN'
        and field['rules']['mapping_type'] in ('physical', 'filler')
    }
    assert not [finding for finding in draft['issues'] if finding['code'] not in ('REQUIRED_RULE',)]
    draft['options']['acknowledge_unknown_mandatory'] = True
    reviewed = specs.normalize_spec(draft)
    assert len([finding for finding in reviewed['issues'] if finding['code'] == 'REQUIRED_RULE']) == 55
    assert all(finding['severity'] == 'warning' for finding in reviewed['issues'])
    assert len(reviewed['coverage_limitations']) == 55
    assert len(compile_pipeline(reviewed).layouts) == 1


def test_master_template_alignment_and_explicit_mandatory_survive_import():
    row = {column['label']: '' for column in specs.MASTER_COLUMNS}
    row.update({'Field ID': 'stable-customer', 'Field Name': 'CUSTOMER', 'Mapping Type': 'POSITION',
                'Start Pos': '1', 'End Pos': '4', 'Length': '4', 'Data Type / Oracle Conversion Function': 'CHAR',
                'Alignment': 'LEFT', 'Mandatory': 'NO', 'Sequence': '1'})
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=list(row))
    writer.writeheader(); writer.writerow(row)
    draft = specs.normalize_spec(specs.extract_spec('master.csv', output.getvalue().encode()))
    field = draft['fields'][0]
    assert field['id'] == 'stable-customer'
    assert field['alignment'] == 'LEFT'
    assert field['mandatory'] == 'NO'
    assert field['rules']['required'] is False
    assert field['rules']['padding']['side'] == 'right'
    assert field['source_reference'] == 'CSV!2'


def test_scenario_3_ovp_dat_cannot_be_declared_fully_valid_with_unknown_rules():
    draft = specs.normalize_spec(specs.extract_spec(SCENARIO.name, SCENARIO.read_bytes()))
    draft['options'].update(acknowledge_unknown_mandatory=True, unknown_records='preserve')
    reviewed = specs.normalize_spec(draft)
    original = OVP.read_bytes()
    report = specs.inspect_dat(reviewed, original)
    assert report['summary']['record_count'] == 7
    assert not report['valid']
    assert report['original_checksum'] != report['candidate_checksum']
    assert any(finding['code'] == 'MANDATORY_UNVERIFIED' for finding in report['record_summaries'][0]['issues'])
