"""Versioned, observation-only second-half history; no I/O or ranking inputs."""
from live_goal_profile import numeric

VERSION = 'second-half-profile-2026-10-18-v1'


def valid_score(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(x) is int and x >= 0 for x in value))


def evaluate(event):
    profile = event.get('goal_profile') or {}
    cutoff = numeric(event.get('start_time'))
    league_id = str(event.get('league_id') or '')
    target_id = str(event.get('event_id') or '')
    out = {'version': VERSION, 'research_only': True,
           'source': 'same_snapshot_goal_profile',
           'profile_fetched_at': profile.get('fetched_at'),
           'kickoff_cutoff': cutoff, 'available': False,
           'sufficient_history': False, 'relative_mean': None,
           'above_league_mean': None,
           'in_46_60_window': None,
           'minimum_history': {'home': 5, 'away': 5, 'league': 10}}
    minute = numeric(event.get('minute'))
    if minute is not None:
        out['in_46_60_window'] = 46 <= minute <= 60

    def clean(rows, team_id=None):
        seen = set()
        for row in rows:
            eid = str(row.get('event_id') or '')
            at = numeric(row.get('time'))
            if (not eid or eid == target_id or eid in seen or not cutoff
                    or at is None or not 0 < at < cutoff
                    or not league_id or str(row.get('league_id')) != league_id
                    or not valid_score(row.get('ft'))):
                continue
            if team_id and team_id not in (row.get('home_id'), row.get('away_id')):
                continue
            seen.add(eid)
            yield row

    def half(row):
        ht = row.get('ht')
        if not valid_score(ht) or any(h > f for h, f in zip(ht, row['ft'])):
            return None
        return sum(row['ft']) - sum(ht)

    def summarize(values):
        return {'n': len(values),
                'mean_total': sum(values)/len(values) if values else None,
                'two_plus_count': sum(x >= 2 for x in values)}

    league_rows = list(clean((profile.get('league') or {}).get('matches') or []))
    league_by_id = {str(r['event_id']): r for r in league_rows}
    out['league'] = summarize([value for r in league_rows if (value := half(r)) is not None])
    for side in ('home', 'away'):
        team = profile.get(side) or {}
        tid = str(event.get(side+'_id') or '')
        values, raw_ids, recovered_ids, conflicts = [], [], [], []
        rows = list(clean(team.get('matches') or [], tid)) if tid else []
        for row in rows:
            eid = str(row['event_id'])
            other = league_by_id.get(eid)
            value = half(row)
            if other:
                agrees = all(row.get(k) == other.get(k)
                             for k in ('home_id', 'away_id', 'league_id', 'time', 'ft'))
                other_value = half(other)
                if not agrees or (value is not None and other_value is not None
                                  and row['ht'] != other['ht']):
                    conflicts.append(eid)
                    continue
                if value is None and other_value is not None:
                    values.append(other_value)
                    recovered_ids.append(eid)
                    continue
            if value is not None:
                values.append(value)
                raw_ids.append(eid)
        out[side] = dict(summarize(values), team_id=tid, history_n=len(rows),
                         raw_n=len(raw_ids), recovered_n=len(recovered_ids),
                         raw_event_ids=raw_ids, recovered_event_ids=recovered_ids,
                         conflict_event_ids=conflicts)
    out['available'] = bool(out['home']['n'] or out['away']['n'])
    enough = (out['home']['n'] >= 5 and out['away']['n'] >= 5
              and out['league']['n'] >= 10)
    out['sufficient_history'] = enough
    if enough and out['league']['mean_total'] > 0:
        mean = (out['home']['mean_total'] + out['away']['mean_total'])/2
        out['relative_mean'] = mean/out['league']['mean_total']
        out['above_league_mean'] = mean > out['league']['mean_total']
        out['reason'] = None
    else:
        out['reason'] = ('zero_league_mean' if enough else 'insufficient_history'
                         if profile else 'profile_not_collected')
    return out
