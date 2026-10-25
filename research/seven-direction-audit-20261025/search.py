"""Predefined coarse scenario search with match-level temporal cohorts."""
import json,gzip,collections,math,hashlib,datetime as dt
from pathlib import Path
import numpy as np
from scipy.stats import t as student_t
ROOT=Path(__file__).resolve().parent
rows=json.load(gzip.open(ROOT/'derived.json.gz'));N=len(rows);ids=sorted({r['event_id'] for r in rows});idmap={x:i for i,x in enumerate(ids)}
eid=np.array([idmap[r['event_id']] for r in rows]);times=np.array([r['ts'] for r in rows]);minutes=np.array([r['minute'] for r in rows]);goals=np.array([sum(r['score']) for r in rows]);margin=np.array([abs(r['score'][0]-r['score'][1]) for r in rows]);scode=np.array([int(r['score'][0])*100+int(r['score'][1]) for r in rows]);lines=np.array([r['line'] for r in rows]);gap=lines-goals;days=np.array([r['day'] for r in rows]);need=np.floor(lines+.25)+1-goals
def arr(fn):return np.array([np.nan if (v:=fn(r)) is None else v for r in rows],float)
sot=arr(lambda r:sum(r['stats']['on_target'])/r['minute'] if r['stats']['on_target'] is not None else None);danger=arr(lambda r:sum(r['stats']['dangerous_attacks'])/r['minute'] if r['stats']['dangerous_attacks'] is not None else None)
cut1=dt.datetime.fromisoformat('2026-10-22T00:00:00+03:00').timestamp();cut2=dt.datetime.fromisoformat('2026-10-24T00:00:00+03:00').timestamp();anchor=np.full(len(ids),np.inf)
np.minimum.at(anchor,eid,times);fold=np.where(anchor[eid]<cut1,0,np.where(anchor[eid]<cut2,1,2));confirmed=arr(lambda r:r.get('confirmed_at'));known=np.array([r['final'] is not None for r in rows]);usable=known & ((fold==2)|((fold==0)&(times<cut1)&(confirmed<cut1))|((fold==1)&(times<cut2)&(confirmed<cut2)))
profit={}
for side in ('over','under'):
    out=np.full(N,np.nan)
    for i,r in enumerate(rows):
        if r['final'] is None:continue
        total=sum(r['final']);q=round(r['line']*4);legs=[q-1,q+1] if q%2 else [q];price=r[side]
        out[i]=sum(price-1 if (total*4>x if side=='over' else total*4<x) else 0 if total*4==x else -1 for x in legs)/len(legs)
    profit[side]=out
scenarios=[('all',np.ones(N,bool)),('draw',margin==0),('margin1',margin==1),('margin2plus',margin>=2),('goals0',goals==0),('goals1',goals==1),('goals2',goals==2),('need_le2',need<=2),('need4plus',need>=4),('low_sot',sot<.1),('sot010_020',(sot>.1)&(sot<=.2)),('danger_lt1',danger<1),('integer',np.isclose(lines%1,0)),('quarter',np.isclose((lines*4)%2,1)),('sot_goal1',(sot>.1)&(sot<=.2)&(goals==1)),('need2_low_sot',(need==2)&(sot<.1)),('integer_need_le2',np.isclose(lines%1,0)&(need<=2)),('draw_gap05',(margin==0)&np.isclose(gap,.5)),('lead2_goals2',(margin>=2)&(goals==2)),('goal1_gap125_150',(goals==1)&(gap>=1.25)&(gap<=1.5))]
windows=[(5,29),(30,44),(45,59),(60,69),(70,79),(80,89),(45,69),(60,89),(5,89)]
groups=[]
def add(family,label,mask):
    idx=np.flatnonzero(mask)
    if len(np.unique(eid[idx]))>=12:groups.append((family,label,idx))
league=np.array([r['league_key'] for r in rows]);names={k:collections.Counter(r['league'] for r in rows if r['league_key']==k).most_common(1)[0][0] for k in set(league)}
for k in sorted(set(league)):add('1_league',str(names[k])+' ['+k+']',league==k)
fav=np.array([str(r['favorite_state']) for r in rows]);favside=arr(lambda r:r['favorite_side']);fp=arr(lambda r:r['favorite_probability'])
for s in ('leading','drawing','trailing'):
    add('2_favorite','favorite_'+s,fav==s);add('2_favorite','strong_favorite_'+s,(fav==s)&(fp>=.60))
    for side in (0,1):add('2_favorite',('home' if side==0 else 'away')+'_favorite_'+s,(fav==s)&(favside==side))
ga=arr(lambda r:r['observed_goal_age']);ra=arr(lambda r:r['observed_red_age'])
for label,a in (('goal',ga),('red',ra)):
    add('3_reaction',label+'_observed_0_5',(a>=0)&(a<=5));add('3_reaction',label+'_observed_5_10',(a>5)&(a<=10));add('3_reaction',label+'_observed_0_10',(a>=0)&(a<=10))
reaction=np.array([str(r['goal_reaction']) for r in rows])
for k in ('leader_scored','trailer_scored','draw_broken'):add('3_reaction',k,reaction==k)
add('3_reaction','equalizer_observed',np.array([r['recent_equalizer'] for r in rows]))
for k in ('first_goal_seen_early','first_goal_seen_late','known_drought15','observed_lead_reversed','two_goals_observed_recent'):add('4_trajectory',k,np.array([r[k] for r in rows]))
shift=arr(lambda r:r['total_shift']);pt=arr(lambda r:r['prematch_total_line'])
for label,mask in [('live_total_down_ge1',shift<=-1),('live_total_down025_075',(shift<=-.25)&(shift> -1)),('same_total',np.isclose(shift,0)),('live_total_up025_075',(shift>=.25)&(shift<1)),('live_total_up_ge1',shift>=1),('pregame_total_le225',pt<=2.25),('pregame_total_ge325',pt>=3.25),('high_pregame_total_falls',(pt>=3.25)&(shift<=-.5)),('low_pregame_total_rises',(pt<=2.25)&(shift>=.5))]:add('5_market_path',label,mask)
category=np.array([str(r['category']) for r in rows]);kind=np.array([r['competition_kind'] for r in rows])
for k in sorted(set(category)):add('6_type','category:'+k,category==k)
for k in sorted(set(kind)):add('6_type','title_type:'+k,kind==k)
def select(eligible,delay):
    _,pos=np.unique(eid[eligible],return_index=True);first=eligible[np.sort(pos)]
    if not delay:return first
    t=np.full(len(ids),np.inf);s=np.full(len(ids),-1);t[eid[first]]=times[first]+delay;s[eid[first]]=scode[first]
    candidates=eligible[(times[eligible]>=t[eid[eligible]])&(scode[eligible]==s[eid[eligible]])]
    if not len(candidates):return candidates
    _,pos=np.unique(eid[candidates],return_index=True);return candidates[np.sort(pos)]
def stats(idx,side):
    ii=idx[usable[idx]];v=profit[side][ii];n=len(ii)
    result=dict(n=n,selected=len(idx),roi=float(v.mean()) if n else None,net=float(v.sum()),without_top3=float(np.sort(v)[:-3].mean()) if n>3 else None)
    byday={d:dict(n=int(np.sum(days[ii]==d)),roi=float(v[days[ii]==d].mean()),net=float(v[days[ii]==d].sum())) for d in set(days[ii])}
    result['days']=byday
    if len(byday)>1:
        best=max(byday,key=lambda d:byday[d]['net']);keep=days[ii]!=best;result['without_best_day']=float(v[keep].mean()) if keep.any() else None
    else:result['without_best_day']=None
    return result
cache={};tests=[];rule_count=collections.Counter();passed=[];exploratory=[];league_seeds=[];combo_masks=[(label,lo,hi,mask & (minutes>=lo)&(minutes<=hi)) for label,mask in scenarios for lo,hi in windows]
allfile=gzip.open(ROOT/'all_rules.jsonl.gz','wt')
for side in ('over','under'):
    price=np.array([r[side] for r in rows]);pricecases=[('any_price',np.ones(N,bool)),('price170_200',(price>=1.7)&(price<2)),('price200_250',(price>=2)&(price<2.5))]
    # Price bands are standalone scenarios, not an unconstrained extra third feature.
    masks=combo_masks+[(label,lo,hi,mask&(minutes>=lo)&(minutes<=hi)) for label,mask in pricecases[1:] for lo,hi in windows]
    for gi,(family,label,gidx) in enumerate(groups):
        for scenario,lo,hi,mask in masks:
            eligible=gidx[mask[gidx]]
            if len(eligible)<12:continue
            for delay in (0,180,300):
                idx=select(eligible,delay)
                if len(idx)<12:continue
                rule_count[family]+=1
                signature=side+':'+hashlib.sha256(idx.tobytes()).hexdigest()
                if signature in cache:
                    cache[signature]['aliases']+=1;continue
                folds=[stats(idx[fold[idx]==f],side) for f in (0,1,2)];whole=stats(idx,side)
                testidx=idx[(fold[idx]==2)&usable[idx]];v=profit[side][testidx]
                sd=float(v.std(ddof=1)) if len(v)>1 else 0
                p=float(student_t.sf(v.mean()/(sd/math.sqrt(len(v))),len(v)-1)) if len(v)>=10 and sd>0 else (0. if len(v)>=30 and np.all(v>0) else 1.)
                row=dict(signature=signature,family=family,group=label,scenario=scenario,window=[lo,hi],delay_seconds=delay,side=side,aliases=1,total=whole,folds=folds,p_late=p,q_late=None)
                cache[signature]=row;tests.append((p,signature));allfile.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
                def positive(s):return s['roi'] is not None and s['roi']>0 and s['without_top3'] is not None and s['without_top3']>0
                bothdays=[z for z in folds[2]['days'].values() if z['n']>=10 and z['roi']>0]
                stable=(all(folds[f]['n']>=v for f,v in enumerate((40,25,30))) and whole['n']>=150 and all(positive(s) for s in folds) and whole['without_best_day'] is not None and whole['without_best_day']>0 and len(bothdays)>=2)
                seed=(all(folds[f]['n']>=v for f,v in enumerate((15,10,15))) and whole['n']>=60 and all(positive(s) for s in folds) and whole['without_best_day'] is not None and whole['without_best_day']>0)
                if stable or seed:
                    row['entry_indices']=idx.tolist();row['descriptive_stable']=stable;row['pre_final_screen']=all(folds[f]['n']>=v and positive(folds[f]) for f,v in enumerate((40,25)));passed.append(row)
                if family=='1_league' and whole['n']>=25 and whole['roi']>0 and whole['without_top3'] is not None and whole['without_top3']>0 and sum(s['n']>=3 and s['roi']>0 for s in folds)>=2:
                    league_seeds.append(dict(row,entry_indices=idx.tolist()))
        if gi%30==0:print('SEARCH',side,gi,len(groups),'UNIQUE',len(cache),'CANDIDATES',len(passed),flush=True)
allfile.close()
ordered=sorted(tests);q=1.;qmap={}
for j in range(len(ordered)-1,-1,-1):
    p,key=ordered[j];q=min(q,p*len(ordered)/(j+1));qmap[key]=q
rng=np.random.default_rng(20261025)
for row in passed:
    row['q_late']=qmap[row['signature']];idx=np.array(row['entry_indices']);ii=idx[(fold[idx]==2)&usable[idx]];v=profit[row['side']][ii]
    draws=np.array([rng.choice(v,len(v),replace=True).mean() for _ in range(4000)]);row['late_bootstrap_ci95']=np.quantile(draws,[.025,.975]).tolist()
    ds=sorted(set(days[ii]));dv={d:v[days[ii]==d] for d in ds};draw=[]
    for _ in range(4000):
        dd=rng.choice(ds,len(ds),replace=True);draw.append(np.concatenate([dv[d] for d in dd]).mean())
    row['late_day_cluster_ci95']=np.quantile(draw,[.025,.975]).tolist()
    row['statistical_pass']=bool(row['descriptive_stable'] and row['q_late']<=.05 and row['late_bootstrap_ci95'][0]>0 and row['late_day_cluster_ci95'][0]>0)
for row in league_seeds:row['q_late']=qmap[row['signature']]
passed.sort(key=lambda r:(not r['descriptive_stable'],-r['folds'][2]['without_top3'],-r['total']['n']))
league_seeds.sort(key=lambda r:(-sum(s['n']>=3 and s['roi']>0 for s in r['folds']),-r['total']['n'],-r['total']['without_top3']))
summary=dict(points=N,events=len(ids),confirmed_events=len({r['event_id'] for r in rows if r['final'] is not None}),cohort_events={str(f):int(np.sum(np.where(anchor<cut1,0,np.where(anchor<cut2,1,2))==f)) for f in (0,1,2)},known_labels=int(known.sum()),temporally_usable_labels=int(usable.sum()),groups=collections.Counter(f for f,l,i in groups),evaluated_rules=dict(rule_count),unique_entry_selections=len(cache),descriptive_stable=sum(r['descriptive_stable'] for r in passed),statistical_pass=sum(r['statistical_pass'] for r in passed),exploratory_consistent=len(passed),league_seed_count=len(league_seeds),minimum_q=min(qmap.values()) if qmap else None)
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));(ROOT/'candidates.json').write_text(json.dumps(passed,ensure_ascii=False,separators=(',',':')));(ROOT/'league_seeds.json').write_text(json.dumps(league_seeds,ensure_ascii=False,separators=(',',':')))
print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)
