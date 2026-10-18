"""Small immutable receipts for explicitly published Live choices; no APIs."""
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


def validate_receipt(payload, filename=None):
    if not isinstance(payload, dict) or payload.get('receipt_schema_version') != 1 or payload.get('kind') != 'published_live_choices':
        raise ValueError('Invalid Live publication receipt schema')
    sid = payload.get('snapshot_id')
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9-]+', sid) or (filename is not None and filename != sid + '.json'):
        raise ValueError('Publication receipt snapshot identity mismatch')
    records = payload.get('selections')
    if not isinstance(records, list) or not 1 <= len(records) <= 5:
        raise ValueError('Publication receipt requires 1-5 explicitly shown choices')
    ids = set()
    for row in records:
        if not isinstance(row, dict) or row.get('snapshot_id') != sid:
            raise ValueError('Publication record snapshot identity mismatch')
        eid = row.get('event_id')
        if not isinstance(eid, str) or not eid.isdigit() or eid in ids:
            raise ValueError('Invalid or duplicate publication event ID')
        ids.add(eid)
        if row.get('selected_at_utc') != payload.get('selected_at_utc'):
            raise ValueError('Publication timestamp mismatch')
        try:
            value = datetime.fromisoformat(row['selected_at_utc'].replace('Z', '+00:00'))
            if value.tzinfo is None:
                raise ValueError()
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ValueError('Publication requires its recorded timezone-aware timestamp') from None
        if not row.get('rules_version') or row.get('mode') not in ('full', 'refresh'):
            raise ValueError('Publication version and mode required')
        if row['rules_version'] == 'sports-only-2026-10-18-v8' and len(records) > 3:
            raise ValueError('v8 publication is limited to three choices')
        for field in ('sports_reason', 'expected_scenario'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError('Publication reason and scenario required')
    return payload


def make_receipt(records, time_basis='recorded_for_final_delivery'):
    if not records:
        raise ValueError('No publication receipt for an empty answer')
    return validate_receipt({
        'receipt_schema_version': 1, 'kind': 'published_live_choices',
        'snapshot_id': records[0]['snapshot_id'], 'selected_at_utc': records[0]['selected_at_utc'],
        'publication_time_basis': time_basis,
        'selections': deepcopy(records),
    })


def read_publications(directory):
    records = []
    for path in sorted(Path(directory).glob('*.json')):
        try:
            receipt = validate_receipt(json.loads(path.read_text()), path.name)
        except (OSError, ValueError, TypeError) as error:
            # Never silently lose exclusions and republish after corrupt receipt data.
            raise ValueError('Cannot safely read publication receipt ' + path.name) from error
        records.extend(receipt['selections'])
    return sorted(records, key=lambda row: (datetime.fromisoformat(row['selected_at_utc'].replace('Z', '+00:00')), row['snapshot_id'], row['rank']))


def merge_publications(ledger, directory):
    """Virtual combined journal; the legacy file remains byte-for-byte unchanged."""
    result = deepcopy(ledger)
    existing = {(r['snapshot_id'], r['event_id']): r for r in result.get('selections', [])}
    ids = {str(r['event_id']) for r in result.get('events', [])}
    for row in read_publications(directory):
        identity = (row['snapshot_id'], row['event_id'])
        if identity in existing:
            if row != existing[identity]:
                raise ValueError('An existing publication is immutable')
            continue
        if row['event_id'] in ids and row['mode'] != 'refresh':
            raise ValueError('A repeated publication requires explicit refresh')
        result.setdefault('selections', []).append(row)
        existing[identity] = row
        if row['event_id'] not in ids:
            result.setdefault('events', []).append({
                'event_id': row['event_id'], 'match': row.get('home', '') + ' - ' + row.get('away', ''),
                'selected_at_utc': row['selected_at_utc'],
            })
            ids.add(row['event_id'])
    return result


def write_receipt(directory, receipt):
    """Atomic local preparation; GitHub create_file is the durable publication step."""
    validate_receipt(receipt)
    path = Path(directory) / (receipt['snapshot_id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text()) != receipt:
            raise ValueError('An existing publication receipt is immutable')
        return path
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(receipt, handle, ensure_ascii=False, separators=(',', ':'))
        handle.write('\n')
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if json.loads(path.read_text()) != receipt:
                raise ValueError("An existing publication receipt is immutable")
    finally:
        temporary.unlink(missing_ok=True)
    return path
