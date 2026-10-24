"""Prospective paired timing and comparable strong-v8 gate diagnostics.

Only original as-of quotes and confirmed outcomes are used. No admission changes.
"""
import collections
import datetime as dt
import math
import random
from pathlib import Path

VERSION = 'shadow-matched-analysis-v1-20261024'
MIN_PAIRS = 100
MIN_DAYS = 7


def instant(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def summarized_pairs(pairs):
    days = {p['day'] for p in pairs}
    n = len(pairs)
    left = math.fsum(p['left'] for p in pairs)
    right = math.fsum(p['right'] for p in pairs)
    result = dict(n=n, days=len(days), first_or_strong_roi=left/n if n else None,
                  later_or_rejected_roi=right/n if n else None,
                  difference_right_minus_left=(right-left)/n if n else None,
                  evidence_signal=False, minimum_pairs=MIN_PAIRS, minimum_days=MIN_DAYS)
    if n >= MIN_PAIRS and len(days) >= MIN_DAYS:
        byday=collections.defaultdict(list)
        for pair in pairs:byday[pair['day']].append(pair)
        keys=sorted(byday);rng=random.Random(20261024);values=[]
        for _ in range(2000):
            sampled=[p for key in rng.choices(keys,k=len(keys)) for p in byday[key]]
            values.append(math.fsum(p['right']-p['left'] for p in sampled)/len(sampled))
        values.sort()
        result['day_cluster_95pct_interval']=[values[49],values[1949]]
        result['evidence_signal']=values[49]>0 or values[1949]<0
    return result


def timed_pairs(root, rows, outcomes, settle, shadow):
    points=collections.defaultdict(list)
    for path in sorted((Path(root)/shadow.JOURNAL).rglob('*.json*')):
        data=(shadow.load(path).get('shadow_universe') or {})
        if data.get('version') != shadow.VERSION or data.get('status') != 'active':continue
        for point in data.get('quoted_points',[]):points[str(point['event_id'])].append(point)
    pairs=[];eligible_with_later=0
    for eid, first in rows.items():
        t0=instant(first['observed_at_utc']);m0=first['minute']
        later=next((p for p in sorted(points[eid],key=lambda p:p['observed_at_utc'])
                    if instant(p['observed_at_utc'])>=t0+dt.timedelta(minutes=5)
                    and p['minute']>=m0+3),None)
        if later is None:continue
        eligible_with_later+=1
        outcome=outcomes.get(eid,{})
        left=settle(first,outcome);right=settle(later,outcome)
        if left is None or right is None:continue
        pairs.append(dict(event_id=eid,day=first['entry_day_msk'],left=left,right=right,
                          decision=first['decision'],first_minute=m0,later_minute=later['minute']))
    by_decision={key:summarized_pairs([p for p in pairs if p['decision']==key])
                 for key in ('strong_v8','rejected_strong_v8','insufficient_sports_data','not_deep_evaluated')}
    return dict(rule='First priced point vs first priced point at least 5 real minutes and 3 match minutes later; same match and confirmed final score.',
                first_priced_with_later_point=eligible_with_later,
                settled_pairs=summarized_pairs(pairs),by_first_decision=by_decision,
                note='One pair per event; later point fixed by clock, never chosen by its result.')


def gate_pairs(rows, outcomes, settle, key, relaxed=False):
    strong=[r for r in rows.values() if r['decision']=='strong_v8']
    rejected=[r for r in rows.values() if r['decision']=='rejected_strong_v8']
    strong.sort(key=lambda r:(r['entry_day_msk'],r['observed_at_utc'],str(r['event_id'])))
    available={str(r['event_id']):r for r in rejected}
    pairs=[]
    for a in strong:
        left=settle(a,outcomes.get(str(a['event_id']),{}))
        if left is None:continue
        choices=[]
        for eid,b in available.items():
            day_gap=abs((dt.date.fromisoformat(b['entry_day_msk'])-dt.date.fromisoformat(a['entry_day_msk'])).days)
            line_gap=abs(float(b['market']['line'])-float(a['market']['line']))
            price_gap=abs(float(b['market']['price'])-float(a['market']['price']))
            minute_gap=abs(float(b['minute'])-float(a['minute']))
            if day_gap>(7 if relaxed else 0) or b['score']!=a['score']:continue
            if line_gap>(.5 if relaxed else 1e-8):continue
            if price_gap>(.10 if relaxed else .05)+1e-9:continue
            if minute_gap>(10 if relaxed else 5):continue
            if key=='league' and (not a.get('league') or b.get('league')!=a['league']):continue
            if key=='country' and (not a.get('country') or b.get('country')!=a['country']):continue
            right=settle(b,outcomes.get(eid,{}))
            if right is None:continue
            distance=(minute_gap/(10 if relaxed else 5)+line_gap/(.5 if relaxed else 1)
                      +price_gap/(.10 if relaxed else .05)+day_gap/7,
                      minute_gap,line_gap,price_gap,eid)
            choices.append((distance,eid,right))
        if choices:
            _,eid,right=min(choices)
            del available[eid]
            pairs.append(dict(day=a['entry_day_msk'],left=left,right=right,strong_id=str(a['event_id']),rejected_id=eid))
    return summarized_pairs(pairs)


def admission_flow(rows, outcomes, settle):
    groups=collections.Counter();closed=collections.Counter();data_gaps=collections.Counter()
    sporting_gaps=collections.Counter();acquisition_gaps=collections.Counter()
    for row in rows.values():
        group=row['decision'];groups[group]+=1
        if settle(row,outcomes.get(str(row['event_id']),{})) is not None:closed[group]+=1
        confidence=row.get('selection_confidence') or {}
        if group=='insufficient_sports_data':
            data_gaps.update(confidence.get('data_gaps') or [])
            acquisition_gaps.update(row.get('deep_missing_reasons') or [])
        if group=='rejected_strong_v8':sporting_gaps.update(confidence.get('sporting_gaps') or [])
    return dict(first_quoted_by_decision=dict(groups),settled_by_decision=dict(closed),
                insufficient_data_gaps=dict(data_gaps),
                insufficient_acquisition_gaps=dict(acquisition_gaps),
                rejected_sporting_gaps=dict(sporting_gaps),
                note='A match may have multiple gaps; gap counts are not disjoint match counts.')


def analyze(root, outcomes, settle):
    import shadow_universe as shadow
    rows,started,errors,captures=shadow.history(root)
    return dict(version=VERSION,status='active' if started else 'awaiting_first_capture',
                started_at_utc=started,successful_captures=captures,capture_errors=errors,
                timing=timed_pairs(root,rows,outcomes,settle,shadow),
                gate=dict(rule='One original first-priced entry per event; same Moscow day, exact score and total line; minute ±5; price ±0.05; distinct matched events without replacement.',
                          admission_flow=admission_flow(rows,outcomes,settle),
                          same_league=gate_pairs(rows,outcomes,settle,'league'),
                          same_country=gate_pairs(rows,outcomes,settle,'country'),
                          all_leagues_diagnostic_only=gate_pairs(rows,outcomes,settle,'all'),
                          same_league_week_diagnostic=gate_pairs(rows,outcomes,settle,'league',True),
                          same_country_week_diagnostic=gate_pairs(rows,outcomes,settle,'country',True),
                          all_leagues_week_diagnostic=gate_pairs(rows,outcomes,settle,'all',True),
                          relaxed_rule='Separate diagnostic only: date ±7 days, exact score, total line ±0.5, minute ±10, price ±0.10; same league or country where specified.',
                          data_insufficient_not_called_rejected=True),
                no_rule_changes=True,exploratory_until_minimum_pairs_and_days=True)
