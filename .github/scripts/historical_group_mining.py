"""Descriptive full-history group search, explicitly fitted on all observed dates."""
import argparse, collections, datetime as dt, gzip, hashlib, itertools, json, math, os, sys
from pathlib import Path
import numpy as np
from fresh_base_mining import NUMERIC,CATEGORICAL,finite,first_indices,arrays,git
from selection_validation import summary

FIXED={
 'minute':[10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90],
 'hour':[6,8,10,12,14,16,18,20,22],
 'goals':[0,1,2,3,4,5], 'margin':[0,1,2,3],
 'line':[1.5,2,2.5,3,3.5,4,4.5,5,5.5,6],
 'price':[1.4,1.5,1.6,1.7,1.8,1.9,2,2.2,2.5,3,4],
 'gap':[.5,1,1.5,2,2.5,3,3.5,4], 'need':[1,2,3,4,5],
 'sot_rate':[.05,.1,.15,.2,.25,.3], 'shots_rate':[.2,.3,.4,.5,.6],
 'danger_rate':[.5,.75,1,1.25,1.5], 'corners_rate':[.05,.1,.15,.2],
 'redcards':[0,1,2], 'gp_ratio':[.8,.9,1,1.1,1.2],
 'league_goals':[2,2.5,3,3.5,4], 'home_venue_goals':[2,2.5,3,3.5,4],
 'away_venue_goals':[2,2.5,3,3.5,4], 'overround':[.03,.04,.05,.06,.08,.1]}
for w in (5,10,15):
 for k in ('sot','shots','danger','xg'):
  FIXED[f'fresh_{k}_{w}']=([0,1,2,3,4] if k=='sot' else [0,1,2,3,4,6,8] if k=='shots' else [3,5,8,10,15,20] if k=='danger' else [.05,.1,.2,.3,.5,.75,1])


def prepare(data):
 rows=[];q=collections.Counter()
 for r in data['points']:
  f=r['features'];legacy=r.get('legacy_recorded',False)
  if r.get('known_finished_before_point'):q['known_finished_excluded']+=1;continue
  if not legacy and (r.get('quote_lag') is None or abs(r['quote_lag'])>120):q['unknown_or_misaligned_quote']+=1;continue
  if f['minute']<=0 or f['minute']>130:q['invalid_game_clock']+=1;continue
  if f['line']<=f['goals']:q['line_already_reached_excluded']+=1;continue
  rows.append(r)
 q=dict(q);q.update(eligible_points=len(rows),eligible_matches=len({r['event_id'] for r in rows}),
          legacy_points=sum(bool(r.get('legacy_recorded')) for r in rows),
          earliest_day=min(r['day'] for r in rows),latest_day=max(r['day'] for r in rows))
 return rows,dict(q)


def compatible(rule):
 fields=collections.defaultdict(list)
 for a in rule:fields[a['field']].append(a)
 for atoms in fields.values():
  if len(atoms)==1:continue
  if len(atoms)!=2 or set(a['op'] for a in atoms)!={'<=','>'}:return False
  lower=next(a['value'] for a in atoms if a['op']=='>');upper=next(a['value'] for a in atoms if a['op']=='<=')
  if lower>=upper:return False
 return True


class Engine:
 def __init__(self,rows):
  self.rows=rows;self.events=arrays(rows)
  self.profit=np.array([r['profit'] if r['profit'] is not None else np.nan for r in rows])
  self.day_names=sorted({r['day'] for r in rows});self.league_names=sorted({r['league'] for r in rows})
  dm={d:i for i,d in enumerate(self.day_names)};lm={d:i for i,d in enumerate(self.league_names)}
  self.days=np.array([dm[r['day']] for r in rows]);self.leagues=np.array([lm[r['league']] for r in rows])
  self.atoms=[];self.coverage={};seen=set();first=first_indices(np.ones(len(rows),bool),self.events)
  for field in NUMERIC:
   v=np.array([np.nan if finite(r['features'].get(field)) is None else float(r['features'][field]) for r in rows])
   train=v[first];train=train[np.isfinite(train)]
   self.coverage[field]=dict(known_first_matches=len(train),total_matches=len(first))
   if len(train)<80:continue
   cuts=sorted(set(FIXED.get(field,[]))|set(float(c) for c in np.quantile(train,np.arange(.1,1,.1))))
   for cut in cuts:
    for op in ('<=','>'):
     mask=np.isfinite(v)&(v<=cut if op=='<=' else v>cut)
     if len(first_indices(mask,self.events))<80:continue
     key=hashlib.sha256(mask.tobytes()).digest()
     if key in seen:continue
     seen.add(key);self.atoms.append((dict(field=field,op=op,value=cut),mask))
  for field in CATEGORICAL:
   v=[r['features'].get(field) for r in rows]
   for value in sorted({v[i] for i in first if v[i] is not None}):
    mask=np.array([x==value for x in v])
    if len(first_indices(mask,self.events))>=80:self.atoms.append((dict(field=field,op='=',value=value),mask))

 def indices(self,ids):
  mask=self.atoms[ids[0]][1].copy()
  for i in ids[1:]:mask&=self.atoms[i][1]
  return first_indices(mask,self.events)

 def quick(self,idx):
  selected=len(idx);closed=idx[np.isfinite(self.profit[idx])];n=len(closed)
  if n<80:return None
  v=self.profit[closed];p=float(v.sum());roi=p/n
  if p<=0:return None
  dc=np.bincount(self.days[closed],minlength=len(self.day_names));dp=np.bincount(self.days[closed],weights=v,minlength=len(self.day_names))
  lc=np.bincount(self.leagues[closed],minlength=len(self.league_names));lp=np.bincount(self.leagues[closed],weights=v,minlength=len(self.league_names))
  days=int((dc>0).sum());top3=float(np.partition(v,-3)[-3:].sum());w3=(p-top3)/(n-3)
  if days<5 or w3<=0:return None
  bd=int(np.argmax(dp));bl=int(np.argmax(lp))
  wd=(p-dp[bd])/(n-dc[bd]) if n>dc[bd] else None
  wl=(p-lp[bl])/(n-lc[bl]) if n>lc[bl] else None
  daily_roi=np.divide(dp,dc,out=np.zeros_like(dp),where=dc>0)
  dispersion=float(np.sqrt(np.sum(dc*(daily_roi-roi)**2)/n))
  positive_days=int((dp>1e-10).sum());best_share=float(dp[bd]/p)
  tier=0 if n>=150 and days>=7 and positive_days/days>=.6 and best_share<=.45 and wd is not None and wd>0 and wl is not None and wl>0 else 1 if n>=100 and days>=5 and positive_days/days>=.5 and best_share<=.6 and wd is not None and wd>0 and wl is not None and wl>0 else 2
  return dict(selected=selected,n=n,unresolved=selected-n,profit=p,roi=roi,without_top3=w3,
   stability_tier=tier,weighted_daily_roi_sd=dispersion,best_day_share_net_profit=best_share,positive_day_fraction=positive_days/days,
   without_best_day=wd,without_best_league=wl,days=days,positive_days=int((dp>1e-10).sum()),
   score=(w3*n/(n+150))/(1+4*dispersion),worst_unknown_profit=p-(selected-n))


def search(rows):
 e=Engine(rows);records=[];seen=set();expressions=0;seen_valid=0
 def visit(ids):
  nonlocal expressions,seen_valid
  rule=[e.atoms[i][0] for i in ids]
  if not compatible(rule):return
  expressions+=1;idx=e.indices(ids)
  if len(idx)<80:return
  key=hashlib.sha256(idx.tobytes()).digest()
  if key in seen:return
  seen.add(key);seen_valid+=1;stats=e.quick(idx)
  if stats is not None:records.append(dict(atom_ids=ids,rule=rule,subset_hash=key.hex(),**stats))
 for depth in (1,2):
  candidates=((i,) for i in range(len(e.atoms))) if depth==1 else itertools.combinations(range(len(e.atoms)),2)
  for ids in candidates:visit(ids)
  print('HISTORICAL_SEARCH_PROGRESS='+json.dumps(dict(depth=depth,atoms=len(e.atoms),expressions=expressions,distinct=seen_valid,profitable=len(records))),flush=True)
 # Extend 160 diverse positive pair seeds with every compatible extra atom.
 seeds=[];signatures=collections.Counter()
 for r in sorted([r for r in records if len(r['atom_ids'])==2],key=lambda x:(x['stability_tier'],x['weighted_daily_roi_sd'],-x['score'],-x['n'])):
  sig=tuple(sorted(a['field'] for a in r['rule']))
  if signatures[sig]>=2:continue
  seeds.append(r);signatures[sig]+=1
  if len(seeds)==160:break
 triples=set()
 for seed in seeds:
  for i in range(len(e.atoms)):
   if i in seed['atom_ids']:continue
   ids=tuple(sorted((*seed['atom_ids'],i)))
   if ids in triples:continue
   triples.add(ids);visit(ids)
 print('HISTORICAL_SEARCH_PROGRESS='+json.dumps(dict(depth=3,expressions=expressions,distinct=seen_valid,profitable=len(records))),flush=True)
 # Prioritize concentration-resistant aggregate profits; keep other profitable sets in the complete ledger.
 ranked=sorted(records,key=lambda r:(r['stability_tier'],r['weighted_daily_roi_sd'],-r['score'],-r['n'],len(r['rule'])))
 chosen=[];sets=[];signatures=collections.Counter();rejections=collections.Counter()
 for r in ranked:
  sig=tuple(sorted(set(a['field'] for a in r['rule'])))
  if signatures[sig]>=2:rejections['same_field_signature']+=1;continue
  ids=e.indices(r['atom_ids']);ident=set(e.events[ids].tolist())
  if any(len(ident&s)/len(ident|s)>.75 for s in sets):rejections['jaccard_above_075']+=1;continue
  chosen.append(r);sets.append(ident);signatures[sig]+=1
  if len(chosen)==30:break
 output=[];entries=[]
 strict_rows=[r for r in rows if not r.get('legacy_recorded')]
 for index,item in enumerate(chosen):
  idx=e.indices(item['atom_ids']);selected=[rows[int(i)] for i in idx];stats=summary(selected)
  assert stats['n']==item['n'] and abs(stats['unit_profit']-item['profit'])<1e-8
  assert abs(sum(v['unit_profit'] for v in stats['daily'].values())-stats['unit_profit'])<1e-8
  mask=e.atoms[item['atom_ids'][0]][1].copy()
  for atom_id in item['atom_ids'][1:]:mask&=e.atoms[atom_id][1]
  all_idx=np.flatnonzero(mask);all_closed=all_idx[np.isfinite(e.profit[all_idx])]
  weights=np.bincount(e.events[all_closed],minlength=int(e.events.max())+1)
  each_event=np.bincount(e.events[all_closed],weights=e.profit[all_closed],minlength=len(weights))
  gid='H'+str(index+1).zfill(2)
  from fresh_base_mining import rule_mask
  strict_idx=first_indices(rule_mask(strict_rows,item['rule']),arrays(strict_rows))
  strict_summary=summary([strict_rows[int(i)] for i in strict_idx])
  by_day={d:stats['daily'].get(d,dict(n=0,unit_profit=0,roi=None)) for d in e.day_names}
  closed=[r for r in selected if r['profit'] is not None];running=peak=drawdown=0
  for r in closed:
   running+=r['profit'];peak=max(peak,running);drawdown=max(drawdown,peak-running)
  entry_prices=[r['market']['price'] for r in closed]
  legacy=[r for r in selected if r.get('legacy_recorded')]
  record=dict(id=gid,rule=item['rule'],rule_sha256=hashlib.sha256(json.dumps(item['rule'],sort_keys=True).encode()).hexdigest(),
   rank_score=item['score'],stability={k:item[k] for k in ('stability_tier','weighted_daily_roi_sd','best_day_share_net_profit','positive_day_fraction')},stats=stats,daily=by_day,strict_timestamp_pool=strict_summary,
   all_points_diagnostic=dict(points=len(all_idx),closed_points=len(all_closed),roi=float(e.profit[all_closed].mean()) if len(all_closed) else None,
    equal_match_weighted_point_mean_roi=float(np.mean(each_event[weights>0]/weights[weights>0])) if (weights>0).any() else None,
    warning='Repeated observations are dependent and are not independent bets; the principal ROI uses the first qualifying entry per match.'),
   positive_days=sum(v['unit_profit']>1e-10 for v in stats['daily'].values()),negative_days=sum(v['unit_profit']<-1e-10 for v in stats['daily'].values()),
   mean_price=float(np.mean(entry_prices)),median_price=float(np.median(entry_prices)),max_drawdown_units=drawdown,
   legacy_first_entries=len(legacy),first_entry_time=min(r['t'] for r in selected),last_entry_time=max(r['t'] for r in selected))
  output.append(record)
  for r in selected:entries.append(dict(group_id=gid,event_id=r['event_id'],day=r['day'],t=r['t'],source=r['source'],minute=r['features']['minute'],score=r['score'],market=r['market'],profit=r['profit'],legacy_recorded=r.get('legacy_recorded',False),features={a['field']:r['features'].get(a['field']) for a in item['rule']}))
 overlaps=[]
 for i,a in enumerate(chosen):
  for j in range(i+1,len(chosen)):
   b=chosen[j];inter=len(sets[i]&sets[j]);overlaps.append(dict(a=output[i]['id'],b=output[j]['id'],shared_matches=inter,jaccard=inter/len(sets[i]|sets[j])))
 selected_points={}
 for r in entries:
  key=(r['event_id'],r['t'],r['market']['line'],r['market']['price']);selected_points[key]=r
 union={}
 for r in entries:
  if r['event_id'] not in union or r['t']<union[r['event_id']]['t']:union[r['event_id']]=r
 baseline_idx=first_indices(np.ones(len(rows),bool),e.events)
 result=dict(version='all-history-descriptive-20261031-v1',generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
  method='Fit all rules using all observed dates; no train/validation/test split and no prospective performance claim.',
  search=dict(atoms=len(e.atoms),rule_expressions=expressions,distinct_supported_entry_sets=seen_valid,profitable_candidates=len(records),triple_seed_count=len(seeds),
   minimum_closed_matches=80,minimum_active_days=5,minimum_without_top3=0,ranking='Stability tier first; then increasing match-count-weighted SD of daily ROI; then concentration-resistant ROI score. Tier 0: N>=150, days>=7, >=60% positive days, best day <=45% of net profit, positive without best day/league. Tier 1: N>=100, days>=5, >=50% positive days, best day <=60%, positive without best day/league. Tier 2: descriptive reserves only.',selected_count=len(output),selection_jaccard_ceiling=.75,coverage=e.coverage,rejections=dict(rejections)),
  days=e.day_names,groups=output,overlaps=overlaps,
  aggregate=dict(distinct_group_matches=len(set(r['event_id'] for r in entries)),sum_group_closed_counts=sum(r['stats']['n'] for r in output),
   first_entry_union=summary(list(union.values())),baseline_first_priced_entry=summary([rows[int(i)] for i in baseline_idx])),
  limitations=['All selection thresholds and rankings use the full observed history, including the current partial day.',
   'Aggregate positive ROI does not imply every day was positive; all daily losses and unresolved results are retained.',
   'A match contributes once per group at its first qualifying saved price; all saved points are searched, never only the preselected old pockets.',
   'Legacy original prices lack independently verified quote timestamps and are included only with explicit legacy counts and a separate strict-timestamp comparison.',
   'Source snapshots and prices are sampled observations; unavailable events and unavailable prices cannot be inferred.',
   'Groups overlap. Sum of group N/profits is not a portfolio. Union uses a separately disclosed earliest-qualifying rule.',
   'Three-condition search is a bounded beam extension of 160 diverse pair seeds, not an exhaustive search of every possible triple.',
   'Saved provider prices and simulated entries are not proof of user execution.'])
 return result,records,entries


def main():
 ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['search','publish']);ap.add_argument('--root',required=True);ap.add_argument('--work',required=True);args=ap.parse_args()
 root=Path(args.root).resolve();work=Path(args.work).resolve();work.mkdir(parents=True,exist_ok=True)
 if args.mode=='search':
  raw=(work/'raw-points.json.gz').read_bytes();data=json.loads(gzip.decompress(raw));rows,quality=prepare(data)
  result,records,entries=search(rows)
  result.update(source_revision=data['source_revision'],code_revision=os.environ.get('GITHUB_SHA'),extraction_quality=data['quality'],analysis_quality=quality,dataset_sha256=hashlib.sha256(raw).hexdigest())
  (work/'historical-summary.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
  for name,value in [('Все прибыльные варианты.json.gz',records),('Все входы выбранных групп.json.gz',entries)]:
   (work/name).write_bytes(gzip.compress(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
  print('HISTORICAL_SEARCH_COMPLETE='+json.dumps(dict(search={k:v for k,v in result['search'].items() if k!='coverage'},groups=[dict(id=g['id'],n=g['stats']['n'],roi=g['stats']['roi']) for g in result['groups']])),flush=True)
 else:
  from refresh_live_results import save_files
  run=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];date=dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date().isoformat()
  dest='research/all-history-groups/'+date+'/'+run+'/'
  files={dest+'summary.json':(work/'historical-summary.json').read_bytes(),dest+'Исходные точки.json.gz':(work/'raw-points.json.gz').read_bytes()}
  for name in ('Все прибыльные варианты.json.gz','Все входы выбранных групп.json.gz'):files[dest+name]=(work/name).read_bytes()
  commit=save_files(root,files);print('HISTORICAL_RESULT_COMMIT='+commit);print('HISTORICAL_RESULT_PATH='+dest)

if __name__=='__main__':main()
