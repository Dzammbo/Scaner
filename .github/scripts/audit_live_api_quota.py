"""Read-only quota audit of recent saved unified collector snapshots."""
import collections,datetime as dt,gzip,json,os,statistics
from pathlib import Path
root=Path(os.environ['COLLECTOR_ROOT'])
paths=sorted((root/'research/live-background/experimental/snapshots').rglob('*.json.gz'))
rows=[]
for path in paths[-48:]:
    s=json.loads(gzip.decompress(path.read_bytes()))
    api=s.get('lab_api') or {}
    cov=s.get('research_detail_coverage') or {}
    price=(s.get('lab_price_coverage') or {}).get('full_collection_window_5_89') or {}
    plan=s.get('adaptive_deep_plan') or {}
    rows.append(dict(snapshot_id=s.get('snapshot_id'),at=s.get('board_observed_at_utc') or s.get('request',{}).get('requested_at_utc'),
       status=s.get('lab_status'),error=s.get('error_type'),calls=api.get('calls',0),
       blocked=api.get('blocked',0),remaining=api.get('remaining_lower_bound'),
       cap=api.get('cap'),reserve=api.get('primary_reserve'),
       endpoints=api.get('endpoints',{}),observed=cov.get('observed'),
       market_policy=s.get('market_probe_policy'),market_probes=s.get('lab_market_probe_count'),
       odds_calls=(api.get('endpoints') or {}).get('/v2/event/odds',0),
       eligible=cov.get('eligible'),deep=cov.get('selected'),complete=cov.get('complete'),
       priced=price.get('exact'),price_requested=price.get('requested'),
       available_at_plan=plan.get('available_calls'),reserved_market_calls=plan.get('reserved_market_calls')))
good=[r for r in rows if r['status'] in ('captured','partial')]
for r in good:
    if r['market_policy'] == 'all_known_score_5_89_deep_first_no_outside_window_odds':
        assert r['market_probes'] == r['eligible'], r
        assert r['odds_calls'] <= r['eligible'], r
hours=collections.defaultdict(list)
for r in rows:
    if r['at']: hours[r['at'][:13]].append(r)
def summ(rs):
    good=[r for r in rs if r['status'] in ('captured','partial')]
    return dict(cycles=len(rs),success=len(good),failed=len(rs)-len(good),
                calls=sum(r['calls'] for r in rs),
                max_calls=max((r['calls'] for r in rs),default=0),
                blocked=sum(r['blocked'] for r in rs),
                min_remaining=min((r['remaining'] for r in good if r['remaining'] is not None),default=None),
                priced=sum(r['priced'] or 0 for r in good),price_requested=sum(r['price_requested'] or 0 for r in good),
                eligible=sum(r['eligible'] or 0 for r in good),deep=sum(r['deep'] or 0 for r in good),
                complete=sum(r['complete'] or 0 for r in good))
print(json.dumps(dict(sample=summ(rows),by_hour={h:summ(rs) for h,rs in hours.items()},last_12=rows[-12:],
  endpoint_totals=dict(sum((collections.Counter(r['endpoints']) for r in rows),collections.Counter()))),ensure_ascii=False))
