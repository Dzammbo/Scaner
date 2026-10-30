"""Reconcile original pocket entries by Moscow entry day, with confirmed outcomes."""
import collections
import datetime as dt
import importlib
import math

FAMILIES = ('independent_pockets', 'distinct_pockets', 'seven_direction_pockets',
            'refined_pockets', 'external_pockets', 'strengthened_pockets', 'hypothesis_pockets')
KEYS = {'independent_pockets/need_four_plus',
        'distinct_pockets/tb_45_59_lead2_exact2',
        'refined_pockets/score1_bothsot',
        'independent_pockets/provider_main_danger_rate_lt1'}
MSK = dt.timezone(dt.timedelta(hours=3))


def day_of(row):
    stamp = row.get('observed_at_utc') or row.get('entry_observed_at_utc')
    if stamp:
        return dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(MSK).date().isoformat()
    return row['entry_day_msk']


def summarize(rows, outcomes, settle):
    values = []; minutes = []; prices = []; lines = []; leagues = collections.Counter()
    for row in rows:
        value = settle(row, outcomes.get(str(row['event_id']), {}))
        if value is not None:
            values.append(value)
        if isinstance(row.get('minute'), (int, float)): minutes.append(row['minute'])
        market = row.get('market') or {}
        if isinstance(market.get('price'), (int, float)): prices.append(market['price'])
        if isinstance(market.get('line'), (int, float)): lines.append(market['line'])
        leagues[row.get('league') or 'unknown'] += 1
    n = len(values); total = math.fsum(values)
    return dict(selected=len(rows), n=n, unresolved=len(rows)-n, unit_profit=total,
                roi=total/n if n else None, positive=sum(v>0 for v in values),
                negative=sum(v<0 for v in values), push=sum(v==0 for v in values),
                roi_without_top3=math.fsum(sorted(values)[:-3])/(n-3) if n>30 else None,
                mean_minute=math.fsum(minutes)/len(minutes) if minutes else None,
                mean_price=math.fsum(prices)/len(prices) if prices else None,
                mean_line=math.fsum(lines)/len(lines) if lines else None,
                leagues=dict(leagues), profits=values)


def acquisition_days(receipts):
    days = collections.defaultdict(lambda: dict(captures=0,failed=0,coverage=collections.Counter(),
                                                revisions=collections.Counter(),missing=collections.Counter()))
    for receipt in receipts:
        stamp=(receipt.get('request') or {}).get('requested_at_utc')
        if not stamp: continue
        day=dt.datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(MSK).date().isoformat()
        bucket=days[day];bucket['captures']+=1;bucket['failed']+=bool(receipt.get('failed'))
        bucket['revisions'][receipt.get('code_revision') or 'unknown']+=1
        coverage=receipt.get('research_detail_coverage') or {}
        for key in ('eligible','selected','trend_available','profile_available','priced','complete'):
            value=coverage.get(key)
            if isinstance(value,(int,float)):bucket['coverage'][key]+=value
        bucket['missing'].update(coverage.get('missing_reasons') or {})
    return {day:{key:dict(value) if isinstance(value,collections.Counter) else value
                 for key,value in bucket.items()} for day,bucket in sorted(days.items())}


def audit(root, outcomes, settle, receipts=()):
    result = {}
    for section in FAMILIES:
        module = importlib.import_module(section)
        cfg, digest = module.config(root)
        if cfg is None: continue
        history = (module.history(root, digest, cfg) if section == 'independent_pockets'
                   else module.history(root, cfg, digest))
        for pocket in cfg['pockets']:
            if pocket.get('market', 'over') != 'over': continue
            key = section + '/' + pocket['id']; rows = history[0][pocket['id']]
            by_day = collections.defaultdict(list)
            for row in rows.values(): by_day[day_of(row)].append(row)
            result[key] = dict(name=pocket['name'], catalog_sha256=digest,
                total=summarize(list(rows.values()), outcomes, settle),
                days={day:summarize(items, outcomes, settle) for day,items in sorted(by_day.items())})
            if key in KEYS:
                result[key]['entries'] = [dict(event_id=eid, day=day_of(row),
                    minute=row.get('minute'), score=row.get('score'), market=row.get('market'),
                    league=row.get('league'), source_snapshot_id=row.get('source_snapshot_id'),
                    profit=settle(row,outcomes.get(eid,{})), features=row.get('features'))
                    for eid,row in rows.items()]
    return dict(version='pocket-entry-days-v1', groups=result, acquisition=acquisition_days(receipts),
                definition='Original first entry per pocket/event; Moscow entry day; flat one unit; confirmed exact original quote; overlapping groups are not additive')
