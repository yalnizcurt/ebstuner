from evaluate import run
from engine import physical_lines,edit_record

def test_synthetic_evaluation_is_computed_and_has_no_false_validation():
    r=run();assert r['metrics']['structural_validation_accuracy']==1
    assert r['metrics']['dat_preservation_rate']==1
    assert r['metrics']['live_bedrock_calls']==0 and r['metrics']['false_auto_repair_rate'] is None
    assert len(r['cases'])>=18

def test_editor_never_mutates_cached_source_lines():
    ctl='LOAD DATA INTO TABLE T (X POSITION(1:4) CHAR)';raw=b'0001\r\n'
    first=physical_lines(ctl,raw)
    assert edit_record(ctl,raw,1,'X','9999')==b'9999\r\n'
    assert physical_lines(ctl,raw)==first==[(b'0001',b'\r\n')]
