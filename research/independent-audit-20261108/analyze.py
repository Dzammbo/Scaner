"""Reproduce the fixed independent-sample audit from immutable Git inputs."""
import argparse
import collections
import csv
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'research/selection-lab-20261018'), str(ROOT/'research/tm'), str(ROOT/'scripts')]
import tracked_groups
import under_pockets

MSK = dt.timezone(dt.timedelta(hours=3))
DAYS = ['2026-11-07', '2026-11-08']


def read(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == '.gz' else data)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def total(stats, key):
    pair = stats.get(key)
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return None
    values = [number(x) for x in pair]
    return sum(values) if all(x is not None for x in values) else None


def settle(line, price, goals, side):
    legs = [line-.25, line+.25] if round(line*4) % 2 else [line]
    return sum(price-1 if (goals > leg if side == 'ТБ' else goals < leg) else 0 if goals == leg else -1 for leg in legs)/len(legs)


def verify_settlement(line, price, goals, side, value):
    q = int(round(line*4))
    ticks = [q-1,q+1] if q % 2 else [q]
    net = Decimal(0)
    for tick in ticks:
        difference = Decimal(str(goals))*4-tick
        if side == 'ТМ':
            difference = -difference
        net += Decimal(str(price))-1 if difference > 0 else Decimal(-1) if difference < 0 else Decimal(0)
    assert abs(value-float(net/len(ticks))) < 1e-9


def compute_features(event, snapshot):
    quote = event.get('research_primary_quote') or {}
    line, price = number(quote.get('line')), number(quote.get('over'))
    minute = number(event.get('minute'))
    if minute is None or minute <= 0 or line is None or price is None:
        return {}
    features = tracked_groups.features(event, {'line':line,'price':price}, snapshot) or {}
    stats = event.get('board_stats') or {}
    for key, field in [('xg_rate','xg'),('red','redcards')]:
        value = total(stats, field)
        features[key] = value/minute if value is not None and key.endswith('rate') else value
    confidence = event.get('selection_confidence') or {}
    features['strong'] = bool(confidence.get('eligible')) if confidence else None
    features['sufficient'] = confidence.get('data_sufficient')
    return features


def load_rows(inputs):
    manifest = read(inputs/'manifest.json')
    folder = inputs/'research/live-background/experimental'
    outcomes, finals, conflicts = {}, {}, set()
    for path in sorted((folder/'outcomes').rglob('*.gz')):
        for result in read(path).get('events', []):
            event_id = str(result['event_id'])
            if result.get('result_conflict'):
                conflicts.add(event_id)
            if result.get('settled') and result.get('final') is not None:
                final = tuple(result['final'])
                if event_id in finals and finals[event_id] != final:
                    conflicts.add(event_id)
                finals[event_id] = final
            if result.get('checked_at_utc','') >= outcomes.get(event_id,{}).get('checked_at_utc',''):
                outcomes[event_id] = result
    records, diagnostics, seen = [], collections.Counter(), set()
    for path in sorted((folder/'research-foundation-receipts').rglob('*.gz')):
        receipt = read(path)
        foundation = receipt['research_foundation']
        if foundation.get('status') != 'active':
            diagnostics['failed_captures'] += 1
            continue
        diagnostics['successful_captures'] += 1
        snapshot = read(folder/'snapshots'/path.parent.name/path.name)
        events = {str(event['event_id']):event for event in snapshot['events']}
        for observation in foundation['observations']:
            event_id = str(observation['event_id'])
            key = (snapshot['snapshot_id'],event_id)
            assert key not in seen
            seen.add(key)
            event = events[event_id]
            sampling = observation.get('sampling') or {}
            checks = observation['checks']
            quote = observation.get('primary_quote') or {}
            time = observation['observed_at_utc']
            score = tuple(int(x) for x in observation['score'].split('-')) if re.fullmatch(r'\d+-\d+', str(observation.get('score'))) else None
            features = compute_features(event,snapshot) if score is not None else {}
            outcome = outcomes.get(event_id,{})
            final = outcome.get('final') if outcome.get('settled') and event_id not in conflicts else None
            if final is not None and (score is None or any(a < b for a,b in zip(final,score))):
                final = None
            valid = all(checks.get(k) for k in ('domain','score_consistent','board_fresh','primary_quote'))
            valid = valid and quote.get('period') == 'full_time' and quote.get('settlement_basis') == 'full_match_total'
            valid = bool(valid and features.get('gap',0)>0)
            day = dt.datetime.fromisoformat(time).astimezone(MSK).date().isoformat()
            row = dict(event_id=event_id, snapshot_id=snapshot['snapshot_id'], time=time, day=day,
                minute=event.get('minute'), score=score, league=event.get('league'), home=event.get('home'),away=event.get('away'),
                independent=bool(sampling.get('independent_research_selected')), arm=sampling.get('arm'),
                inclusion_probability=sampling.get('research_inclusion_probability'),
                features=features, event=event, quote=quote, valid_quote=valid,
                pause=bool(checks.get('post_goal_pause')), final=final,
                trend_available=bool((event.get('stats_trend') or {}).get('available')),
                collection_revision=snapshot.get('code_revision'))
            if row['independent']:
                assert 0 < row['inclusion_probability'] <= 1
            for side, field in [('ТБ','over'),('ТМ','under')]:
                price = number(quote.get(field))
                row[side] = None
                row[side+'_worse'] = None
                if final is not None and valid:
                    row[side] = settle(quote['line'],price,sum(final),side)
                    row[side+'_worse'] = settle(quote['line'],max(1.001,price-.05),sum(final),side)
                    verify_settlement(quote['line'],price,sum(final),side,row[side])
            records.append(row)
    records.sort(key=lambda row:(row['time'], row['snapshot_id'], row['event_id']))
    return records, manifest, dict(diagnostics), len(conflicts)


def first(rows):
    selected = {}
    for row in rows:
        selected.setdefault(row['event_id'],row)
    return list(selected.values())


def summarize(rows, side):
    closed = [row for row in rows if row[side] is not None]
    values = [row[side] for row in closed]
    n = len(values)
    profit = math.fsum(values)
    leagues = collections.defaultdict(list)
    daily = []
    for row in closed:
        leagues[row['league']].append(row[side])
    best_league = max(leagues,key=lambda key:sum(leagues[key])) if leagues else None
    excluded = leagues.get(best_league,[])
    for day in DAYS:
        day_rows = [row for row in closed if row['day']==day]
        daily.append(dict(day=day,n=len(day_rows),profit=math.fsum(row[side] for row in day_rows),
            roi=math.fsum(row[side] for row in day_rows)/len(day_rows) if day_rows else None,
            pending=sum(row['day']==day and row[side] is None for row in rows)))
    curve=np.r_[0,np.cumsum(values)]
    drawdown=float(np.max(np.maximum.accumulate(curve)-curve)) if len(curve) else 0
    return dict(selected=len(rows),n=n,pending=len(rows)-n,profit=profit,roi=profit/n if n else None,
        worse_roi=math.fsum(row[side+'_worse'] for row in closed)/n if n else None,
        without_three=math.fsum(sorted(values)[:-3])/(n-3) if n>3 else None,
        best_league=best_league,without_best_league=(profit-sum(excluded))/(n-len(excluded)) if n>len(excluded) else None,
        max_drawdown=drawdown,daily=daily,standard_error=float(np.std(values,ddof=1)/math.sqrt(n)) if n>1 else None)


def simple_rules():
    rules=[('Первый пригодный вход',[],lambda f:True)]
    def add(name,fields,predicate):rules.append((name,fields,predicate))
    for lo,hi in [(5,29),(30,59),(60,89)]:
        add(f'{lo}–{hi} минута',['minute'],lambda f,lo=lo,hi=hi:lo<=f['minute']<=hi)
    add('Нулевой счёт',['goals'],lambda f:f['goals']==0)
    add('Уже есть голы',['goals'],lambda f:f['goals']>0)
    for lo,hi in [(0,.75),(.75,1.5),(1.5,10)]:
        add(f'Запас линии больше {lo} и до {hi}',['gap'],lambda f,lo=lo,hi=hi:lo<f['gap']<=hi)
    add('Коэффициент от 1,60 до 2,20',['price'],lambda f:1.6<=f['price']<=2.2)
    add('Коэффициент от двух',['price'],lambda f:f['price']>=2)
    add('Редкие створы',['sot_rate'],lambda f:f['sot_rate']<.08)
    for threshold in [.08,.10,.12]:
        add(f'Створы от {threshold:.2f} в минуту',['sot_rate'],lambda f,t=threshold:f['sot_rate']>=t)
    for threshold in [.3,.4,.5]:
        add(f'Удары от {threshold:.1f} в минуту',['shots_rate'],lambda f,t=threshold:f['shots_rate']>=t)
    add('Низкий темп опасных атак',['danger_rate'],lambda f:f['danger_rate']<.6)
    add('Высокий темп опасных атак',['danger_rate'],lambda f:f['danger_rate']>=1.5)
    add('Без удалений',['red'],lambda f:f['red']==0)
    add('С удалением',['red'],lambda f:f['red']>=1)
    add('Низкий темп xG',['xg_rate'],lambda f:f['xg_rate']<.02)
    add('Высокий темп xG',['xg_rate'],lambda f:f['xg_rate']>=.03)
    add('Створ за полные последние пять минут',['fresh_sot_5'],lambda f:f['fresh_sot_5']>=1)
    add('После перерыва и частые створы',['minute','sot_rate'],lambda f:f['minute']>=46 and f['sot_rate']>=.1)
    add('После часа и редкие створы',['minute','sot_rate'],lambda f:f['minute']>=60 and f['sot_rate']<.08)
    return rules


def current_rules():
    names=read(ROOT/'.github/live-tracked-sot.json')['names']
    over=read(ROOT/'.github/live-tracked-groups.json')['pockets']
    under=read(ROOT/'.github/live-under-pockets.json')['pockets']
    result=[]
    for rule in over:
        fields=[a['field'] for a in rule['rule']]
        if rule['pool']=='strong':fields.append('strong')
        result.append(('ТБ',names[rule['id']],fields,
            lambda row,rule=rule: (rule['pool']!='strong' or row['features'].get('strong') is True)
            and rule['minute_window'][0]<=row['minute']<=rule['minute_window'][1]
            and tracked_groups.matches(rule['rule'],row['features'])))
    required=[['gap'],['sot_rate','danger_rate'],['goals','red'],['sot_rate','xg_rate'],['sot_rate','red'],
              ['minute'],['goals','gap'],['danger_rate'],['red','gap'],['minute','danger_rate']]
    for rule,fields in zip(under,required):
        result.append(('ТМ',rule['name'],fields,lambda row,key=rule['id']:under_pockets.match(key,row['event'],row['quote']['line'],row['quote']['under'])))
    return result


def eligible_rule(row,fields):
    return all(row['features'].get(field) is not None for field in fields)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('inputs',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    rows,manifest,diagnostics,conflicts=load_rows(args.inputs)
    independent=[row for row in rows if row['independent']]
    quoted=[row for row in independent if row['valid_quote']]
    clean=[row for row in quoted if row['pause']]
    coverage=[]
    for day in DAYS:
        candidates=[r for r in independent if r['day']==day]
        priced=[r for r in candidates if r['valid_quote']]
        safe=[r for r in priced if r['pause']]
        coverage.append(dict(day=day,points=len(candidates),matches=len({r['event_id'] for r in candidates}),
            priced=len(priced),safe_points=len(safe),safe_matches=len({r['event_id'] for r in safe}),
            settled_safe_matches=len({r['event_id'] for r in safe if r['final'] is not None}),
            sot=sum(r['features'].get('sot_rate') is not None for r in candidates),
            xg=sum(r['features'].get('xg_rate') is not None for r in candidates),
            trend=sum(r['trend_available'] for r in candidates),
            full_five_minute=sum(r['features'].get('fresh_sot_5') is not None for r in candidates)))
    report=dict(manifest={k:v for k,v in manifest.items() if k!='objects'},diagnostics=diagnostics,
        all_points=len(rows),all_matches=len({r['event_id'] for r in rows}),
        independent_points=len(independent),independent_matches=len({r['event_id'] for r in independent}),
        quoted_points=len(quoted),clean_points=len(clean),clean_matches=len({r['event_id'] for r in clean}),
        conflicts=conflicts,coverage=coverage,baseline={},gates=[],current=[],search=[],validation=[])
    ledger=[]
    for side in ['ТБ','ТМ']:
        report['baseline'][side]=summarize(first(clean),side)
        report['baseline'][side]['without_pause_check']=summarize(first(quoted),side)
        for field,name,predicate in [('sufficient','Достаточно спортивных данных',lambda v:v is True),
            ('strong','Прежний сильный спортивный допуск',lambda v:v is True),
            ('sot_rate','Створы от 0,10 в минуту',lambda v:v>=.1)]:
            common=first(r for r in clean if r['features'].get(field) is not None)
            passed=[r for r in common if predicate(r['features'][field])]
            rejected=[r for r in common if not predicate(r['features'][field])]
            report['gates'].append(dict(side=side,name=name,accepted=summarize(passed,side),rejected=summarize(rejected,side)))
    for side,name,fields,predicate in current_rules():
        available=[r for r in clean if eligible_rule(r,fields)]
        candidates=[r for r in available if predicate(r)]
        selected=first(candidates)
        common=first(available)
        passed=[r for r in common if predicate(r)]
        rejected=[r for r in common if not predicate(r)]
        report['current'].append(dict(side=side,name=name,available_points=len(available),candidate_points=len(candidates),
            **summarize(selected,side),first_state_accepted=summarize(passed,side),first_state_rejected=summarize(rejected,side)))
        for r in selected:ledger.append(dict(rule=name,kind='Действующее правило',side=side,**{k:r[k] for k in ['event_id','snapshot_id','time','day','league','minute','score','final']},line=r['quote']['line'],price=r['quote']['over' if side=='ТБ' else 'under'],profit=r[side]))
    match_day={r['event_id']:r['day'] for r in reversed(independent)}
    folds={}
    for day in DAYS:
        ids=sorted([key for key,value in match_day.items() if value==day],key=lambda value:hashlib.sha256(value.encode()).hexdigest())
        folds.update({key:index%3 for index,key in enumerate(ids)})
    for side in ['ТБ','ТМ']:
        strategies=[]
        for name,fields,predicate in simple_rules():
            candidates=[]
            for row in clean:
                features=dict(row['features'],price=row['quote']['over' if side=='ТБ' else 'under'])
                if all(features.get(field) is not None for field in fields) and predicate(features):
                    candidates.append(row)
            selected=first(candidates)
            summary=summarize(selected,side)
            summary.update(side=side,name=name,candidate_points=len(candidates))
            summary['historical_candidate']=bool(summary['n']>=80 and all(d['n']>=20 and d['profit']>0 for d in summary['daily'])
                and summary['without_three']>0 and summary['worse_roi']>0 and summary['without_best_league'] is not None and summary['without_best_league']>0)
            report['search'].append(summary)
            strategies.append((name,selected))
            for row in selected:ledger.append(dict(rule=name,kind='Простое правило',side=side,**{k:row[k] for k in ['event_id','snapshot_id','time','day','league','minute','score','final']},line=row['quote']['line'],price=row['quote']['over' if side=='ТБ' else 'under'],profit=row[side]))
        evaluated=[]
        for fold in range(3):
            ranked=[]
            for name,selected in strategies:
                train=[r for r in selected if folds[r['event_id']]!=fold]
                metrics=summarize(train,side)
                if metrics['n']>=40 and all(d['n']>=10 and d['profit']>0 for d in metrics['daily']) and metrics['roi']>metrics['standard_error']:
                    ranked.append((metrics['roi']-metrics['standard_error'],name,selected,metrics))
            if ranked:
                winner=max(ranked,key=lambda row:(row[0],row[1]))
                heldout=[r for r in winner[2] if folds[r['event_id']]==fold]
                evaluated.extend(heldout)
                report['validation'].append(dict(side=side,fold=fold,chosen=winner[1],train=winner[3],test=summarize(heldout,side)))
            else:report['validation'].append(dict(side=side,fold=fold,chosen=None,test=summarize([],side)))
        report.setdefault('validation_total',{})[side]=summarize(evaluated,side)
    report['hypotheses_tested']=len(report['search'])
    report['fixed_rules_tested']=len(report['current'])
    report['historical_candidates']=[dict(side=r['side'],name=r['name']) for r in report['search'] if r['historical_candidate']]
    (args.output/'Результаты.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    with gzip.open(args.output/'Реестр входов.json.gz','wt',encoding='utf-8') as stream:json.dump(ledger,stream,ensure_ascii=False)
    compact=[{k:v for k,v in r.items() if k not in ('event',)} for r in rows]
    with gzip.open(args.output/'Наблюдения.json.gz','wt',encoding='utf-8') as stream:json.dump(compact,stream,ensure_ascii=False)
    (args.output/'Источники.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({k:report[k] for k in ['all_points','independent_points','independent_matches','clean_points','clean_matches','coverage','baseline','gates','historical_candidates','validation_total']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
