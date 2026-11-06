"""Prospective paired comparison of the frozen ten groups and SOT pace >= 0.10.

Both arms start together, own their first-entry state, and reuse live admission.
No historical backfill, provider requests, user publications or betting actions.
"""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import tracked_groups as base
from distinct_pockets import read, number, stamp

VERSION = 'tracked-sot-comparison-v1'
CONFIG = '.github/live-tracked-sot.json'
JOURNAL = 'research/live-background/experimental/tracked-sot-receipts'
SECTION = 'tracked_sot'
ARMS = ('baseline', 'enhanced')


def config(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        return None, None
    parent, digest = base.config(root)
    when = dt.datetime.fromisoformat(cfg['activation_msk'])
    if (cfg.get('version') != VERSION or when.tzinfo is None
            or cfg.get('parent_catalog_sha256') != digest
            or cfg.get('minimum_sot_per_minute') != 0.1
            or cfg.get('stake_units') != 1
            or set(cfg.get('names', {})) != base.IDS):
        raise ValueError('Invalid frozen shots-on-target comparison')
    cfg['pockets'] = copy.deepcopy(parent['pockets'])
    for group in cfg['pockets']:
        group['name'] = cfg['names'][group['id']]
    return cfg, hashlib.sha256(raw).hexdigest()


def history(root, cfg, digest):
    rows = {arm: {g['id']: {} for g in cfg['pockets']} for arm in ARMS}
    captures = errors = 0
    activation = stamp(cfg['activation_msk'])
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        receipt = read(path)
        data = receipt.get(SECTION) or {}
        if data.get('id') != cfg['id']:
            continue
        if data.get('version') != VERSION or data.get('catalog_sha256') != digest:
            raise ValueError('Shots-on-target comparison changed within frozen series')
        if data.get('status') == 'error' or receipt.get('failed'):
            errors += 1
            continue
        if data.get('status') != 'active':
            continue
        if set(data.get('choices', {})) != set(ARMS):
            raise ValueError('Missing comparison arm')
        captures += 1
        for arm in ARMS:
            if set(data['choices'][arm]) != set(rows[arm]):
                raise ValueError('Missing comparison groups')
            for key, entries in data['choices'][arm].items():
                for entry in entries:
                    eid = str(entry['event_id'])
                    rate = number((entry.get('features') or {}).get('sot_rate'))
                    if (eid in rows[arm][key] or entry.get('arm') != arm
                            or entry.get('pocket_id') != key or entry.get('version') != VERSION
                            or entry.get('series_id') != cfg['id']
                            or entry.get('catalog_sha256') != digest
                            or (stamp(entry.get('decision_at_utc')) or 0) < activation
                            or (stamp(entry.get('observed_at_utc')) or 0) < activation
                            or (stamp((entry.get('market') or {}).get('checked_at')) or 0) < activation
                            or arm == 'enhanced' and (rate is None or rate < cfg['minimum_sot_per_minute'])):
                        raise ValueError('Duplicate, altered or pre-activation comparison entry')
                    rows[arm][key][eid] = entry
    return rows, captures, errors


def shots_on_target(event):
    raw = (event.get('board_stats') or {}).get('on_target')
    values = base.pair(raw)
    if values is None or any(isinstance(v, bool) for v in raw) or any(not v.is_integer() for v in values):
        return None
    return values


def evaluate(root, snapshot, ordered, market_for_event, outcomes, settle):
    cfg, digest = config(root)
    if cfg is None:
        return None
    rows, captures, errors = history(root, cfg, digest)
    results = {}
    # Reuse the same pure saved-quote callback and engine for both arms.
    # A rejected early state never becomes seen in the enhanced arm.
    for arm in ARMS:
        def accept(event, quote, values):
            shots = shots_on_target(event)
            rate = sum(shots) / values['minute'] if shots is not None else None
            return arm == 'baseline' or rate is not None and rate >= cfg['minimum_sot_per_minute']
        result = base.evaluate_catalog(snapshot, ordered, market_for_event, outcomes, settle,
                                      cfg, digest, (rows[arm], captures, errors), accept)
        events = {str(e['event_id']): e for e in snapshot['events']}
        for entries in result['choices'].values():
            for entry in entries:
                event = events[entry['event_id']]
                shots = shots_on_target(event)
                entry['arm'] = arm
                entry['shots_on_target'] = shots
                entry['features']['sot_rate'] = sum(shots) / entry['minute'] if shots is not None else None
                entry['parent_series_id'] = cfg['parent_series_id']
        results[arm] = result
    return dict(id=cfg['id'], version=VERSION, catalog_sha256=digest, status='active',
                started_at_utc=results['baseline']['started_at_utc'],
                observed_at_utc=snapshot['board_observed_at_utc'],
                choices={arm: result['choices'] for arm, result in results.items()},
                diagnostics={arm: result['diagnostics'] for arm, result in results.items()},
                previous_successful_captures=captures, prior_capture_errors=errors,
                parent_selection_changed=False, prior_results_in_new_series=False)


def portfolio(groups, cfg):
    """First entry across groups; equal timestamps use frozen group priority."""
    priorities = {g['id']: g['priority'] for g in cfg['pockets']}
    unique = {}
    for key, entries in groups.items():
        for eid, entry in entries.items():
            order = (stamp(entry['decision_at_utc']), priorities[key])
            if eid not in unique or order < unique[eid][0]:
                unique[eid] = (order, entry)
    return {eid: value[1] for eid, value in unique.items()}


def report(root, outcomes, settle):
    cfg, digest = config(root)
    if cfg is None:
        return None
    rows, captures, errors = history(root, cfg, digest)
    arms = {}
    for arm in ARMS:
        progress = base.summary(rows[arm], outcomes, settle)
        unique = portfolio(rows[arm], cfg)
        arms[arm] = dict(pockets={g['id']: dict(name=g['name'], priority=g['priority'],
                            **progress[g['id']]) for g in cfg['pockets']},
                         portfolio=base.summary({'all': unique}, outcomes, settle)['all'])
    return dict(id=cfg['id'], version=VERSION, catalog_sha256=digest,
                status='active' if captures else 'awaiting_first_successful_capture',
                started_at_utc=dt.datetime.fromisoformat(cfg['activation_msk']).astimezone(dt.timezone.utc).isoformat(),
                successful_captures=captures, capture_errors=errors, arms=arms,
                name='Достаточный темп ударов в створ', minimum_sot_per_minute=0.1,
                parent_selection_changed=False, prior_results_in_new_series=False)
