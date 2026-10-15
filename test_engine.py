from pathlib import Path
from engine import parse_ctl,inspect,edit_record,parse_lines,pad_record,suggestions
D=Path(__file__).parent
CTL=(D/'demo_lockbox.ctl').read_text()
VALID=(D/'valid.dat').read_bytes()
BAD=(D/'truncated.dat').read_bytes()

def test_parser():
    b=parse_ctl(CTL)
    assert len(b)==7
    assert b[2].minimum_length==105
    assert b[4].minimum_length==175

def test_unmodified_roundtrip():
    assert b''.join(a+end for a,end in parse_lines(VALID))==VALID
    assert all(not i['issues'] for i in inspect(CTL,VALID)['records'])

def test_truncated_detection():
    d=inspect(CTL,BAD)
    assert d['records'][2]['length']==91
    assert d['records'][2]['status']=='needs_review'
    assert any(s['record']==3 and s['requires_approval'] for s in suggestions(CTL,BAD))

def test_approved_padding_only():
    repaired=pad_record(CTL,BAD,3)
    assert repaired==VALID
    assert not inspect(CTL,repaired)['records'][2]['issues']

def test_field_edit_preserves_all_other_bytes():
    out=edit_record(CTL,VALID,3,'CUSTOMER_NUMBER','0002060')
    old=parse_lines(VALID);new=parse_lines(out)
    assert len(old)==len(new)
    assert old[2][0][:45]==new[2][0][:45]
    assert old[2][0][75:]==new[2][0][75:]
    assert new[2][0][45:75].startswith(b'0002060')
    assert old[:2]==new[:2] and old[3:]==new[3:]
    assert not inspect(CTL,out)['records'][2]['issues']

def test_width_overflow():
    import pytest
    with pytest.raises(ValueError):edit_record(CTL,VALID,3,'ITEM_NUMBER','LONGCODE')

def test_crlf():
    out=edit_record(CTL,VALID,3,'BATCH_NAME','104')
    assert out.count(b'\r\n')==VALID.count(b'\r\n')

def test_route_nondefault_process():
    d=inspect(CTL,VALID)
    assert any(x['table']=='XXHRC_AR_POC_STG' for x in d['records'][3]['branches'])
    assert not any(x['table']=='AR_PAYMENTS_INTERFACE' for x in d['records'][3]['branches'])
