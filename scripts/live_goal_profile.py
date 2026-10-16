"""Historical goal context: pre-kickoff only, explicit coverage, no eligibility veto."""
import json
import re
import time
from pathlib import Path

PROFILE_VERSION = 1
CACHE_SECONDS = 86400
CUP = re.compile(r'cup|copa|coupe|pokal|кубок', re.I)


def numeric(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score(value):
    if isinstance(value, dict):
        value = str(value.get('home')) + '-' + str(value.get('away'))
    if not isinstance(value, str) or not re.fullmatch(r'\d+[-:]\d+', value):
        return None
    return [int(x) for x in re.split('[-:]', value)]


def clean_rows(rows, cutoff, team_id=None):
    result, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        eid, ts = str(row.get('id') or ''), numeric(row.get('time'))
        if not eid or eid in seen or ts is None or not 0 < ts < cutoff or str(row.get('time_status')) != '3':
            continue
        home, away = str((row.get('home') or {}).get('id') or ''), str((row.get('away') or {}).get('id') or '')
        if team_id and team_id not in (home, away):
            continue
        scores = row.get('scores') or {}
        ft = score(scores.get('2'))
        basis = 'scores.2'
        if ft is None:
            # Final ss may include extra time or penalties in knockout games.
            if scores.get('3') or scores.get('4') or CUP.search((row.get('league') or {}).get('name') or ''):
                continue
            ft, basis = score(row.get('ss')), 'provider_ss_no_period_breakdown'
        if ft is None:
            continue
        ht = score(scores.get('1'))
        if ht and any(a > b for a, b in zip(ht, ft)):
            ht = None
        result.append({'event_id': eid, 'time': ts, 'home_id': home, 'away_id': away,
                       'league_id': str((row.get('league') or {}).get('id') or ''),
                       'ft': ft, 'ht': ht, 'score_basis': basis})
        seen.add(eid)
    return sorted(result, key=lambda x: (-x['time'], x['event_id']))


def summarize(rows, team_id=None):
    n = len(rows)
    if not n:
        return {'n': 0}
    totals = [sum(r['ft']) for r in rows]
    out = {'n': n, 'mean_total': sum(totals)/n,
           'goals_0_1': sum(x <= 1 for x in totals), 'goals_2': totals.count(2),
           'goals_3_plus': sum(x >= 3 for x in totals),
           'over_1_5_count': sum(x >= 2 for x in totals),
           'period_verified_n': sum(r['score_basis'] == 'scores.2' for r in rows),
           'oldest_time': min(r['time'] for r in rows), 'latest_time': max(r['time'] for r in rows)}
    if team_id:
        gf = [r['ft'][0 if r['home_id'] == team_id else 1] for r in rows]
        ga = [r['ft'][1 if r['home_id'] == team_id else 0] for r in rows]
        out.update(goals_for=sum(gf), goals_against=sum(ga), mean_for=sum(gf)/n, mean_against=sum(ga)/n)
    second = [sum(r['ft'])-sum(r['ht']) for r in rows if r['ht'] is not None]
    out['second_half'] = {'n': len(second), 'mean_total': sum(second)/len(second) if second else None,
                          'two_plus_count': sum(x >= 2 for x in second)}
    return out


def build_profile(event, history, league_payload, fetched_at):
    cutoff = numeric(event.get('start_time'))
    out = {'version': PROFILE_VERSION, 'fetched_at': fetched_at, 'available': False,
           'ranking_ratio': 1.0, 'ranking_used': False, 'kickoff_cutoff': cutoff,
           'history_api_success': history.get('success'), 'league_api_success': league_payload.get('success'),
           'source': 'BetsAPI event/history and events/ended', 'history_qty_requested': 20}
    if not cutoff or not event.get('home_id') or not event.get('away_id'):
        return dict(out, reason='missing_verified_team_ids_or_kickoff')
    data = history.get('results') if history.get('success') == 1 else None
    if not isinstance(data, dict):
        return dict(out, reason='history_unavailable')
    for side in ('home', 'away'):
        tid = event[side+'_id']
        rows = clean_rows(data.get(side), cutoff, tid)[:20]
        same = [r for r in rows if r['league_id'] == event.get('league_id')]
        venue = [r for r in rows if r[side+'_id'] == tid]
        out[side] = {'team_id': tid, 'all': summarize(rows, tid), 'last10': summarize(rows[:10], tid),
                     'same_league': summarize(same, tid), 'venue': summarize(venue, tid), 'matches': rows}
    league_rows = clean_rows(league_payload.get('results'), cutoff)
    league_rows = [r for r in league_rows if r['league_id'] == event.get('league_id')][:50]
    out['league'] = dict(summarize(league_rows), scope='latest_available_first_page_before_kickoff', matches=league_rows)
    out['available'] = bool(out['home']['all']['n'] or out['away']['all']['n'])
    # Comparable competition only. Coverage minimum is NOT a match eligibility filter.
    h, a, league = out['home']['same_league'], out['away']['same_league'], out['league']
    if min(h['n'], a['n']) >= 5 and league['n'] >= 10 and league['mean_total'] > 0:
        out['ranking_ratio'] = ((h['mean_for']+a['mean_against']+a['mean_for']+h['mean_against'])/2)/league['mean_total']
        out['ranking_used'] = True
    out['reason'] = None if out['ranking_used'] else 'insufficient_comparable_history_neutral_rank'
    return out


def read_cache(directory, now):
    events, leagues = {}, {}
    paths = sorted(Path(directory).glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True)[:50]
    for path in paths:
        try:
            data = json.loads(path.read_text())
            for event in data.get('events', []):
                p = event.get('goal_profile') or {}
                if p.get('version') != PROFILE_VERSION or not p.get('available'):
                    continue
                age = now - float(p.get('fetched_at', 0))
                if not 0 <= age <= CACHE_SECONDS:
                    continue
                events.setdefault(event['event_id'], (event.get('home_id'), event.get('away_id'), p))
            for lid, entry in data.get('goal_profile_league_cache', {}).items():
                if 0 <= now-float(entry.get('fetched_at', 0)) <= CACHE_SECONDS:
                    leagues.setdefault(lid, entry)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return events, leagues
