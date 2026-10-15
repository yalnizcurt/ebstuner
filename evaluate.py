"""Reproducible synthetic evaluation. No credentials or customer data needed."""
from __future__ import annotations
import argparse
import json
import statistics
import time
from datetime import datetime,timezone
from pathlib import Path
from engine import inspect,round_trip,edit_record,parse_lines
from reconstruction import reconstruct
from spreadsheets import import_spreadsheet,default_mapping

ROOT=Path(__file__).parent
SIMPLE="LOAD DATA INTO TABLE T WHEN RT='1' (RT POSITION(1:1) CHAR, ID POSITION(2:5) CHAR, AMOUNT POSITION(6:10) INTEGER EXTERNAL, DAY POSITION(11:18) DATE 'YYYYMMDD')"
GOOD=b'1AB   001220240229'


def datasets():
    lockbox=(ROOT/'demo_lockbox.ctl').read_text();valid=(ROOT/'valid.dat').read_bytes();damaged=(ROOT/'truncated.dat').read_bytes()
    large='LOAD DATA INTO TABLE T (X POSITION(1:16000) CHAR)'
    cases=[
        ('correct',SIMPLE,GOOD,True,None),
        ('missing_trailing_spaces','LOAD DATA INTO TABLE T (ID POSITION(1:4) CHAR, REF POSITION(5:12) CHAR)',b'0001ABC',False,b'0001ABC     '),
        ('missing_internal_space',SIMPLE,GOOD[:4]+GOOD[5:],False,GOOD),
        ('shifted_fields',SIMPLE,GOOD[:5]+GOOD[6:],False,GOOD),
        ('truncated_record',SIMPLE,GOOD[:-5],False,GOOD),
        ('wrong_field_width',SIMPLE,GOOD[:10]+b'2024022',False,GOOD),
        ('malformed_date',SIMPLE,GOOD[:10]+b'20230229',False,None),
        ('malformed_numeric',SIMPLE,GOOD[:5]+b'abcde'+GOOD[10:],False,None),
        ('incorrect_identifier',SIMPLE,b'8'+GOOD[1:],False,None),
        ('multi_matching_when',SIMPLE+" INTO TABLE U WHEN RT='1' (RT POSITION(1:1) CHAR, ID POSITION(2:5) CHAR)",GOOD,True,None),
        ('unmapped_bytes','LOAD DATA INTO TABLE T (ID POSITION(3:6) CHAR)',b'??0001KEEP',True,None),
        ('large_fixed_width',large,b'A'+b' '*15999,True,None),
        ('CRLF',SIMPLE,GOOD+b'\r\n'+GOOD+b'\r\n',True,None),
        ('LF',SIMPLE,GOOD+b'\n'+GOOD+b'\n',True,None),
        ('leading_zero_identifiers','LOAD DATA INTO TABLE T (ID POSITION(1:8) CHAR)',b'00001234',True,None),
        ('unsupported_ctl',SIMPLE.replace('INTEGER EXTERNAL','INTEGER'),GOOD,False,None),
        ('lockbox_valid',lockbox,valid,True,None),
        ('lockbox_truncated',lockbox,damaged,False,valid),
    ]
    return cases


def run(output_dir=None):
    results=[];latencies=[];preserved=0;exact_first=exact_any=recoverable=total_candidates=0
    cases=datasets()
    for name,ctl,data,expected,truth in cases:
        started=time.perf_counter();report=inspect(ctl,data)
        candidates=reconstruct(ctl,data)
        duration=(time.perf_counter()-started)*1000;latencies.append(duration)
        valid=report['export_status']=='structurally_validated'
        unchanged=round_trip(ctl,data)[0]==data;preserved+=unchanged
        reconstructed=[]
        for c in candidates:
            lines=parse_lines(data);lines[c['record']-1]=(bytes.fromhex(c['candidate_hex']),lines[c['record']-1][1])
            reconstructed.append(b''.join(a+e for a,e in lines))
        if truth is not None:
            recoverable+=1;exact_first+=bool(reconstructed and reconstructed[0]==truth);exact_any+=truth in reconstructed
        total_candidates+=len(candidates)
        results.append({'case':name,'expected_structurally_valid':expected,'observed_structurally_valid':valid,
                        'correct_classification':valid==expected,'byte_identical_round_trip':unchanged,
                        'candidates':len(candidates),'ground_truth_recovered_in_returned_candidates':truth in reconstructed if truth else None,
                        'latency_ms':round(duration,3)})
        if output_dir:
            output_dir.mkdir(parents=True,exist_ok=True);(output_dir/f'{name}.ctl').write_text(ctl);(output_dir/f'{name}.dat').write_bytes(data)
            if truth is not None:(output_dir/f'{name}.expected.dat').write_bytes(truth)
    sh=import_spreadsheet('reference.csv',b'ID,AMOUNT\n0001,10.2300\n0002,\n0002,12.20\n')
    mapping=default_mapping('LOAD DATA INTO TABLE T (ID POSITION(1:4) CHAR, AMOUNT POSITION(5:12) DECIMAL EXTERNAL)',sh['sheets'][0])
    expected_mapping={'ID':'ID','AMOUNT':'AMOUNT'}
    return {'run_at':datetime.now(timezone.utc).isoformat(),'dataset':'synthetic-v1','cases':results,
            'metrics':{'structural_validation_accuracy':sum(x['correct_classification'] for x in results)/len(results),
                       'exact_top_candidate_reconstruction_rate':exact_first/recoverable,'exact_returned_candidate_reconstruction_rate':exact_any/recoverable,
                       'reconstruction_cases':recoverable,'returned_candidates':total_candidates,'dat_preservation_rate':preserved/len(cases),
                       'automatic_repairs_applied':0,'false_auto_repair_rate':None,
                       'human_approval_requirement_rate':1. if total_candidates else None,
                       'field_mapping_accuracy_exact_headers':sum(mapping.get(k)==v for k,v in expected_mapping.items())/len(expected_mapping),
                       'ai_proposal_acceptance_rate':None,'live_bedrock_calls':0,'bedrock_tokens_observed':0,'cost_per_live_analysis_usd':None,
                       'processing_latency_mean_ms':round(statistics.mean(latencies),3),'processing_latency_max_ms':round(max(latencies),3)},
            'notes':['Rates describe this small synthetic corpus, not production accuracy.','False auto-repair rate is undefined: this policy performs no automatic repairs.',
                     'Human approval requirement measures proposals, not actual human decisions.','Mapping metric covers deterministic exact headers only.',
                     'No live Bedrock invocation; live acceptance, tokens per analysis and cost are unmeasured.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'docs'/'evaluation-results.json');parser.add_argument('--fixtures',type=Path)
    args=parser.parse_args();report=run(args.fixtures);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report['metrics'],indent=2))

if __name__=='__main__':main()
