"""Explicit Events-only tennis snapshot; expert selection happens after collection."""
import concurrent.futures as cf
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

VERSION = 'tennis-expert-2026-10-17-v1'

def fetch(path, **params):
    started = time.time()
    params['token'] = os.environ['BETSAPI_KEY']
    try:
        url = 'https://api.b365api.com' + path + '?' + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.load(response)
        return {'data': data, 'observed_at': time.time(), 'elapsed': time.time()-started}
    except Exception as exc:
        # Never print URLs or exception messages containing credentials.
        return {'error': type(exc).__name__, 'observed_at': time.time()}

def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None

def prices(payload, event, now):
    if payload.get('success') != 1:
        return {'status': 'unavailable'}
    result = payload.get('results') or {}
    rows = (result.get('odds') or {}).get('13_1') or []
    if not rows:
        return {'status': 'missing_market'}
    row = max(rows, key=lambda r: number(r.get('add_time')) or 0)
    stats = result.get('stats') or {}
    checked = number((stats.get('odds_update') or {}).get('13_1'))
    quoted = number(row.get('add_time'))
    direction = str(stats.get('matching_dir'))
    out = {'status': 'unverified', 'raw_latest': row, 'checked_at': checked,
           'quoted_at': quoted, 'matching_dir': direction}
    if direction not in ('1', '-1'):
        return out
    home, away = number(row.get('home_od')), number(row.get('away_od'))
    if direction == '-1':
        home, away = away, home
    out.update(home=home, away=away)
    if not home or not away or min(home, away) <= 1:
        out['status'] = 'suspended'
    elif not quoted or not checked or not 0 <= now-checked <= 90 or not 0 <= now-quoted <= 90:
        out['status'] = 'stale_or_unknown'
    elif str(event.get('time_status')) != '1':
        out['status'] = 'not_live'
    else:
        out['status'] = 'fresh_needs_score_review'
    return out

def main():
    started = time.time()
    board = fetch('/v3/events/inplay', sport_id=13)
    payload = board.get('data', {})
    if payload.get('success') != 1:
        print('TENNIS_ERROR=' + json.dumps(board))
        raise SystemExit(1)
    request = json.loads(Path('tennis/request.json').read_text()) if Path('tennis/request.json').exists() else {}
    requested = {str(x) for x in request.get('event_ids', [])}
    events = [e for e in payload.get('results', []) if str(e.get('time_status')) == '1'
              and not re.search(r'virtual|esport|simulated|cyber', e.get('league', {}).get('name', ''), re.I)]
    if requested:
        events = [e for e in events if str(e['id']) in requested]
    records = {str(e['id']): {'board': e} for e in events}
    endpoints = {'view': '/v1/event/view', 'history': '/v1/event/history',
                 'trend': '/v1/event/stats_trend', 'odds': '/v2/event/odds'}
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        futures = {}
        for identity in records:
            for kind, endpoint in endpoints.items():
                args = {'event_id': identity}
                if kind == 'history': args['qty'] = 20
                if kind == 'odds': args.update(source='bet365', odds_market='1')
                futures[pool.submit(fetch, endpoint, **args)] = identity, kind
        for future in cf.as_completed(futures):
            identity, kind = futures[future]
            records[identity][kind] = future.result()
    for record in records.values():
        view = record.get('view', {}).get('data', {}).get('results') or []
        event = view[0] if isinstance(view, list) and view else record['board']
        record['event'] = event
        record['winner_prices'] = prices(record['odds'].get('data', {}), event, time.time())
    snapshot = {'version': VERSION, 'observed_at': started, 'finished_at': time.time(),
                'board': board, 'records': records, 'selection_mode': 'expert_in_chat',
                'requests': 1 + 4*len(records)}
    identity = os.environ.get('GITHUB_RUN_ID', str(int(started))) + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    path = Path('tennis/snapshots') / (identity + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('snapshot_path=' + str(path) + '\n')
    print('TENNIS_SUMMARY=' + json.dumps({'snapshot': str(path), 'pool': len(records), 'requests': snapshot['requests'], 'elapsed': time.time()-started}))
    for identity, record in records.items():
        event = record['event']
        print('TENNIS_EVENT=' + json.dumps({'id': identity, 'event': event,
            'prices': record['winner_prices'],
            'history_status': record['history'].get('data', {}).get('success'),
            'trend_status': record['trend'].get('data', {}).get('success')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
