import pytest
from reconstruction import reconstruct,score_candidate,edit_distance,DEFAULT_WEIGHTS,hard_constraints
from compiler import compile_ctl

CTL="LOAD DATA INTO TABLE T WHEN RT='1' (RT POSITION(1:1) CHAR, ID POSITION(2:5) CHAR, AMOUNT POSITION(6:10) INTEGER EXTERNAL, DAY POSITION(11:18) DATE 'YYYYMMDD')"
GOOD=b'1AB   001220240229'


def test_internal_missing_space_found_and_ranked():
    damaged=GOOD[:4]+GOOD[5:]
    candidates=reconstruct(CTL,damaged)
    assert any(bytes.fromhex(c['candidate_hex'])==GOOD for c in candidates)
    assert all(c['requires_approval'] and not c['auto_repair_eligible'] for c in candidates)
    assert all(c['score_breakdown'] and c['edit_distance']==1 for c in candidates)
    assert all(c['source']=='DETERMINISTIC' for c in candidates)


def test_tied_candidate_scores_are_ambiguous():
    source='LOAD DATA INTO TABLE T (A POSITION(1:3) CHAR, B POSITION(4:6) CHAR)'
    cs=reconstruct(source,b'ABCDE')
    assert len(cs)>1 and cs[0]['ambiguous'] and cs[0]['score_separation'] is not None


def test_hard_constraints_reject_value_invention_and_routing():
    schema=compile_ctl(CTL)
    assert not hard_constraints(schema,GOOD,GOOD.replace(b'00122',b'99999'),'ascii',[0])
    assert not hard_constraints(schema,GOOD,b'2'+GOOD[1:],'ascii',[0])


def test_configurable_weights_and_independent_features():
    for key in DEFAULT_WEIGHTS:
        f={k:0. for k in DEFAULT_WEIGHTS};f[key]=1.
        score,breakdown=score_candidate(f)
        assert score==round(DEFAULT_WEIGHTS[key]*100,4)
        assert breakdown[key]['feature']==1
    all_one={k:1. for k in DEFAULT_WEIGHTS}
    assert score_candidate(all_one)[0]==100
    with pytest.raises(ValueError):score_candidate(all_one,{'preservation':1})
    with pytest.raises(ValueError):score_candidate(all_one,{k:.5 for k in DEFAULT_WEIGHTS})


def test_edit_distance_is_real_and_bounded():
    assert edit_distance(b'kitten',b'sitting')==3
    assert edit_distance(b'abc',b'a bc')==1
    assert edit_distance(b'a',b'abcdefghij',3)==4


def test_unsupported_and_large_damage_never_guessed():
    assert not reconstruct(CTL,GOOD[:2],max_missing=8)
    assert not reconstruct(CTL.replace('INTEGER EXTERNAL','INTEGER'),GOOD[:-1])
