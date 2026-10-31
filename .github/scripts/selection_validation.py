"""Audit existing FT over cohorts. Never optimize or mutate selection rules.

The full current day is disclosed but excluded from the primary screen.
All retrospective inference remains exploratory after prior history inspection.
"""
import collections
import datetime as dt
from decimal import Decimal, ROUND_FLOOR
import gzip
import hashlib
import importlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np
from scipy.stats import t

FAMILIES = ('independent_pockets', 'distinct_pockets', 'seven_direction_pockets',
            'refined_pockets', 'external_pockets', 'strengthened_pockets', 'hypothesis_pockets')
MSK = dt.timezone(dt.timedelta(hours=3))


def exact_profit(entry, outcome):
    """Independent Decimal implementation, including split quarter lines."""
    market = entry.get('market') or {}
    final = outcome.get('final')
    if not outcome.get('settled') or outcome.get('result_conflict'):
        return None
    if not isinstance(final, (list, tuple)) or len(final) != 2:
        return None
    score = str(entry.get('score', '')).replace(':', '-')
    try:
        initial = [int(x) for x in score.split('-')]
        if len(initial) != 2 or any(int(a) < b for a,b in zip(final,initial)):
            return None
        if str(market.get('score','')).replace(':','-') != score:
            return None
        line = Decimal(str(market['line'])); price = Decimal(str(market['price']))
        if not line.is_finite() or not price.is_finite() or price <= 1 or line < 0 or line*4 != (line*4).to_integral_value():
            return None
    except (KeyError, ValueError, ArithmeticError):
        return None
    total = Decimal(sum(int(x) for x in final))
    if line*2 == (line*2).to_integral_value():
        legs = [line]
    else:
        low = (line*2).to_integral_value(rounding=ROUND_FLOOR)/2
        legs = [low,low+Decimal('.5')]
    return float(sum(price-1 if total>x else Decimal(0) if total==x else Decimal(-1)
                     for x in legs)/len(legs))


def _cluster_variance(values, labels):
    n=len(values); groups=collections.defaultdict(float)
    mean=math.fsum(values)/n
    for value,label in zip(values,labels): groups[label]+=value-mean
    g=len(groups)
    if g<2:return None,g
    return g/(g-1)*math.fsum(v*v for v in groups.values())/(n*n),g


def summary(rows):
    closed=[r for r in rows if r['profit'] is not None]
    values=[r['profit'] for r in closed];n=len(values);profit=math.fsum(values)
    days=collections.defaultdict(list);leagues=collections.defaultdict(list)
    for r in closed:
        days[r['day']].append(r['profit']);leagues[r['league']].append(r['profit'])
    result=dict(selected=len(rows),n=n,unresolved=len(rows)-n,unit_profit=profit,
                roi=profit/n if n else None,days=len(days),leagues=len(leagues),
                positive=sum(v>0 for v in values),negative=sum(v<0 for v in values),push=sum(v==0 for v in values))
    result['daily']={k:dict(n=len(v),unit_profit=math.fsum(v),roi=math.fsum(v)/len(v)) for k,v in sorted(days.items())}
    if not n:return result
    result['worst_unresolved_profit_bound']=profit-(len(rows)-n)
    result['without_top3']=math.fsum(sorted(values)[:-3])/(n-3) if n>3 else None
    best_day=max(days,key=lambda k:math.fsum(days[k]));best_league=max(leagues,key=lambda k:math.fsum(leagues[k]))
    def excluding(parts,key):
        count=n-len(parts[key]);return (profit-math.fsum(parts[key]))/count if count else None
    result.update(best_day=best_day,best_league=best_league,
        without_best_day=excluding(days,best_day),without_best_league=excluding(leagues,best_league))
    if n>1:
        var=float(np.var(values,ddof=1));se=math.sqrt(var/n)
        result['iid_ci95']=[profit/n-float(t.ppf(.975,n-1))*se,profit/n+float(t.ppf(.975,n-1))*se]
        vd,gd=_cluster_variance(values,[r['day'] for r in closed])
        vl,gl=_cluster_variance(values,[r['league'] for r in closed])
        vi,gi=_cluster_variance(values,[(r['day'],r['league']) for r in closed])
        if vd is not None and vl is not None:
            variants=[var/n,vd,vl]
            if vi is not None:variants.append(max(0,vd+vl-vi))
            robust_se=math.sqrt(max(variants));df=min(gd,gl)-1
            margin=float(t.ppf(.975,df))*robust_se
            result.update(conservative_cluster_ci95=[profit/n-margin,profit/n+margin],
                          cluster_df=df,conservative_cluster_se=robust_se,
                          p_positive=float(t.sf(profit/n/robust_se,df)) if robust_se else (0. if profit>0 else 1.))
    return result


def holm(pvalues):
    """Step-down FWER adjustment; unknown historical adaptive trials remain a caveat."""
    ordered=sorted(pvalues.items(),key=lambda kv:(kv[1],kv[0]));m=len(ordered);previous=0.;result={}
    for i,(key,p) in enumerate(ordered):
        previous=max(previous,min(1.,(m-i)*p));result[key]=previous
    return result


def screen(rows,latest_day,protocol):
    mature=[r for r in rows if r['day']<latest_day]
    total=summary(rows);stats=summary(mature)
    ordered=sorted({r['day'] for r in mature})
    split=(len(ordered)+1)//2;left=set(ordered[:split]);right=set(ordered[split:])
    halves=[summary([r for r in mature if r['day'] in part]) for part in (left,right)]
    blocks=[]
    for lo,hi in protocol['temporal_blocks']:
        selected=[r for r in mature if (lo=='start' or lo<=r['day']) and r['day']<=hi]
        if selected:blocks.append(dict(start=lo,end=hi,**summary(selected)))
    positive=lambda x:isinstance(x,(int,float)) and math.isfinite(x) and x>0
    checks=dict(positive_mature_roi=positive(stats.get('roi')),positive_total_roi=positive(total.get('roi')),
        positive_without_top3=positive(stats.get('without_top3')),
        positive_without_best_day=positive(stats.get('without_best_day')),
        positive_without_best_league=positive(stats.get('without_best_league')),
        positive_both_halves=all(positive(h.get('roi')) for h in halves),
        positive_every_fixed_block=len(blocks)>=2 and all(positive(b.get('roi')) for b in blocks),
        positive_pending_sensitivity=positive(stats.get('worst_unresolved_profit_bound')))
    return dict(total=total,mature=stats,current_day=summary([r for r in rows if r['day']==latest_day]),
        chronological_halves=halves,fixed_blocks=blocks,checks=checks,descriptive_stability_pass=all(checks.values()))


def make_row(source,outcomes,settle,default_clock=None,default_league=None):
    row=dict(source);eid=str(row.get('event_id',''))
    if not eid:raise ValueError('Missing entry identity')
    stamp=row.get('observed_at_utc') or row.get('entry_observed_at_utc') or default_clock
    if not stamp:raise ValueError('Missing original entry time: '+eid)
    observed=dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))
    if observed.tzinfo is None:raise ValueError('Naive original entry time')
    row['score']=str(row.get('score','')).replace(':','-')
    outcome=outcomes.get(eid,{})
    profit=settle(row,outcome)
    exact=exact_profit(row,outcome)
    if (profit is None)!=(exact is None) or profit is not None and abs(profit-exact)>1e-9:
        raise ValueError('Independent settlement mismatch: '+eid)
    return dict(event_id=eid,observed_at_utc=observed.isoformat(),day=observed.astimezone(MSK).date().isoformat(),
        league=row.get('league') or default_league or 'unknown',profit=profit,
        minute=row.get('minute'),score=row['score'],market=row.get('market'),final=outcome.get('final'),
        source_snapshot_id=row.get('source_snapshot_id'),result_conflict=bool(outcome.get('result_conflict')))


def analyze(root,outcomes,settle,receipts,targets,report,protocol):
    root=Path(root);latest=dt.datetime.fromisoformat(report['generated_at_utc']).astimezone(MSK).date().isoformat()
    groups={};raw_rows={};reconciled=0;duplicates=0;omissions=collections.Counter()
    def add_group(key,name,rows,kind,paused=False,**metadata):
        nonlocal duplicates
        byid={}
        for source in rows:
            eid=str(source.get('event_id',''))
            row=make_row(source,outcomes,settle,metadata.get('clock'),(targets.get(eid) or {}).get('league'))
            if eid in byid:
                duplicates+=1
                # An exact repeated reference is not a second bet.
                if (row['market'],row['score'])!=(byid[eid]['market'],byid[eid]['score']):
                    raise ValueError('Conflicting duplicate original entry: '+key+'/'+eid)
                continue
            byid[eid]=row
        values=list(byid.values());raw_rows[key]=values
        result=screen(values,latest,protocol)
        eligible=kind=='canonical_pocket' and not paused
        status=('research_candidate' if eligible and result['descriptive_stability_pass'] else
                'paused_history' if paused else 'unstable_or_insufficient' if eligible else 'context_only')
        groups[key]=dict(name=name,kind=kind,paused=paused,status=status,metadata=metadata,**result)
    for section in FAMILIES:
        module=importlib.import_module(section);cfg,digest=module.config(root)
        if cfg is None:continue
        history=module.history(root,digest,cfg) if section=='independent_pockets' else module.history(root,cfg,digest)
        known={p['id'] for p in cfg['pockets']}
        for pocket in cfg['pockets']:
            if pocket.get('market','over')!='over':continue
            key=section+'/'+pocket['id'];byid=history[0] if section=='strengthened_pockets' else history[0][pocket['id']]
            add_group(key,pocket['name'],byid.values(),'canonical_pocket',
                      pocket['id'] in report.get('paused_pockets',{}).get(section,[]),catalog_sha256=digest,
                      version=cfg['version'],entry_policy=cfg.get('entry_policy'),pool=cfg.get('pool',pocket.get('pool')))
            expected=report[section]['pockets'][pocket['id']];actual=groups[key]['total']
            if actual['n']!=expected['n'] or abs(actual['unit_profit']-expected['unit_profit'])>1e-8:
                raise ValueError('Canonical reconciliation failed: '+key)
            reconciled+=1
        journal=getattr(module,'JOURNAL','research/live-background/experimental/pocket-receipts')
        archived=collections.defaultdict(list)
        for path in sorted((root/journal).rglob('*.json*')):
            item=module.read(path) if hasattr(module,'read') else module.load(path)
            data=item.get(section) or {}
            if item.get('failed') or data.get('status')!='active':continue
            choices=data.get('choices') or {}
            if not isinstance(choices,dict):continue
            for key,rows in choices.items():
                if key in known or key.startswith('tm_') or key.endswith('_under'):continue
                if any(r.get('side')=='under' for r in rows):continue
                cohort=data.get('catalog_sha256') or data.get('id') or data.get('version')
                archived[(cohort,key)].extend(rows)
        for (cohort,key),rows in archived.items():
            add_group('archive/'+section+'/'+str(cohort)+'/'+key,key,rows,'archived_pocket',catalog_sha256=cohort)
    # All original saved policy choices, separated by statistics epoch. No
    # retrospective regrouping of repeated intra-match points.
    legacy=collections.defaultdict(list)
    for record in sorted(receipts,key=lambda r:(r.get('request') or {}).get('requested_at_utc','')):
        if record.get('failed'):continue
        epoch=(record.get('statistics_epoch') or {}).get('id') or 'legacy_pre_epoch_heterogeneous_versions'
        clock=(record.get('request') or {}).get('requested_at_utc')
        for policy,rows in (record.get('choices') or {}).items():
            for source in rows:
                row=dict(source);row.setdefault('entry_observed_at_utc',clock)
                row.setdefault('source_snapshot_id',record.get('snapshot_id'))
                legacy[(epoch,policy)].append(row)
    for (epoch,policy),rows in legacy.items():
        add_group('policy/'+epoch+'/'+policy,policy,rows,'saved_policy',epoch=epoch,
                  mixed_versions=epoch=='legacy_pre_epoch_heterogeneous_versions')
    import shadow_universe
    shadow=shadow_universe.history(root)[0]
    add_group('baseline/full_board_first_quote','Широкий пул: первая точная котировка',shadow.values(),'baseline')
    for decision in sorted({r.get('decision','unknown') for r in shadow.values()}):
        add_group('baseline/first_quote/'+decision,decision,[r for r in shadow.values() if r.get('decision','unknown')==decision],'baseline')
    # Earlier manual history is a separate historical source, not a backfill
    # into later frozen independent catalogs.
    raw=subprocess.run(['git','show','HEAD:research/full-history-20261018/data.json.gz'],cwd=root,
                       check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
    seed=json.loads(gzip.decompress(raw));historical=collections.defaultdict(list)
    for r in seed['rows']['primary']:
        if r.get('period')!='FT':omissions['historical_non_ft']+=1;continue
        market=dict(line=r.get('line'),price=r.get('odds'),score='-'.join(map(str,r.get('score',[]))))
        final=r.get('final');closed=bool(r.get('closed')) and isinstance(final,list)
        entry=dict(event_id=str(r['id']),score=market['score'],market=market,
                   observed_at_utc=r['time'],minute=r.get('minute'),league=r.get('league'))
        historic_outcome=dict(settled=closed,final=final,result_conflict=False)
        value=exact_profit(entry,historic_outcome)
        if value is not None and r.get('profit') is not None and abs(value-r['profit'])>1e-8:
            raise ValueError('Historical seed settlement mismatch: '+str(r['id']))
        # Keep the original historical outcome; later journals do not own these records.
        row=make_row(entry,{str(r['id']):historic_outcome},exact_profit)
        historical[r.get('version') or 'unknown'].append(row)
    for version,rows in historical.items():
        key='historical_manual/'+version;raw_rows[key]=rows
        groups[key]=dict(name=version,kind='historical_manual',paused=False,status='context_only',
                        metadata=dict(version=version),**screen(rows,latest,protocol))
    pvalues={k:v['mature']['p_positive'] for k,v in groups.items() if 'p_positive' in v['mature']}
    adjusted=holm(pvalues)
    for key,g in groups.items():g['mature']['holm_p_positive']=adjusted.get(key)
    candidates=sorted((k for k,g in groups.items() if g['status']=='research_candidate'),
        key=lambda k:(-(groups[k]['mature'].get('conservative_cluster_ci95') or [-math.inf])[0],-groups[k]['mature']['n'],k))
    # Exact event overlap is disclosed; no combined portfolio return is manufactured.
    overlap=[]
    for i,a in enumerate(candidates):
        for b in candidates[i+1:]:
            aa={r['event_id'] for r in raw_rows[a]};bb={r['event_id'] for r in raw_rows[b]}
            overlap.append(dict(a=a,b=b,shared_events=len(aa&bb),jaccard=len(aa&bb)/len(aa|bb)))
    all_ids={r['event_id'] for rows in raw_rows.values() for r in rows}
    ledger={key:[[r['event_id'],r['observed_at_utc'],r['day'],r['league'],r['profit'],
                  (r.get('market') or {}).get('line'),(r.get('market') or {}).get('price')]
                 for r in rows] for key,rows in raw_rows.items()}
    encoded=json.dumps(ledger,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return dict(version=protocol['version'],generated_at_utc=report['generated_at_utc'],
        protocol=protocol,latest_moscow_day_excluded_from_primary_screen=latest,
        known_historical_distinct_searches_lower_bound=37099,
        source_revision=report['source_revision'],rules_revision=report['rules_revision'],
        quality=dict(canonical_groups_reconciled=reconciled,total_cohorts=len(groups),
            unique_matches_in_evaluated_entries=len(all_ids),unique_observed_lifetime=len(targets),
            unique_confirmed_lifetime=sum(bool(v.get('settled') and not v.get('result_conflict')) for v in outcomes.values()),
            duplicate_original_references_removed=duplicates,omissions=dict(omissions),
            historical_seed=seed['rows']['meta'],
            original_historical_groups_tested=(seed.get('groups') or {}).get('groups_tested'),
            independent_decimal_settlement_checked=True,ledger_sha256=hashlib.sha256(encoded).hexdigest()),
        canonical_research_candidates=candidates,confirmed_for_production=[],candidate_overlap=overlap,
        statuses=dict(collections.Counter(v['status'] for v in groups.values())),
        groups=groups,ledger=ledger,
        limitations=['All previously inspected history is exploratory. No new independent future observations are invented.',
          'Holm adjustment covers evaluated cohorts, not the unknown full adaptive history of searches.',
          'Few day/league clusters cannot establish stationary positive expected returns.',
          'Quotes are exact recorded provider quotes; execution,latency,and user-bet prices are not validated.',
          'No ROI is assigned to unquoted matches. Missing collection intervals cannot be reconstructed.',
          'Current-day partial outcomes are disclosed but excluded from the primary screen.',
          'Archived and paused histories are reviewed but are not reactivated or promoted.'])
