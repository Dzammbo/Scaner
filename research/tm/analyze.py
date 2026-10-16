"""Predeclared exploratory grid; untouched chronological test, no TB imports."""
import argparse,gzip,itertools,json,math,statistics,random
from collections import defaultdict,Counter
from pathlib import Path
CHECKPOINTS=(30,40,60,70,80)

def num(x):
    try:
        x=float(x)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None

def score(row,period='2'):
    v=(row.get('scores')or{}).get(period)
    if not isinstance(v,dict):return None
    a,b=num(v.get('home')),num(v.get('away'))
    return (int(a),int(b)) if a is not None and b is not None and min(a,b)>=0 else None

def parsed(payload):
    out={}
    if payload.get('success')!=1:return out
    for field,sides in (payload.get('results')or{}).items():
        if not isinstance(sides,dict):continue
        out[field]={}
        for side in ('home','away'):
            points=[]
            for r in sides.get(side,[]):
                m,v,ts=num(r.get('time_str')),num(r.get('val')),num(r.get('created_at'))
                if None not in (m,v,ts) and m>=0 and v>=0:points.append((ts,m,v))
            out[field][side]=sorted(set(points))
    return out

def at(points,t,cutoff):
    eligible=[p for p in points if p[0]<=cutoff and p[1]<=t]
    return eligible[-1][2] if eligible else None

def both(series,field,t,cutoff):
    v=[at(series.get(field,{}).get(side,[]),t,cutoff) for side in ('home','away')]
    return v if None not in v else None

def delta(series,field,start,end,cutoff):
    a,b=both(series,field,start,cutoff),both(series,field,end,cutoff)
    if a is None or b is None:return None
    # Counter revisions/reset in the interval invalidate, never create low tempo.
    for side in ('home','away'):
        ps=[p for p in series.get(field,{}).get(side,[]) if p[0]<=cutoff and p[1]<=end]
        if any(y[2]<x[2] for x,y in zip(ps,ps[1:]) if y[1]>=start):return None
    d=sum(b)-sum(a)
    return d if d>=0 else None

def stats(rows,tid,venue=None):
    rs=[r for r in rows if str(r['home']['id'])==tid or str(r['away']['id'])==tid]
    rs=rs[-20:]
    if venue:rs=[r for r in rs if str(r[venue]['id'])==tid]
    if not rs:return {'n':0}
    ft=[score(r) for r in rs];tot=[sum(v) for v in ft]
    gf=[v[0 if str(r['home']['id'])==tid else 1] for r,v in zip(rs,ft)]
    ht=[score(r,'1') for r in rs];sh=[sum(f)-sum(h) for f,h in zip(ft,ht) if h and all(a<=b for a,b in zip(h,f))]
    return {'n':len(rs),'mean_total':statistics.mean(tot),'gf':statistics.mean(gf),'ga':statistics.mean(tot)-statistics.mean(gf),
            'goals_0_1':sum(v<=1 for v in tot),'goals_2':tot.count(2),'goals_3_plus':sum(v>=3 for v in tot),
            'over_1_5':sum(v>=2 for v in tot),'second_half_n':len(sh),'second_half_mean':statistics.mean(sh) if sh else None}

def profile(row,earlier):
    h,a=str(row['home']['id']),str(row['away']['id'])
    same=[r for r in earlier if str(r['league']['id'])==str(row['league']['id']) and int(r['time'])+3*3600<int(row['time']) and score(r)]
    hs,aws=stats(same,h),stats(same,a);lr=same[-50:]
    lm=statistics.mean(sum(score(r)) for r in lr) if lr else None
    ratio=((hs['gf']+aws['ga']+aws['gf']+hs['ga'])/2)/lm if min(hs['n'],aws['n'])>=5 and len(lr)>=10 and lm else None
    return {'home':hs,'away':aws,'home_venue':stats(same,h,'home'),'away_venue':stats(same,a,'away'),
            'league_n':len(lr),'league_mean':lm,'ratio':ratio,'scope':'same_league_prior_matches_only_3h_completion_buffer'}

def quote(payload,t,cutoff,current):
    result=payload.get('results')or{};rows=(result.get('odds')or{}).get('1_3',[])
    candidates=[]
    for r in rows:
        tm,ts=num(r.get('time_str')),num(r.get('add_time'))
        if tm is not None and ts is not None and tm<=t and ts<=cutoff:candidates.append((ts,r))
    if not candidates:return None
    ts,r=max(candidates,key=lambda x:x[0])
    if cutoff-ts>120 or ts<=0:return None
    # Conflicting latest quotes are not resolved using future prices.
    latest=[x for stamp,x in candidates if stamp==ts]
    if len({(x.get('ss'),x.get('handicap'),x.get('under_od')) for x in latest})>1:return None
    parts=str(r.get('handicap','')).replace('/',',').split(',');values=[num(p) for p in parts]
    if not values or None in values:return None
    line=sum(values)/len(values);under=num(r.get('under_od'));over=num(r.get('over_od'))
    ss=r.get('ss','').replace(':','-')
    if str((result.get('stats')or{}).get('matching_dir'))=='-1':ss='-'.join(reversed(ss.split('-')))
    if ss!='-'.join(str(int(v)) for v in current) or under is None or under<=1 or abs(line*4-round(line*4))>1e-8:return None
    return {'line':line,'under':under,'over':over,'timestamp':ts,'age':cutoff-ts}

def profit(ft,line,price):
    legs=[line] if round(line*4)%2==0 else [line-.25,line+.25]
    return sum(price-1 if ft<x else 0 if ft==x else -1 for x in legs)/len(legs)

def feature_rows(matches,targets,details):
    records=[];coverage=Counter();prior=[]
    split={}
    rs=[r for r in matches if str(r['id']) in targets]
    if rs:
        # Shared calendar cutoffs across leagues; no validation/test date overlap.
        t1=int(rs[int(len(rs)*.6)]['time']);t2=int(rs[int(len(rs)*.8)]['time'])
        for r in rs:split[str(r['id'])]='train' if int(r['time'])<t1 else 'validation' if int(r['time'])<t2 else 'test'
    for row in matches:
        eid=str(row['id']);ft=score(row);ht=score(row,'1')
        if eid not in targets:
            prior.append(row);continue
        coverage['targets']+=1
        raw=details.get(eid,{})
        ser=parsed(raw.get('trend',{}).get('payload',{}));odds=raw.get('odds',{}).get('payload',{})
        if ser:coverage['trend_matches']+=1
        if odds.get('success')==1:coverage['odds_response_matches']+=1
        if not ft:
            coverage['missing_ft']+=1;prior.append(row);continue
        valid_goals=all(ser.get('goals',{}).get(side) and ser['goals'][side][-1][2]==ft[i] for i,side in enumerate(('home','away')))
        if not valid_goals:
            coverage['invalid_goal_series']+=1;prior.append(row);continue
        # Validate cumulative terminal values against the independent ended record.
        # This is a coverage check, never a live predictive feature.
        for field in ('on_target','off_target','dangerous_attacks','xg','redcards'):
            end=(row.get('stats')or{}).get(field)
            valid=isinstance(end,list) and len(end)==2
            if valid:
                for i,side in enumerate(('home','away')):
                    ps=ser.get(field,{}).get(side,[]);value=num(end[i])
                    if not ps or value is None or abs(ps[-1][2]-value)>.05:valid=False
            if not valid:
                ser.pop(field,None)
                coverage['invalid_terminal_'+field]+=1
        prof=profile(row,prior)
        for t in CHECKPOINTS:
            # No final totals used as live features; timestamps constrain every point.
            timestamps=[p[0] for sides in ser.values() for ps in sides.values() for p in ps if t<=p[1]<t+1]
            if not timestamps:continue
            cutoff=min(timestamps)
            cur=both(ser,'goals',t,cutoff)
            # Local corrections are visible at decision time; future corrections
            # must never remove an otherwise valid earlier observation.
            local_correction=False
            for side in ('home','away'):
                ps=[p for p in ser.get('goals',{}).get(side,[]) if p[0]<=cutoff and p[1]<=t]
                if any(y[2]<x[2] and y[1]>=t-20 for x,y in zip(ps,ps[1:])):local_correction=True
            if local_correction:
                coverage['decision_points_with_recent_score_correction']+=1
                continue
            if cur is None or any(a>b for a,b in zip(cur,ft)):continue
            on=delta(ser,'on_target',t-10,t,cutoff);off=delta(ser,'off_target',t-10,t,cutoff)
            shots=on+off if on is not None and off is not None else None
            prevon=delta(ser,'on_target',t-20,t-10,cutoff) if t!=60 else None
            da=delta(ser,'dangerous_attacks',t-10,t,cutoff)
            prevda=delta(ser,'dangerous_attacks',t-20,t-10,cutoff) if t!=60 else None
            xg=delta(ser,'xg',t-10,t,cutoff);xgt=both(ser,'xg',t,cutoff)
            g10=delta(ser,'goals',t-10,t,cutoff);g5=delta(ser,'goals',t-5,t,cutoff)
            g20=delta(ser,'goals',t-20,t,cutoff) if t!=60 else None
            rc=both(ser,'redcards',t,cutoff)
            flags={
              'no_on_target_10':on==0,'shots_10_le_3':shots is not None and shots<=3,
              'low_threat':on is not None and on<=1 and shots is not None and shots<=3,
              'xg_10_le_015':xg is not None and xg<=.15,
              'on_target_decay':on is not None and prevon is not None and prevon>=2 and on<=1,
              'attack_decay':da is not None and prevda is not None and prevda>=10 and da<=prevda*.5,
              'no_goal_10':g10==0,'quick_goals_then_pause':g20 is not None and g20>=2 and g10 is not None and g10<g20 and g5==0,
              'ht_3plus':t>=60 and ht is not None and sum(ht)>=3,
              'lead_2plus':abs(cur[0]-cur[1])>=2,'draw':cur[0]==cur[1],
              'goals_2plus':sum(cur)>=2,'score_0_0':sum(cur)==0,
              'profile_low':prof['ratio'] is not None and prof['ratio']<=.85,
              'profile_high':prof['ratio'] is not None and prof['ratio']>=1.15,
              'profile_both_under_25':min(prof['home']['n'],prof['away']['n'])>=5 and max(prof['home']['mean_total'],prof['away']['mean_total'])<=2.5,
              'overconversion':xgt is not None and sum(cur)-sum(xgt)>=1.5,
              'red_card':rc is not None and sum(rc)>0}
            q=quote(odds,t,cutoff,cur)
            record={'event_id':eid,'league_id':str(row['league']['id']),'time':int(row['time']),'minute':t,'split':split[eid],
                    'home':row['home']['name'],'away':row['away']['name'],'score':[int(v) for v in cur],
                    'ft':list(ft),'remaining':sum(ft)-sum(cur),'flags':flags,'profile':prof,'quote':q,
                    'on10':on,'shots10':shots,'da10':da,'xg10':xg,'cutoff_timestamp':cutoff}
            record['profit']=profit(sum(ft),q['line'],q['under']) if q else None
            records.append(record)
        prior.append(row)
    coverage['feature_rows']=len(records);coverage['feature_matches']=len({r['event_id'] for r in records})
    coverage['quoted_rows']=sum(r['quote'] is not None for r in records)
    coverage['quoted_matches']=len({r['event_id'] for r in records if r['quote']})
    return records,dict(coverage)

def wilson(w,n,z=1.96):
    if not n:return None
    p=w/n;a=1+z*z/n;c=(p+z*z/(2*n))/a;d=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/a
    return [c-d,c+d]

def evaluate(rows,flags,target):
    selected=[r for r in rows if all(r['flags'][f] for f in flags)]
    control=[r for r in rows if not all(r['flags'][f] for f in flags)]
    strata=defaultdict(list)
    for r in control:strata[(r['league_id'],tuple(r['score']))].append(r)
    matched=[r for r in selected if len(strata[(r['league_id'],tuple(r['score']))])>=5]
    baseline=sum(sum(x['remaining']<=target for x in strata[(r['league_id'],tuple(r['score']))])/len(strata[(r['league_id'],tuple(r['score']))]) for r in matched)/len(matched) if matched else None
    rate=sum(r['remaining']<=target for r in matched)/len(matched) if matched else None
    win=sum(r['remaining']<=target for r in selected);n=len(selected)
    priced=[r for r in selected if r['quote']]
    return {'n':n,'wins':win,'rate':win/n if n else None,'ci95':wilson(win,n),'matched_n':len(matched),
            'matched_baseline':baseline,'matched_lift':rate-baseline if rate is not None else None,
            'mean_remaining':statistics.mean(r['remaining'] for r in selected) if n else None,
            'priced_n':len(priced),'roi':statistics.mean(r['profit'] for r in priced) if priced else None,
            'mean_price':statistics.mean(r['quote']['under'] for r in priced) if priced else None,
            'league_n':dict(Counter(r['league_id'] for r in selected))}

def permutation_p(rows,flags,target,repetitions=4999):
    """One-sided, within league/current-score strata, one row per match."""
    groups=defaultdict(lambda:[[],[]])
    for r in rows:
        selected=all(r['flags'][f] for f in flags)
        groups[(r['league_id'],tuple(r['score']))][0 if selected else 1].append(int(r['remaining']<=target))
    groups=[g for g in groups.values() if g[0] and len(g[1])>=5]
    n=sum(len(a) for a,b in groups)
    if not n:return None
    observed=sum(sum(a)-len(a)*sum(b)/len(b) for a,b in groups)/n
    rng=random.Random(20261016);greater=0
    for _ in range(repetitions):
        total=0
        for a,b in groups:
            successes=sum(rng.sample(a+b,len(a)))
            total+=successes-len(a)*(sum(a)+sum(b)-successes)/len(b)
        greater+=total/n>=observed-1e-12
    return (greater+1)/(repetitions+1)


def search(records):
    names=sorted(next(iter(records))['flags']) if records else []
    # Fixed finite grid, explicitly counted. No threshold tuning on validation/test.
    rules=[(n,) for n in names]+list(itertools.combinations(names,2))
    found=[];trials=0
    for minute in CHECKPOINTS:
        train=[r for r in records if r['minute']==minute and r['split']=='train']
        for target in (1,2):
            for flags in rules:
                trials+=1;m=evaluate(train,flags,target)
                if m['n']>=60 and m['matched_n']>=40 and m['matched_lift'] is not None and m['matched_lift']>0:
                    found.append({'minute':minute,'target':target,'flags':flags,'train':m})
    # Select 20 discovery candidates only by training data.
    found.sort(key=lambda r:r['train']['matched_lift']*math.sqrt(r['train']['matched_n']),reverse=True)
    shortlist=found[:20]
    validated=[]
    for r in shortlist:
        val=[x for x in records if x['minute']==r['minute'] and x['split']=='validation']
        r['validation']=evaluate(val,r['flags'],r['target'])
        v=r['validation']
        if v['n']>=20 and v['matched_n']>=15 and v['matched_lift'] is not None and v['matched_lift']>0:validated.append(r)
    validated.sort(key=lambda r:r['validation']['matched_lift']*math.sqrt(r['validation']['matched_n']),reverse=True)
    finalists=validated[:5]
    for r in finalists:
        test=[x for x in records if x['minute']==r['minute'] and x['split']=='test']
        r['test']=evaluate(test,r['flags'],r['target'])
        r['permutation_p']=permutation_p(test,r['flags'],r['target'])
        r['status']='unconfirmed_exploratory_candidate'
    ordered=sorted([r for r in finalists if r['permutation_p'] is not None],key=lambda r:r['permutation_p'])
    previous=0
    for i,r in enumerate(ordered):
        previous=max(previous,min(1,r['permutation_p']*(len(ordered)-i)))
        r['holm_p']=previous
        if previous<=.05 and r['test']['matched_n']>=50:
            r['status']='sports_effect_passed_single_holdout_not_profitability'
    return {'tested_rules':trials,'training_eligible':len(found),'validation_candidates':shortlist,'finalists':finalists,
            'selection_objective':'matched additional-goals containment; not optimized ROI',
            'warning':'Multiple exploratory tests; no rule is production approved. Five final hypotheses share one test period; Wilson intervals are unadjusted; test permutation p-values use Holm correction across finalists. Price execution remains unverified.'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('directory');args=ap.parse_args();folder=Path(args.directory)
    manifest=json.loads((folder/'manifest.json').read_text());matches=sorted(json.loads((folder/'matches.json').read_text()),key=lambda r:(int(r['time']),str(r['id'])))
    details={}
    for eid in manifest['target_ids']:
        p=folder/(eid+'.json.gz')
        if p.exists():
            with gzip.open(p,'rt') as f:details[eid]=json.load(f)
    rows,coverage=feature_rows(matches,set(manifest['target_ids']),details)
    with gzip.open(folder/'features.json.gz','wt') as f:json.dump(rows,f)
    result={'version':'tm-research-v1.1-quality-repair','holdout_status':'previously_inspected_dataset_after_quality_repair_not_fresh_confirmation','coverage':coverage,'splits':{s:len({r['event_id'] for r in rows if r['split']==s}) for s in ('train','validation','test')},'research':search(rows)}
    (folder/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
