import json,math,sys,copy
from pathlib import Path
R=Path(__file__).resolve().parents[2];sys.path.insert(0,str(R/'scripts'))
from live_ranking import rank_final_candidates
D=json.loads((R/'research/live-results-20261018/comparison.json').read_text())
cache={}
def snapshot(sid):
 if sid not in cache:cache[sid]=json.loads((R/f'snapshots/live/{sid}.json').read_text())
 return cache[sid]
def settle(total,line,price):
 q=round(line*4)
 lines=[line] if q%2==0 else [line-.25,line+.25]
 return sum(price-1 if total>x else 0 if total==x else -1 for x in lines)/len(lines)
def enrich(rows,replay=False):
 out=[]
 for original in rows:
  r=copy.deepcopy(original);s=snapshot(r['snapshot_id']);events=copy.deepcopy(s['events'])
  if replay: rank_final_candidates(events)
  e=next(e for e in events if str(e['event_id'])==r['id'])
  r['rank']=e['final_sports_selection']['sports_rank'];r['version']=s['rules_version'];ref=r.get('reference')or{};line=ref.get('line');price=ref.get('over');start=sum(map(int,r['initial'].split('-')))
  r['need']=None;r['rounded_need']=None
  if line is not None:
   # Full payout requires beating the upper half of a split quarter line.
   upper=line+.25 if round(line*4)%2 else line
   r['need']=math.floor(upper)+1-start
   userline=line-.25 if round(line*4)%2 else line
   r['rounded_need']=math.floor(userline)+1-start
  if r['status']=='closed' and line is not None and price is not None:
   calculated=settle(sum(r['final']),line,price)
   assert abs(calculated-r['profit'])<1e-8,(r,calculated)
  out.append(r)
 return out
def stats(rows):
 closed=[r for r in rows if r['status']=='closed'];p=[r for r in closed if r['profit'] is not None];n=len(closed)
 return dict(selected=len(rows),closed=n,one=sum(r['additional']>=1 for r in closed),two=sum(r['additional']>=2 for r in closed),priced=len(p),profit=round(sum(r['profit']for r in p),6),roi=round(100*sum(r['profit']for r in p)/len(p),4)if p else None,fullwins=sum(r['additional']>=r['need']for r in closed if r['need'] is not None),positive=sum(r['profit']>0 for r in p))
result={}
for name,replay in [('published',False),('reranked_v8',True)]:
 rows=enrich(D['details'][name],replay)
 assert len(rows)==len({r['id']for r in rows})
 groups={'all':rows}
 for rank in range(1,6):groups[f'rank_{rank}']=[r for r in rows if r['rank']==rank]
 for cutoff in [1,2,3]:groups[f'top_{cutoff}']=[r for r in rows if r['rank']<=cutoff]
 for need in [1,2,3,4,5]:groups[f'need_{need}']=[r for r in rows if r['need']==need]
 for need in [1,2,3,4,5]:groups[f'rounded_need_{need}']=[r for r in rows if r['rounded_need']==need]
 for rank in [1,2,3]:
  for need in [1,2,3,4]:groups[f'rank_{rank}_need_{need}']=[r for r in rows if r['rank']==rank and r['need']==need]
 result[name]={'groups':{k:stats(v)for k,v in groups.items()},'rows':rows}
 print(name,json.dumps(result[name]['groups'],ensure_ascii=False))
json.dump(result,open(sys.argv[1] if len(sys.argv)>1 else '/tmp/scaner-rank-goals-check.json','w'),ensure_ascii=False,indent=2)
