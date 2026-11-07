"""Independent sampling and a fail-closed, observation-only price audit.

Research inclusion probabilities describe the randomized arm, not the union
with control picks or provider response probabilities. No bet execution.
"""
import collections
import datetime as dt
import hashlib
import json
import math
import re
import time
from pathlib import Path

CONFIG = '.github/live-research-foundation.json'
VERSION = 'independent-research-20261107-v1'
JOURNAL = 'research-foundation-receipts'


def config(root):
    if root is None or not (Path(root) / CONFIG).exists():
        return None
    cfg = json.loads((Path(root) / CONFIG).read_text())
    if not cfg.get('enabled'):
        return None
    if cfg.get('version') != VERSION or cfg.get('mode') != 'observation_only':
        raise ValueError('Unsupported research foundation mode/version')
    if not 0 < cfg['research_share_of_deep_slots'] <= 1 or not 0 <= cfg['broad_quote_budget_share'] < 1:
        raise ValueError('Invalid sampling allocation')
    if not 0 <= cfg['comparison_matches_per_cycle'] <= 2:
        raise ValueError('Invalid comparison budget')
    if cfg['reference_source'] != 'betfair':
        raise ValueError('Unverified reference adapter')
    # Activation needs an implemented and validated forecasting/execution path.
    if cfg['approved_models'] or cfg['execution_source'] is not None:
        raise ValueError('Model/execution activation requires separate implementation')
    return cfg


def numeric(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    if isinstance(value, (int, float)):
        return numeric(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def eligible(e):
    m = numeric(e.get('minute'))
    return m is not None and 5 <= m <= 89 and bool(re.fullmatch(r'\d+-\d+', str(e.get('score', ''))))


def digest(seed, value):
    return hashlib.sha256((seed + '|' + str(value)).encode()).hexdigest()


def plan(events, controls, available, seed, previous_deep, cfg):
    """Fix stratum sample sizes before looking at IDs, sports, cache or replies."""
    previous_deep = set(map(str, previous_deep))
    population = {str(e['event_id']): e for e in events if eligible(e)}
    available = max(0, int(available))
    comparisons = min(cfg['comparison_matches_per_cycle'], len(population), available // 12)
    extra = 2 * comparisons  # one reference request and one primary recheck
    broad_quotes = min(len(population), int((available - extra) * cfg['broad_quote_budget_share']))
    # Worst case per match: trend, match history, league history, primary quote.
    slots = min(len(population), max(0, (available - extra - broad_quotes) // 4))
    research_slots = min(slots, math.ceil(slots * cfg['research_share_of_deep_slots']))
    groups = collections.defaultdict(list)
    for eid, e in population.items():
        phase = '5-29' if e['minute'] < 30 else '30-59' if e['minute'] < 60 else '60-89'
        groups[phase + '|' + ('repeat' if eid in previous_deep else 'new')].append(e)
    keys = sorted(groups, key=lambda key: digest(seed + '|strata', key))
    allocation = collections.Counter()
    while sum(allocation.values()) < research_slots:
        for key in keys:
            if allocation[key] < len(groups[key]):
                allocation[key] += 1
                if sum(allocation.values()) == research_slots:
                    break
    chosen = []
    for key in keys:
        ordered = sorted(groups[key], key=lambda e: digest(seed + '|draw', e['event_id']))
        chosen.extend(ordered[:allocation[key]])
        for e in groups[key]:
            e['research_sampling'] = dict(version=VERSION, stratum=key,
                population=len(groups[key]), sample_n=allocation[key],
                research_inclusion_probability=allocation[key] / len(groups[key]),
                independent_research_selected=e in ordered[:allocation[key]],
                seed=seed, cohort=key.split('|')[1])
    research_ids = {str(e['event_id']) for e in chosen}
    chosen_ids = set(research_ids)
    for e in controls:
        eid = str(e['event_id'])
        if len(chosen) >= slots:
            break
        if eid in population and eid not in chosen_ids:
            chosen.append(population[eid])
            chosen_ids.add(eid)
    for e in events:
        eid = str(e['event_id']); ok = eid in population
        sampling = e.get('research_sampling', {}) if ok else dict(
            version=VERSION, stratum='outside_scope', population=0, sample_n=0,
            research_inclusion_probability=0, independent_research_selected=False,
            seed=seed, cohort='repeat' if eid in previous_deep else 'new')
        selected = eid in chosen_ids
        sampling.update(selected=selected, eligible_for_deep_observation=ok,
            eligible_for_new_deep=ok and eid not in previous_deep,
            previously_deep=eid in previous_deep,
            arm='research' if eid in research_ids else 'control' if selected else 'board',
            inclusion_probability=1 if ok and len(chosen_ids) == len(population) else None,
            probability_note='Use research_inclusion_probability only for independent_research_selected; union/control and response probabilities are not inferred',
            reason=None if selected else 'not_drawn_or_control_quota' if ok else 'outside_window_or_unknown_score')
        e['research_sampling'] = sampling
        e['research_enriched'] = selected
    details = 3 * len(chosen)
    reserve = min(len(population), max(0, available - details - extra)) + extra
    return chosen, dict(version=VERSION, observed=len(events), eligible=len(population),
        selected=len(chosen), research_selected=len(research_ids),
        control_selected=len(chosen_ids - research_ids), control_candidates=len(controls),
        available_calls=available, estimated_detail_calls=details,
        reserved_market_calls=reserve, comparison_reserved_calls=extra,
        comparison_matches=comparisons, fixed_deep_limit=None,
        strata={k:dict(population=len(groups[k]), sample_n=allocation[k]) for k in keys},
        seed=seed, allocation_fixed_before_requests=True)


def market_order(events, priority):
    """Drawn matches first; remaining board in seeded order, never provider rank."""
    seen = set(); result = []
    research = [e for e in events if (e.get('research_sampling') or {}).get('independent_research_selected')]
    rest = sorted(events, key=lambda e: digest((e.get('research_sampling') or {}).get('seed', ''), e['event_id']))
    for e in research + list(priority) + rest:
        eid = str(e['event_id'])
        if eid not in seen and eligible(e):
            seen.add(eid); result.append(e)
    return result


def quote_record(e, parsed, bookmaker, sent_at, received_at, payload=None, error=None):
    q = parsed.get('events_main_total') or {}
    return dict(bookmaker=bookmaker, event_id=str(e['event_id']), market='1_3',
        period='full_time', settlement_basis='full_match_total', line=q.get('line'),
        over=q.get('over'), under=q.get('under'), score=q.get('quote_score'),
        quote_changed_at=q.get('quote_changed_at'), market_checked_at=q.get('market_checked_at'),
        requested_at_utc=sent_at, received_at_utc=received_at,
        available=bool(q), provider_status=parsed.get('main_total_status'),
        reason=error or parsed.get('main_total_reason'), suspended=None,
        available_stake=None, execution_confirmed=False, acceptance_receipt=None,
        raw_payload=payload)


def collect_comparisons(c, snapshot, budget, cfg):
    limit = snapshot['adaptive_deep_plan'].get('comparison_matches', 0)
    drawn = [e for e in snapshot['events'] if e['research_sampling'].get('independent_research_selected')]
    drawn.sort(key=lambda e: digest(e['research_sampling']['seed'] + '|comparison', e['event_id']))
    selected = drawn[:limit]  # chosen before inspecting prices or responses
    for e in snapshot['events']:
        e['research_price_check'] = dict(selected=False, reason='not_in_comparison_sample')
    for e in selected:
        records = {}
        for label, source in [('reference', cfg['reference_source']), ('primary_recheck', 'bet365')]:
            sent = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                payload, _ = budget('/v2/event/odds', event_id=e['event_id'], source=source,
                                    odds_market='3', _timeout=8)
                received = dt.datetime.now(dt.timezone.utc).isoformat()
                parsed = c.live.current_main_total(payload, e, int(time.time()))
                records[label] = quote_record(e, parsed, source, sent, received, c.sanitize(payload))
            except Exception as exc:
                received = dt.datetime.now(dt.timezone.utc).isoformat()
                records[label] = quote_record(e, {}, source, sent, received, error=type(exc).__name__)
        e['research_price_check'] = dict(selected=True, **records)
    return len(selected)


def goal_check(e):
    if e.get('score') == '0-0':
        return True, 'zero_score'
    previous = e.get('dynamics') or {}
    if previous.get('previous_score') == e.get('score') and (numeric(previous.get('minute_delta')) or 0) > 3 and (numeric(previous.get('elapsed_wall_seconds')) or 0) > 180:
        return True, 'stable_observed_score'
    # No goal time inferred from final scores or a single snapshot.
    return False, 'post_goal_pause_unconfirmed'


def valid_quote(q, e, now, cfg):
    if not q or not q.get('available'):
        return False
    line = numeric(q.get('line')); checked = timestamp(q.get('market_checked_at'))
    price = [numeric(q.get(k)) for k in ('over', 'under')]
    return (line is not None and line >= 0 and abs(line * 4 - round(line * 4)) < 1e-8
        and all(p is not None and p > 1 for p in price) and q.get('score') == e.get('score')
        and checked is not None and 0 <= now - checked <= cfg['max_quote_check_age_seconds'])


def evaluate(snapshot, cfg, now=None):
    now = time.time() if now is None else now
    rows = []; counts = collections.Counter()
    board_time = timestamp(snapshot.get('board_observed_at_utc'))
    for e in snapshot.get('events', []):
        q = e.get('research_primary_quote') or {}; check = e.get('research_price_check') or {}
        reasons = []; flags = {}
        flags['domain'] = eligible(e) and str(e.get('time_status')) == '1'
        previous_score = (e.get('dynamics') or {}).get('previous_score')
        flags['score_consistent'] = not (eligible(e) and re.fullmatch(r'\d+-\d+', str(previous_score))
            and any(int(a) < int(b) for a,b in zip(e['score'].split('-'), previous_score.split('-'))))
        flags['board_fresh'] = board_time is not None and 0 <= now - board_time <= cfg['max_board_age_seconds']
        flags['primary_quote'] = valid_quote(q, e, now, cfg)
        flags['post_goal_pause'], goal_reason = goal_check(e)
        ref = check.get('reference') or {}; repeat = check.get('primary_recheck') or {}
        ref_t = timestamp(ref.get('received_at_utc')); repeat_t = timestamp(repeat.get('received_at_utc'))
        flags['reference_same_contract'] = (valid_quote(ref, e, now, cfg) and valid_quote(repeat, e, now, cfg)
            and ref.get('line') == repeat.get('line') and ref_t is not None and repeat_t is not None
            and abs(ref_t - repeat_t) <= cfg['max_comparison_skew_seconds'])
        flags['primary_price_rechecked'] = (valid_quote(repeat, e, now, cfg) and flags['primary_quote']
            and q.get('line') == repeat.get('line') and q.get('over') == repeat.get('over')
            and q.get('under') == repeat.get('under'))
        labels = {'domain':'Вне проверяемой области', 'score_consistent':'Наблюдалось исправление счёта', 'board_fresh':'Состояние матча устарело',
            'primary_quote':'Нет согласованной свежей цены', 'post_goal_pause':'Пауза после гола не подтверждена',
            'reference_same_contract':'Нет сопоставимой цены второго источника',
            'primary_price_rechecked':'Повторная проверка той же цены не подтверждена'}
        reasons.extend(labels[k] for k, ok in flags.items() if not ok)
        reasons.extend(['Нет допущенной модели вероятности', 'Цена исполнения пользователя не подключена'])
        p = None; cross = None
        # Only half-lines have the binary probability used here. Asian lines stay raw.
        if flags['primary_quote'] and round(q['line'] * 4) % 4 == 2:
            p = (1/q['over']) / (1/q['over'] + 1/q['under'])
        if flags['reference_same_contract'] and round(ref['line'] * 4) % 4 == 2:
            p_ref = (1/ref['over']) / (1/ref['over'] + 1/ref['under'])
            cross = dict(reference_fair_over=p_ref, primary_over=repeat['over'],
                primary_under=repeat['under'], relative_over=p_ref*repeat['over']-1,
                relative_under=(1-p_ref)*repeat['under']-1,
                interpretation='market_discrepancy_not_validated_model_EV', commission_known=False)
        counts.update(reasons)
        rows.append(dict(event_id=str(e['event_id']), minute=e.get('minute'), score=e.get('score'),
            home=e.get('home'), away=e.get('away'), league=e.get('league'),
            sampling=e.get('research_sampling'), board_stats=e.get('board_stats'),
            dynamics=e.get('dynamics'), trend_available=(e.get('stats_trend') or {}).get('available'),
            trend_request_sent=bool(e.get('trend_request_sent')), primary_quote=q,
            price_check=check, market_fair_over=p, market_comparison=cross, checks=flags,
            goal_evidence=goal_reason, status='Наблюдение без допуска к ставке', reasons=reasons,
            admitted=False, prediction=None, model_version=None, model_ev=None,
            risk_key=str(e['event_id']), max_positions_per_match=1,
            snapshot_id=snapshot['snapshot_id'], observed_at_utc=snapshot['board_observed_at_utc']))
    return dict(version=VERSION, name=cfg['name'], status='active', mode='observation_only',
        config_sha256=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),
        observed_at_utc=snapshot['board_observed_at_utc'],
        evaluated_at_utc=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),
        source_revision=snapshot.get('code_revision'), points=len(rows),
        independent_sample=sum(bool(r['sampling'].get('independent_research_selected')) for r in rows),
        comparison_requested=sum(bool(r['price_check'].get('selected')) for r in rows),
        comparable_prices=sum(r['checks']['reference_same_contract'] for r in rows),
        admitted=0, reasons=dict(counts), entries=[], observations=rows)


def latest_status(root, loader=None):
    cfg = config(root)
    if not cfg:
        return None
    paths = sorted((Path(root)/'research/live-background/experimental'/JOURNAL).rglob('*.json.gz'))
    if not paths:
        return dict(status='awaiting_first_capture', name=cfg['name'])
    if loader is None:
        import gzip
        loader = lambda p: json.load(gzip.open(p, 'rt'))
    row = loader(paths[-1])['research_foundation']
    return {k:v for k,v in row.items() if k not in ('observations', 'entries')}
