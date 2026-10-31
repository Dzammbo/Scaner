"""Reproduce selection diagnosis from a saved report with daily_audit.

Usage: python analyze.py REPORT.json DAILY_FREEZE.json OUTPUT.json
For the local connector extraction only, REPORT may be a directory containing
groups*.json, entries*.json, and metadata.json. No network or journal mutation.
"""
import collections
import datetime as dt
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.stats import t, ttest_ind


def summary(values):
    a = np.asarray([x for x in values if x is not None], dtype=float)
    n = len(a)
    if not n:
        return dict(n=0, profit=0, roi=None)
    profit = math.fsum(a)
    result = dict(n=n, profit=profit, roi=profit/n)
    if n > 1:
        sd = float(a.std(ddof=1)); se = sd/math.sqrt(n)
        margin = float(t.ppf(.975, n-1))*se
        result.update(sd=sd, iid_t_ci95=[profit/n-margin, profit/n+margin],
                      iid_one_sided_p=float(t.sf(profit/n/se, n-1)) if se else None)
    if n > 30:
        result['without_top3'] = math.fsum(sorted(a)[:-3])/(n-3)
    return result


def read_inputs(path, freeze_path):
    path = Path(path)
    if path.is_dir():
        groups = {}
        for p in path.glob('groups*.json'): groups.update(json.loads(p.read_text()))
        entries = {}
        for p in path.glob('entries*.json'):
            d = json.loads(p.read_text()); entries[d['k']] = d['rows']
        meta = json.loads((path/'metadata.json').read_text())
        return groups, entries, meta['freeze'], meta['generated'], meta['acquisition']
    d = json.loads(path.read_text()); groups = d['daily_audit']['groups']
    entries = {k:[[e['event_id'],e['day'],e.get('observed_at_utc'),e['profit'],
                   e['league'],e['minute'],e['market']['line'],e['market']['price']]
                  for e in v['entries']] for k,v in groups.items() if 'entries' in v}
    return groups,entries,json.loads(Path(freeze_path).read_text()),d['generated_at_utc'],d['daily_audit']['acquisition']


def row_stats(rows):
    closed = [r for r in rows if r[3] is not None]
    result = summary(r[3] for r in closed)
    result.update(selected=len(rows),unresolved=len(rows)-len(closed),
        leagues=len({r[4] for r in closed}),
        mean_price=math.fsum(r[7] for r in closed)/len(closed) if closed else None,
        mean_minute=math.fsum(r[5] for r in closed)/len(closed) if closed else None)
    return result


def run(source, freeze_path):
    groups,entries,freeze,generated,acquisition = read_inputs(source,freeze_path)
    checks=[]; statistics={}; periods={}; forward={}; mix={}
    for key,g in groups.items():
        values=[p for day in g['days'].values() for p in day['profits']]
        result=summary(values)
        assert result['n']==g['total']['n'],key
        assert abs(result['profit']-g['total']['unit_profit'])<1e-9,key
        statistics[key]=dict(name=g['name'],**result)
        days={day:summary(s['profits']) for day,s in g['days'].items()}
        before=[p for day,s in g['days'].items() if day<'2026-10-28' for p in s['profits']]
        after=[p for day,s in g['days'].items() if '2026-10-28'<=day<'2026-10-31' for p in s['profits']]
        recent=[p for day,s in g['days'].items() if day=='2026-10-31' for p in s['profits']]
        periods[key]=dict(before_28=summary(before),days_28_30=summary(after),partial_31=summary(recent),days=days)
        checks.append(key)
    cutoff=dt.datetime.fromisoformat(freeze['frozen_at_utc'])
    forward_ids={}
    for group in freeze['groups']:
        key=group['section']+'/'+group['pocket_id']; rows=entries[key]
        assert len({r[0] for r in rows})==len(rows),key
        new=[r for r in rows if dt.datetime.fromisoformat(r[2].replace('Z','+00:00'))>=cutoff]
        old=[r for r in rows if dt.datetime.fromisoformat(r[2].replace('Z','+00:00'))<cutoff]
        forward[key]=dict(frozen_n=group['n'],frozen_roi=group['roi'],
                         original_entries_before_freeze=row_stats(old),
                         new_original_entries=row_stats(new),
                         new_original_entries_before_31=row_stats([r for r in new if r[1]<'2026-10-31']))
        forward_ids[key]={r[0] for r in new}
        a=[r for r in rows if '2026-10-26'<=r[1]<'2026-10-28' and r[3] is not None]
        b=[r for r in rows if '2026-10-28'<=r[1]<'2026-10-31' and r[3] is not None]
        shared={r[4] for r in a}&{r[4] for r in b}
        mix[key]=dict(early_26_27=row_stats(a),later_28_30=row_stats(b),shared_leagues=len(shared),
            early_shared=row_stats([r for r in a if r[4] in shared]),
            later_shared=row_stats([r for r in b if r[4] in shared]))
    allids=[r[0] for rows in entries.values() for r in rows]
    counts=collections.Counter(allids)
    pairwise=[]
    for i,(ka,ra) in enumerate(entries.items()):
        for kb,rb in list(entries.items())[i+1:]:
            a={r[0] for r in ra};b={r[0] for r in rb}
            pairwise.append(dict(a=ka,b=kb,intersection=len(a&b),jaccard=len(a&b)/len(a|b)))
    return dict(generated_at_utc=generated,freeze_at_utc=freeze['frozen_at_utc'],
        reconciled_groups=len(checks),statistics=statistics,periods=periods,
        forward=forward,league_comparison=mix,
        overlap=dict(pocket_entries=len(allids),unique_matches=len(counts),
            matches_in_multiple_groups=sum(c>1 for c in counts.values()),pairwise=pairwise),
        acquisition={day:dict(captures=v['captures'],failed=v['failed'],coverage=v['coverage'],
            complete_ratio=v['coverage'].get('complete',0)/v['coverage']['eligible']
            if v['coverage'].get('eligible') else None,missing=v.get('missing'))
            for day,v in acquisition.items() if day>='2026-10-26'},
        limitations=['Unadjusted iid t intervals are descriptive and optimistic: groups were selected, matches share days/leagues, and multiple testing is not corrected.',
          '31 October is partial with open entries; primary daily comparison excludes it.',
          'Before/after differences and common-league cuts do not identify causality.',
          'Original-entry forward split uses exact freeze timestamp; resolved-after-freeze is not treated as a new entry.',
          'Research first-entry group results are not executed user bets or a deduplicated manual LIVE portfolio.'])


if __name__=='__main__':
    result=run(sys.argv[1],sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('generated_at_utc','reconciled_groups','forward','league_comparison','overlap')},ensure_ascii=False,indent=2))
