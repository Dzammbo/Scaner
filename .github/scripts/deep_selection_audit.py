"""Read-only comprehensive selection audit; original observations and prices only."""
import collections, datetime as dt, gzip, hashlib, importlib.util, itertools, json, math, os, random, statistics, subprocess, sys
from pathlib import Path
MSK=dt.timezone(dt.timedelta(hours=3)); UTC=dt.timezone.utc
def read(p):
 b=p.read_bytes(); return json.loads(gzip.decompress(b) if p.name.endswith('.gz') else b)
def first(rows):
 d={}
 for r in sorted(rows,key=lambda x:(x['t'],x['order'])):d.setdefault(r['id'],r)
 return list(d.values())
def stats(rows):
 a=[r for r in rows if r.get('profit') is not None]; v=[r['profit'] for r in a]; n=len(v)
 out=dict(selected=len(rows),n=n,unresolved=len(rows)-n,net=round(math.fsum(v),6),roi=math.fsum(v)/n if n else None)
 if not n:return out
 days=collections.defaultdict(list);leagues=collections.defaultdict(list)
 for r in a:days[r['day']].append(r);leagues[r.get('league','unknown')].append(r)
 best=max(days,key=lambda d:math.fsum(r['profit'] for r in days[d]));rest=[r['profit'] for r in a if r['day']!=best]
 top_league=max(leagues,key=lambda d:math.fsum(r['profit'] for r in leagues[d]));lr=[r['profit'] for r in a if r.get('league','unknown')!=top_league]
 mean=math.fsum(v)/n;se=statistics.stdev(v)/math.sqrt(n) if n>1 else None
 out.update(without_top3=math.fsum(sorted(v)[:-3])/(n-3) if n>3 else None,
  without_best_day=math.fsum(rest)/len(rest) if rest else None,best_day=best,
  without_best_league=math.fsum(lr)/len(lr) if lr else None,best_league=top_league,
  normal_ci95=[mean-1.96*se,mean+1.96*se] if se else None,
  days={d:dict(n=len(z),net=math.fsum(r['profit'] for r in z),roi=statistics.fmean(r['profit'] for r in z)) for d,z in sorted(days.items())},
  mean_minute=statistics.fmean(r['minute'] for r in a),mean_price=statistics.fmean(r['price'] for r in a),
  mean_gap=statistics.fmean(r['line']-sum(r['score']) for r in a),zero_sot=sum(r.get('sot')==0 for r in a),
  low_sot=sum(r.get('sot') is not None and r['minute']>0 and r['sot']/r['minute']<.1 for r in a))
 return out
def flat(x,prefix='',out=None):
 out={} if out is None else out
 if isinstance(x,dict):
  for k,v in x.items():
   if k not in ('event_id','id','home_id','away_id','league_id','timestamp'):flat(v,prefix+'.'+k,out)
 elif isinstance(x,(list,tuple)) and len(x)==2:
  flat(x[0],prefix+'.home',out);flat(x[1],prefix+'.away',out)
  try:
   a,b=float(x[0]),float(x[1]);out[prefix+'.total']=a+b;out[prefix+'.imbalance']=abs(a-b)
  except (TypeError,ValueError):pass
 elif isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x):out[prefix]=float(x)
 elif isinstance(x,str):
  try:
   v=float(x)
   if math.isfinite(v):out[prefix]=v
  except ValueError:pass
 return out
def pvalue(v):
 if len(v)<2:return 1.
 sd=statistics.stdev(v)
 return .5*math.erfc((statistics.fmean(v)/(sd/math.sqrt(len(v))))/math.sqrt(2)) if sd else (0. if min(v)>0 else 1.)
def search(rows):
 # All rules are selected using discovery only. Temporal check remains retrospective.
 rows=sorted(rows,key=lambda r:(r['t'],r['order'])); n=len(rows)
 trainmask=sum(1<<i for i,r in enumerate(rows) if r['day']<'2026-10-24' and r.get('profit') is not None)
 testmask=sum(1<<i for i,r in enumerate(rows) if r['day']=='2026-10-24' and r.get('profit') is not None)
 atoms=[]; core=[]; inventories={}; raw=[flat(r.get('raw_features',{})) for r in rows]
 fields=sorted({k for r in rows for k in r.get('features',{})})
 def add(name,value,ids,is_core=True):
  mask=sum(1<<i for i in ids)
  if (mask&trainmask).bit_count()>=20:
   atom=(name,value,mask);atoms.append(atom)
   if is_core:core.append(atom)
 for field in fields:
  if field in ('strong','redcards','evening','need_le2'):continue
  for value in sorted({r['features'].get(field) for r in rows if r['features'].get(field) is not None}):
   add(field,str(value),[i for i,r in enumerate(rows) if r['features'].get(field)==value])
 # Extra fixed domains not present in earlier predeclared pockets.
 extras={
  'score_difference':lambda r:str(min(abs(r['score'][0]-r['score'][1]),3)),
  'line_gap':lambda r:str(round(r['line']-sum(r['score']),2)),
  'home_leads':lambda r:'home' if r['score'][0]>r['score'][1] else 'away' if r['score'][0]<r['score'][1] else 'draw',
  'rank_exact':lambda r:str(r.get('rank')),
  'pressure_trend':lambda r:(r.get('raw_features',{}).get('fresh')or{}).get('pressure_trend')}
 for field,fn in extras.items():
  vals=[fn(r) for r in rows]
  for value in sorted(set(v for v in vals if v is not None)):add(field,value,[i for i,v in enumerate(vals) if v==value])
 # Numeric thresholds fixed by discovery quantiles; inventory retains every usable source field.
 seen_numeric={}; numeric=[]
 for field in sorted({k for r in raw for k in r}):
  vals=sorted(raw[i][field] for i in range(n) if (1<<i)&trainmask and field in raw[i]);distinct=len(set(vals))
  inventories[field]=dict(discovery_n=len(vals),unique=distinct,total_n=sum(field in x for x in raw))
  if len(vals)<40 or distinct<4:continue
  vector=tuple(x.get(field) for x in raw)
  if vector in seen_numeric:inventories[field]['duplicate_of']=seen_numeric[vector];continue
  seen_numeric[vector]=field
  # Exclude history identifiers, timestamps, ordinal counters and non-sport metadata.
  if any(s in field.lower() for s in ('timestamp','kickoff','observed','cache','elapsed','duration','rank','remaining','time','version','confidence')):continue
  cuts=sorted(set(vals[min(len(vals)-1,int((len(vals)-1)*q))] for q in (.25,.5,.75)))
  for cut in cuts:
   for op in ('<=','>'):
    ids=[i for i,x in enumerate(raw) if field in x and (x[field]<=cut if op=='<=' else x[field]>cut)]
    before=len(atoms);add('numeric:'+field,op+str(round(cut,6)),ids,False)
    if len(atoms)>before:numeric.append(atoms[-1])
 masks={};rules=[];tested=0
 def evaluate(combo):
  nonlocal tested
  if len({x[0] for x in combo})!=len(combo):return
  mask=combo[0][2]
  for atom in combo[1:]:mask&=atom[2]
  tr=mask&trainmask;trn=tr.bit_count()
  if trn<25:return
  tested+=1
  if mask in masks:return
  masks[mask]=len(rules);idx=[];m=tr
  while m:
   b=m&-m;idx.append(b.bit_length()-1);m-=b
  v=[rows[i]['profit'] for i in idx];p=pvalue(v);mean=math.fsum(v)/len(v)
  rules.append(dict(rule=[[a[0],a[1]] for a in combo],mask=mask,train_n=trn,train_roi=mean,p=p))
 for a in atoms:evaluate([a])
 for combo in itertools.combinations(core,2):evaluate(combo)
 for combo in itertools.combinations(core,3):evaluate(combo)
 for a in numeric:
  for b in core:evaluate([a,b])
 ordered=sorted(rules,key=lambda x:x['p']);q=1.
 for j in range(len(ordered)-1,-1,-1):
  q=min(q,ordered[j]['p']*len(ordered)/(j+1));ordered[j]['q_bh_approx']=q
 def details(item):
  subset=[r for i,r in enumerate(rows) if item['mask']&(1<<i)]
  tr=[r for r in subset if r['day']<'2026-10-24'];te=[r for r in subset if r['day']=='2026-10-24']
  out={k:v for k,v in item.items() if k!='mask'};out.update(all=stats(subset),discovery=stats(tr),temporal_check=stats(te))
  s=out['all'];t=out['temporal_check'];d=out['discovery']
  out['descriptive_pass']=bool(s['n']>=50 and t['n']>=20 and d['n']>=30 and all((z or -1)>0 for z in (s['roi'],s['without_top3'],s['without_best_day'],s['without_best_league'],t['roi'],d['roi'])))
  out['statistical_pass']=bool(out['descriptive_pass'] and item['q_bh_approx']<.05 and t['normal_ci95'] and t['normal_ci95'][0]>0 and len(s['days'])>=7)
  return out
 # Freeze candidates by discovery score only, before reading temporal-check returns.
 eligible=[r for r in rules if r['train_n']>=40 and r['train_roi']>0]
 eligible.sort(key=lambda x:(x['p'],-x['train_n'],len(x['rule'])))
 chosen=[];sets=[]
 for r in eligible:
  if any((r['mask']&m).bit_count()/max(1,(r['mask']|m).bit_count())>.8 for m in sets):continue
  chosen.append(details(r));sets.append(r['mask'])
  if len(chosen)>=20:break
 descriptive=[]
 for r in eligible:
  if (r['mask']&testmask).bit_count()<20:continue
  d=details(r)
  if d['descriptive_pass']:descriptive.append(d)
 descriptive.sort(key=lambda x:(x['q_bh_approx'],-x['all']['n']))
 return dict(baseline=stats(rows),discovery=stats([r for r in rows if r['day']<'2026-10-24']),temporal_check=stats([r for r in rows if r['day']=='2026-10-24']),atom_count=len(atoms),core_atoms=len(core),tested_rule_expressions=tested,distinct_rule_subsets=len(rules),fdr_below_005=sum(r['q_bh_approx']<.05 for r in rules),discovery_selected_candidates=chosen,descriptive_survivors=descriptive[:25],descriptive_survivor_count=len(descriptive),statistical_survivors=sum(r['statistical_pass'] for r in descriptive),numeric_inventory=inventories),[{k:v for k,v in r.items() if k!='mask'} for r in rules]

def main():
 root=Path(os.environ['COLLECTOR_ROOT']);control=Path(os.environ['CONTROL_ROOT']);run=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']
 source=(control/'.github/scripts/analyze_all_live_entries.py').read_text()
 assert hashlib.sha256(source.encode()).hexdigest()==os.environ['EXPORTER_SHA256']
 # Reuse canonical parsing and settlement, stopping before the legacy search/report stage.
 export=source[:source.index(' config_path=Path(os.environ["EPOCH_CONFIG"])')]
 export=export.replace('def main():','def export_points():',1)
 export=export.replace('order=order,profit=None,final=None)',"order=order,profit=None,final=None,raw_features=dict(board=st,fresh=f,goal_profile=gp,confidence=confidence))")
 export+=' return all_points,manifest,dict(quality),captures\n'
 ns={'__name__':'audit_export'};exec(compile(export,'canonical_export','exec'),ns)
 points,manifest,quality,captures=ns['export_points']();print('EXPORTED',len(points),flush=True)
 report_path=next((root/'research/live-results-refresh').rglob(run+'.json'));report=read(report_path)
 manifest[str(report_path.relative_to(root))]=hashlib.sha256(report_path.read_bytes()).hexdigest()
 index={(r['id'],r['sid'],r['line'],r['price']):r for r in points if r['basis']=='exact_provider_main'}
 primary=[];missing=[];conflicts=[]
 for a in report['ledger']['sports_v8_fresh']:
  q=a.get('market')
  if not q:continue
  r=index.get((a['event_id'],a['source_snapshot_id'],q['line'],q['price']))
  if r is None:missing.append(a['event_id']);continue
  r=dict(r)
  if r['profit']!=a['profit']:conflicts.append(dict(id=a['event_id'],broad=r['profit'],primary=a['profit']))
  r.update(profit=a['profit'],final=a['final'],rank=a['sports_rank']);ns['derive_features'](r);primary.append(r)
 assert not missing,missing
 assert len([r for r in primary if r['profit'] is not None])==report['current']['sports_v8_fresh']['n']
 assert abs(math.fsum(r['profit'] for r in primary if r['profit'] is not None)-report['current']['sports_v8_fresh']['unit_profit'])<1e-8
 allsearch,rules=search(primary);print('SEARCHED_PRIMARY',len(rules),flush=True)
 last=max(t for t,_ in captures);mature=[r for r in primary if r['t']<=last-3*3600]
 maturesearch,maturerules=search(mature);print('SEARCHED_MATURE',len(maturerules),flush=True)
 start=dt.datetime.fromisoformat(report['statistics_epoch']['started_at_utc']).timestamp()
 exact=[r for r in points if r['basis']=='exact_provider_main']
 cohorts={'before_current_epoch':[r for r in exact if r['t']<start], 'current_epoch':[r for r in exact if r['t']>=start]}
 stages={}
 pocket=lambda r:r.get('need')==2 and r.get('sot') is not None and r['minute']>0 and r['sot']/r['minute']<.1
 for name,rs in cohorts.items():
  window=[r for r in rs if 8<=r['minute']<=60];strong=[r for r in window if r.get('strong')];hours=[r for r in strong if 8<=r['hour']<24];profile=[r for r in hours if r.get('gp_ratio') is not None and r['gp_ratio']<1.1]
  stages[name]={k:stats(first(a)) for k,a in [('any_exact',rs),('window8_60',window),('strong8_60',strong),('strong_hours',hours),('strong_hours_profile',profile),('old_pocket_first_qualifying',list(filter(pocket,rs))),('old_pocket_strong8_60',list(filter(pocket,strong))),('old_pocket_strong_hours_profile',list(filter(pocket,profile)))]}
  stages[name]['old_pocket_no_zero']=stats([r for r in first(list(filter(pocket,rs))) if r['sot']>0])
 stages['actual_primary']=stats(primary);stages['old_pocket_at_primary_entry']=stats([r for r in primary if pocket(r)])
 # Same eventual IDs, different saved entry times. This is descriptive, not causal.
 broad_current=first([r for r in cohorts['current_epoch'] if pocket(r)]);pi={r['id']:r for r in primary};bi={r['id']:r for r in broad_current}
 common=sorted(set(pi)&set(bi));paired=[]
 for eid in common:
  a,b=pi[eid],bi[eid]
  if a['profit'] is None or b['profit'] is None:continue
  paired.append(dict(id=eid,primary_profit=a['profit'],pocket_profit=b['profit'],minute_change=b['minute']-a['minute'],price_change=b['price']-a['price'],line_change=b['line']-a['line'],later=b['t']>a['t']))
 paired_stats=dict(n=len(paired),primary_net=math.fsum(r['primary_profit'] for r in paired),pocket_net=math.fsum(r['pocket_profit'] for r in paired),later_count=sum(r['later'] for r in paired),mean_minute_change=statistics.fmean(r['minute_change'] for r in paired) if paired else None)
 # Legacy selection-policy results from actual frozen receipt choices, independent per model.
 legacy=collections.defaultdict(dict);versions=collections.defaultdict(list);rec_manifest={};epoch_ids=collections.Counter();feature_gaps=collections.Counter()
 for base in [root/'research/live-background/primary',root/'research/live-background/experimental',root/'research/live-lab']:
  for path in sorted((base/'receipts').rglob('*.json*')):
   receipt=read(path)
   if receipt.get('failed'):continue
   ep=(receipt.get('statistics_epoch')or{}).get('id','legacy');epoch_ids[ep]+=1
   choices=dict(receipt.get('choices')or{});ctrl=(receipt.get('control_evaluation')or{}).get('choices')
   if ctrl is not None:choices['standard_v8']=ctrl
   for policy,rs in choices.items():
    for a in rs:
     eid=str(a['event_id']);key=(ep,policy)
     if eid in legacy[key]:continue
     q=a.get('market')or{};r=index.get((eid,receipt.get('snapshot_id'),q.get('line'),q.get('price')))
     if r is None:feature_gaps['legacy_choices_without_exact_point']+=1;continue
     legacy[key][eid]=dict(r)
 legacy_results={ep:{p:stats(list(z.values())) for (e,p),z in legacy.items() if e==ep} for ep in sorted({e for e,p in legacy})}
 # Nonindependent all-points ROI versus first-entry and equal-match weighting.
 repeats={}
 for name,rs in cohorts.items():
  z=[r for r in rs if pocket(r)];settled=[r for r in z if r['profit'] is not None];per=collections.defaultdict(list)
  for r in settled:per[r['id']].append(r['profit'])
  repeats[name]=dict(points=len(z),settled_points=len(settled),unique_events=len({r['id'] for r in z}),all_points_roi=statistics.fmean(r['profit'] for r in settled) if settled else None,equal_match_mean_roi=statistics.fmean(statistics.fmean(v) for v in per.values()) if per else None,first=stats(first(z)))
 # All priced primary source metrics, missingness, stale or impossible board combinations.
 quality.update(primary_points=len(primary),primary_missing=missing,primary_vs_broad_conflicts=conflicts,
  primary_sot_below_score=sum(r.get('sot') is not None and r['sot']<sum(r['score']) for r in primary),
  primary_quote_lag_seconds=[r['quote_time']-r['t'] for r in primary if r.get('quote_time') is not None],legacy_choice_gaps=dict(feature_gaps))
 result=dict(generated_at_utc=dt.datetime.now(UTC).isoformat(),source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),rules_revision=os.environ['GITHUB_SHA'],report_path=str(report_path.relative_to(root)),report_generated_at_utc=report['generated_at_utc'],quality=quality,current=allsearch,mature_cohort=maturesearch,mature_entry_cutoff_utc=dt.datetime.fromtimestamp(last-3*3600,UTC).isoformat(),stages=stages,paired=paired_stats,repeated_point_bias=repeats,legacy_receipt_results=legacy_results,epoch_receipt_counts=dict(epoch_ids),manifest_sha256=manifest,
  methodology=dict(temporal_split='discovery entry days before 24 October; retrospective check 24 October, not independent prospective evidence',mature_cohort='entry at least 3 hours before latest saved board; pending remain pending',search='all singles/pairs/triples of coarse domains plus discovery quartile numeric singles and numeric/coarse pairs; duplicate row subsets removed',multiplicity='one-sided normal mean tests, BH across every distinct tested subset; approximate descriptive only',minimums='25 discovery to test,40 to nominate; descriptive survivor >=50 total,>=30 discovery,>=20 check, positive trimmed/day/league exclusions; statistical survivor also >=7 days and positive check CI',selection_changed=False))
 dest='research/deep-selection-audit/'+dt.datetime.now(UTC).astimezone(MSK).date().isoformat()+'/'+run+'/'
 encode=lambda x:(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n').encode()
 files={dest+'results.json':encode(result),dest+'primary-points.json':encode([{k:v for k,v in r.items() if k!='raw_features'} for r in primary]),dest+'entry-points.json.gz':gzip.compress(encode(points),mtime=0),dest+'search-rules.json.gz':gzip.compress(encode(dict(all=rules,mature=maturerules)),mtime=0),dest+'paired-entries.json':encode(paired)}
 for i in range(0,len(primary),25):files[dest+'primary-features-'+str(i//25).zfill(3)+'.json']=encode(primary[i:i+25])
 spec=importlib.util.spec_from_file_location('save_results',control/'.github/scripts/refresh_live_results.py');saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver)
 commit=saver.save_files(root,files)
 print('AUDIT_RESULT_PATH='+dest+'results.json',flush=True);print('AUDIT_RESULT_COMMIT='+commit,flush=True)
 print(json.dumps(dict(primary=result['current']['baseline'],rules=len(rules),survivors=result['current']['descriptive_survivor_count'],statistical=result['current']['statistical_survivors']),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
