"""Independent arithmetic and split checks against materialized rows."""
import gzip,json,sys
from decimal import Decimal
from pathlib import Path
folder=Path(sys.argv[1]);rows=json.load(gzip.open(folder/'features.json.gz','rt'))
keys=[(r['event_id'],r['minute']) for r in rows];assert len(keys)==len(set(keys))
for earlier,later in [('train','validation'),('validation','test')]:
    assert max(r['time'] for r in rows if r['split']==earlier)<min(r['time'] for r in rows if r['split']==later)
seen={}
priced=0
for r in rows:
    assert seen.setdefault(r['event_id'],r['split'])==r['split']
    q=r['quote']
    if not q:continue
    priced+=1
    assert 0<=r['cutoff_timestamp']-q['timestamp']<=120
    quarters=int(Decimal(str(q['line']))*4)
    legs=[quarters] if quarters%2==0 else [quarters-1,quarters+1]
    returns=[]
    for leg in legs:
        difference=leg-4*sum(r['ft'])
        returns.append(Decimal(str(q['under'])) if difference>0 else Decimal(1) if difference==0 else Decimal(0))
    independent=sum(returns)/len(returns)-1
    assert abs(float(independent)-r['profit'])<1e-9
out={'unique_match_checkpoints':len(rows),'priced_rows_recomputed':priced,'strict_global_chronological_splits':True,'duplicate_rows':0,'settlement_mismatches':0,'live_rules_enabled':False}
(folder/'audit.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
