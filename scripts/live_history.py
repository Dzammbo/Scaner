"""History and explicit requests for on-demand live snapshots. No scheduler."""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

COUNTERS = ('on_target', 'off_target', 'corners', 'dangerous_attacks', 'xg', 'goals', 'redcards')

def _read_legacy_selected_events(path):
    """Read event IDs that already received an Основной live forecast."""
    try:
        payload = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return set(), ['selected-events file missing']
    except (json.JSONDecodeError, OSError) as exc:
        return set(), [type(exc).__name__ + ': ' + str(exc)[:180]]
    try:
        if payload.get('schema_version') != 1 or not isinstance(payload.get('events'), list):
            raise ValueError('unsupported selected-events schema')
        ids = {
            str(row['event_id']).strip()
            for row in payload['events']
            if isinstance(row, dict) and str(row.get('event_id') or '').strip()
        }
        return ids, []
    except (TypeError, ValueError) as exc:
        return set(), [type(exc).__name__ + ': ' + str(exc)[:180]]

def read_selected_events(path):
    """Legacy IDs plus durable small receipts, even before any materialization."""
    from live_publications import read_publications
    ids, warnings = _read_legacy_selected_events(path)
    ids.update(row['event_id'] for row in read_publications(Path(path).parent / 'live-publications'))
    return ids, warnings


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()

def read_request(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-file')
    parser.add_argument('--event-ids', default='')
    args = parser.parse_args(argv)
    mode, ids = 'full', []
    if args.request_file:
        request = json.loads(Path(args.request_file).read_text())
        age = datetime.now(timezone.utc).timestamp() - timestamp(request['requested_at_utc'])
        if not -30 <= age <= 180:
            raise ValueError('Request expired; issue a new request, do not reuse old match data')
        mode = request['mode']
        ids = request.get('event_ids', [])
        if mode not in ('full', 'refresh') or not isinstance(ids, list):
            raise ValueError('Invalid request mode or event_ids')
    elif args.event_ids:
        mode, ids = 'refresh', args.event_ids.split(',')
    ids = list(dict.fromkeys(str(x).strip() for x in ids))
    if mode == 'refresh' and (not 1 <= len(ids) <= 3 or any(not x.isdigit() for x in ids)):
        raise ValueError('Refresh requires 1-3 numeric event IDs')
    if mode == 'full' and ids:
        raise ValueError('Full scan must not be restricted to event IDs')
    return mode, ids

def read_history(directory, now):
    records, warnings = {}, []
    if not Path(directory).exists():
        return records, warnings
    # Select the latest observation per event; never splice different providers.
    for path in Path(directory).glob('*.json'):
        try:
            snap = json.loads(path.read_text())
            observed = snap['board_observed_at_utc']
            age = timestamp(now) - timestamp(observed)
            if not 0 < age <= 7200:
                continue
            for event in snap['events']:
                key = event['event_id']
                old = records.get(key)
                if old is None or timestamp(observed) > timestamp(old[0]):
                    records[key] = (observed, event)
        except (ValueError, KeyError, TypeError, OSError):
            warnings.append(path.name)
    return records, warnings

def compare_event(event, previous, now):
    if previous is None:
        return {'available': False, 'reason': 'no_previous_snapshot'}
    observed, old = previous
    seconds = timestamp(now) - timestamp(observed)
    if (event.get('home'), event.get('away')) != (old.get('home'), old.get('away')):
        return {'available': False, 'reason': 'event_identity_changed'}
    if event['minute'] < old['minute'] or not 0 < seconds <= 7200:
        return {'available': False, 'reason': 'clock_reset_or_invalid_interval'}
    deltas, rejected = {}, []
    for key in COUNTERS:
        before = (old.get('board_stats') or {}).get(key)
        after = (event.get('board_stats') or {}).get(key)
        if before is None or after is None:
            continue
        try:
            if len(before) != 2 or len(after) != 2:
                raise ValueError()
            values = [float(x) - float(y) for x, y in zip(after, before)]
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError()
            deltas[key] = [round(v, 4) for v in values]
        except (TypeError, ValueError):
            rejected.append(key)
    return {
        'available': bool(deltas), 'previous_observed_at_utc': observed,
        'elapsed_wall_seconds': round(seconds, 1),
        'previous_minute': old['minute'], 'minute_delta': event['minute'] - old['minute'],
        'previous_score': old['score'], 'score_changed': event['score'] != old['score'],
        'interval_is_10_15_wall_minutes': 600 <= seconds <= 900,
        'crosses_halftime': old['minute'] <= 45 < event['minute'],
        'counter_deltas': deltas, 'corrected_or_reset_fields': rejected,
        'note': 'Actual interval between observations; not pure playing time or a rolling 15-minute window',
    }

def save_snapshot(result, directory, snapshot_id):
    path = Path(directory) / (snapshot_id + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    return str(path)
