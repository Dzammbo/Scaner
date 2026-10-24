"""Prospective paired timing and comparable strong-v8 gate diagnostics.

Only original as-of quotes and confirmed outcomes are used. No admission changes.
"""
import collections
import datetime as dt
import math
import random
import statistics
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


def point_history(root, shadow):
    points=collections.defaultdict(list)
    for path in sorted((Path(root)/shadow.JOURNAL).rglob('*.json*')):
        data=(shadow.load(path).get('shadow_universe') or {})
        if data.get('version') != shadow.VERSION or data.get('status') != 'active':continue
        for point in data.get('quoted_points',[]):points[str(point['event_id'])].append(point)
    for items in points.values():items.sort(key=lambda p:(p['observed_at_utc'],p.get('source_snapshot_id','')))
    return points


def first_decidable(rows, points):
    result={};later_than_first=0
    msk=dt.timezone(dt.timedelta(hours=3))
    for eid,first in rows.items():
        choice=next((p for p in points[eid] if p.get('decision') in ('strong_v8','rejected_strong_v8')),None)
        if choice is None:continue
        entry=dict(first,score=choice['score'],minute=choice['minute'],market=choice['market'],
                   observed_at_utc=choice['observed_at_utc'],source_snapshot_id=choice.get('source_snapshot_id'),
                   decision=choice['decision'],selection_confidence=choice.get('selection_confidence') or {},
                   entry_day_msk=instant(choice['observed_at_utc']).astimezone(msk).date().isoformat())
        later_than_first+=choice['observed_at_utc']!=first['observed_at_utc']
        result[eid]=entry
    return result,later_than_first


def timed_pairs(rows, points, outcomes, settle):
    pairs=[];eligible_with_later=0
    for eid, first in rows.items():
        t0=instant(first['observed_at_utc']);m0=first['minute']
        later=next((p for p in points[eid]
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


def coverage_funnel(root, shadow, rows, points, decidable, outcomes, settle):
    """Count distinct as-of events, never repeated snapshots as new matches."""
    first_board = {}
    for path in sorted((Path(root)/shadow.JOURNAL).rglob('*.json*')):
        item = shadow.load(path).get('shadow_universe') or {}
        if item.get('version') != shadow.VERSION or item.get('status') != 'active':
            continue
        source = (Path(root)/'research/live-background/experimental/snapshots'
                  /path.parent.name/path.name)
        if not source.exists():
            raise FileNotFoundError('Missing paired board snapshot: ' + str(source))
        snapshot = shadow.load(source)
        if snapshot['snapshot_id'] != path.name.split('.json')[0]:
            raise ValueError('Shadow and board snapshot IDs differ')
        for event in snapshot.get('events', []):
            minute = event.get('minute')
            score = str(event.get('score', ''))
            if not isinstance(minute, (int, float)) or not 5 <= minute <= 89:
                continue
            if score.count('-') != 1 or not all(part.isdigit() for part in score.split('-')):
                continue
            eid = str(event['event_id'])
            if eid not in first_board:
                coverage = event.get('research_detail_coverage') or {}
                first_board[eid] = dict(at=snapshot['board_observed_at_utc'],
                    minute=minute, league=event.get('league'), country=event.get('country'),
                    priced=bool(coverage.get('exact_main_price')),
                    deep=bool(event.get('research_enriched')),
                    missing=coverage.get('missing_reasons') or [])
    if not set(rows) <= set(first_board):
        raise ValueError('First quote without eligible observed board row')
    durations_to_quote = []
    durations_to_decision = []
    delayed_decisions = []
    eventual_missing = collections.Counter()
    first_price_missing = collections.Counter()
    minute_bands = collections.defaultdict(lambda: collections.Counter())
    for eid, board in first_board.items():
        band = ('05-30' if board['minute'] <= 30 else '31-60' if board['minute'] <= 60 else '61-89')
        minute_bands[band]['observed'] += 1
        if eid in rows:
            minute_bands[band]['quoted'] += 1
            durations_to_quote.append((instant(rows[eid]['observed_at_utc'])-instant(board['at'])).total_seconds()/60)
        else:
            eventual_missing.update(r for r in board['missing'] if r.startswith('price_'))
            if not any(r.startswith('price_') for r in board['missing']):
                eventual_missing['price_reason_unknown'] += 1
        if not board['priced']:
            first_price_missing.update(r for r in board['missing'] if r.startswith('price_'))
        if eid in decidable:minute_bands[band]['decidable'] += 1
    for eid, decision in decidable.items():
        minutes=(instant(decision['observed_at_utc'])
                 -instant(rows[eid]['observed_at_utc'])).total_seconds()/60
        durations_to_decision.append(minutes)
        if minutes>0:delayed_decisions.append(minutes)
    first_insufficient = {eid for eid, row in rows.items()
                          if row['decision'] == 'insufficient_sports_data'}
    last_unresolved = collections.Counter()
    acquisition = collections.Counter()
    acquisition_recovered = collections.Counter()
    for eid in first_insufficient:
        reasons=rows[eid].get('deep_missing_reasons') or []
        if any('budget' in r.lower() for r in reasons):category='budget_exhausted'
        elif any(('timeout' in r.lower() or 'http' in r.lower() or 'request_error' in r.lower()) for r in reasons):category='transient_api_error'
        elif reasons:category='other_acquisition_gap'
        else:category='no_explicit_acquisition_error'
        acquisition[category]+=1
        if eid in decidable:acquisition_recovered[category]+=1
    for eid in first_insufficient - decidable.keys():
        last = points.get(eid, [])[-1] if points.get(eid) else rows[eid]
        last_unresolved.update((last.get('selection_confidence') or {}).get('data_gaps') or [])
    def delay(values):
        return dict(n=len(values),median_minutes=statistics.median(values) if values else None,
                    p90_minutes=sorted(values)[math.ceil(.9*len(values))-1] if values else None)
    def settled(entries):
        return sum(settle(row, outcomes.get(eid, {})) is not None
                   for eid, row in entries.items())
    return dict(unique_eligible_board=len(first_board),
                first_board_priced=sum(b['priced'] for b in first_board.values()),
                ever_first_quoted=len(rows),
                first_quote_deep_evaluated=sum(r.get('deep_evaluated', False) for r in rows.values()),
                first_quote_sports_decidable=sum(r['decision'] in ('strong_v8', 'rejected_strong_v8') for r in rows.values()),
                eventually_sports_decidable=len(decidable),
                eligible_board_confirmed=sum(bool((outcomes.get(eid) or {}).get('settled')
                    and not (outcomes.get(eid) or {}).get('result_conflict')) for eid in first_board),
                quoted_settleable=settled(rows),
                decidable_settleable=settled(decidable),
                first_quote_insufficient=len(first_insufficient),
                insufficient_later_decidable=len(first_insufficient & decidable.keys()),
                first_quote_to_decision=delay(durations_to_decision),
                first_quote_to_later_decision=delay(delayed_decisions),
                first_board_to_first_quote=delay(durations_to_quote),
                first_quote_insufficient_acquisition_categories=dict(acquisition),
                acquisition_later_decidable=dict(acquisition_recovered),
                first_board_unpriced_reasons=dict(first_price_missing),
                never_quoted_first_board_reasons=dict(eventual_missing),
                persistent_insufficiency_last_observed_gaps=dict(last_unresolved),
                by_first_seen_minute={k:dict(v) for k,v in sorted(minute_bands.items())},
                note='Unique matches, not repeated observations; missing reasons overlap. Provider unavailable on an observed point does not prove permanent absence; snapshots only cover successful captures.')


def analyze(root, outcomes, settle):
    import shadow_universe as shadow
    rows,started,errors,captures=shadow.history(root)
    points=point_history(root,shadow)
    decidable,later_decision=first_decidable(rows,points)
    return dict(version=VERSION,status='active' if started else 'awaiting_first_capture',
                started_at_utc=started,successful_captures=captures,capture_errors=errors,
                coverage_funnel=coverage_funnel(root,shadow,rows,points,decidable,outcomes,settle),
                timing=timed_pairs(rows,points,outcomes,settle),
                gate=dict(rule='One first prospectively decidable exact-priced point per event; same Moscow day, exact score and total line; minute ±5; price ±0.05; distinct matched events without replacement.',
                          first_quote_flow=admission_flow(rows,outcomes,settle),
                          first_decidable_flow=admission_flow(decidable,outcomes,settle),
                          first_decidable_matches=len(decidable),
                          decision_reached_after_first_quote=later_decision,
                          decision_entry_rule='First prospective exact-priced strong or rejected point with sufficient sporting data; never backfill first quote or select by result.',
                          same_league=gate_pairs(decidable,outcomes,settle,'league'),
                          same_country=gate_pairs(decidable,outcomes,settle,'country'),
                          all_leagues_diagnostic_only=gate_pairs(decidable,outcomes,settle,'all'),
                          same_league_week_diagnostic=gate_pairs(decidable,outcomes,settle,'league',True),
                          same_country_week_diagnostic=gate_pairs(decidable,outcomes,settle,'country',True),
                          all_leagues_week_diagnostic=gate_pairs(decidable,outcomes,settle,'all',True),
                          relaxed_rule='Separate diagnostic only: date ±7 days, exact score, total line ±0.5, minute ±10, price ±0.10; same league or country where specified.',
                          data_insufficient_not_called_rejected=True),
                no_rule_changes=True,exploratory_until_minimum_pairs_and_days=True)
