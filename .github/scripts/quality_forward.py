"""Frozen, prospective price-aware comparison. Never changes parent selections."""
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

VERSION = 'remaining-goals-quality-v1-20261024'
EXPERIMENT = 'quality-forward-v1-20261024'
MODEL_PATH = 'research/selection-quality/models/remaining-goals-quality-v1-20261024.json'
FEATURES = ['intercept', 'goals', 'score_difference', 'draw', 'sot_rate',
            'fresh_sot', 'fresh_finishing', 'danger_rate', 'goal_profile',
            'redcards', 'missing_danger', 'missing_profile', 'missing_redcards']
PROTOCOL = dict(parent_epoch_id='shared-top5-forward-20261022-v2',
                opportunity='original_parent_base_entry_once_per_new_match',
                candidate_ev_min=.05, refill=False, retrain=False,
                minimum_settled=500, minimum_entry_days=7, minimum_candidate_settled=100,
                minimum_bin_n=50, calibration_relative_error_max=.10,
                probability_comparison_minimum_fraction=.80,
                automatic_promotion=False)
EDGES = [-.05, 0., .05, .10, .20]
MSK = dt.timezone(dt.timedelta(hours=3))


def num(x):
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def stamp(x):
    t = dt.datetime.fromisoformat(str(x).replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('Clock requires timezone')
    return t.timestamp()


def read(path):
    p = Path(path)
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_bytes())


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def vector(r):
    """Missing optional evidence has its own flag; required evidence is never zero-filled."""
    minute = num(r.get('minute'))
    score = r.get('score')
    if not minute or not 8 <= minute <= 60 or not isinstance(score, list) or len(score) != 2:
        raise ValueError('Invalid first-entry clock or score')
    if any(num(x) is None or x < 0 or int(x) != x for x in score):
        raise ValueError('Invalid score')
    required = [num(r.get(k)) for k in ('sot', 'fresh_sot5', 'fresh_shots5')]
    if any(v is None or v < 0 for v in required):
        raise ValueError('Required finishing evidence missing')
    sot, fresh_sot, fresh_shots = required
    danger, profile, reds = [num(r.get(k)) for k in ('danger', 'gp_ratio', 'redcards')]
    if any(x is not None and x < 0 for x in (danger, profile, reds)):
        raise ValueError('Negative optional evidence')
    return [1., min(6, sum(score)) / 3, min(4, abs(score[0]-score[1])) / 3,
            float(score[0] == score[1]), min(3, sot/minute/.2),
            min(3, fresh_sot/2), min(3, fresh_shots/5),
            min(3, danger/minute/2) if danger is not None else 0.,
            max(-1, min(1, profile-1)) if profile is not None else 0.,
            min(3, reds) if reds is not None else 0.,
            float(danger is None), float(profile is None), float(reds is None)]


def exposure(r):
    return (90-float(r['minute']))/45


def solve(a, b):
    a = [list(row)+[v] for row, v in zip(a, b)]
    n = len(b)
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(a[j][i]))
        a[i], a[pivot] = a[pivot], a[i]
        if abs(a[i][i]) < 1e-12:
            raise ValueError('Singular count model')
        q = a[i][i]
        a[i] = [v/q for v in a[i]]
        for j in range(n):
            if j != i:
                q = a[j][i]
                a[j] = [u-q*v for u, v in zip(a[j], a[i])]
    return [row[-1] for row in a]


def predict(r, model):
    value = sum(a*b for a, b in zip(vector(r), model['beta'])) + math.log(exposure(r))
    return max(.01, min(15., math.exp(max(-12, min(8, value)))*model.get('scale', 1.)))


def fit(rows):
    n = len(FEATURES)
    beta = [math.log(max(.01, sum(r['additional'] for r in rows)/sum(exposure(r) for r in rows)))] + [0.]*(n-1)
    for _ in range(40):
        h = [[0.]*n for _ in range(n)]
        g = [0.]*n
        for r in rows:
            x = vector(r)
            mu = math.exp(max(-12, min(8, sum(a*b for a, b in zip(x, beta))+math.log(exposure(r)))))
            for i in range(n):
                g[i] += x[i]*(r['additional']-mu)
                for j in range(n):
                    h[i][j] += mu*x[i]*x[j]
        for i in range(1, n):
            h[i][i] += 10.
            g[i] -= 10.*beta[i]
        h[0][0] += 1e-8
        delta = solve(h, g)
        shrink = max(1., max(abs(x) for x in delta))
        beta = [b+d/shrink for b, d in zip(beta, delta)]
        if max(abs(x) for x in delta) < 1e-7:
            break
    return dict(beta=beta, scale=1.)


def chronological_split(rows):
    """One match once, outcome must already have been confirmed before the next block."""
    rows = sorted(rows, key=lambda r: (r['t'], r['id']))
    if len({r['id'] for r in rows}) != len(rows) or len(rows) < 300:
        raise ValueError('At least 300 unique matches required')
    a, b = int(len(rows)*.6), int(len(rows)*.8)
    cal_start, test_start = rows[a]['t'], rows[b]['t']
    train = [r for r in rows[:a] if r['confirmed_at'] < cal_start]
    calibration = [r for r in rows[a:b] if r['confirmed_at'] < test_start]
    validation = rows[b:]
    if len(train) < 150 or len(calibration) < 30 or len(validation) < 50:
        raise ValueError('Insufficient purged chronological blocks')
    return train, calibration, validation


def payoff(total, line, price):
    if num(line) is None or num(price) is None or price <= 1 or not 0 <= line <= 30 or abs(line*4-round(line*4)) > 1e-8:
        raise ValueError('Invalid original market')
    legs = [line-.25, line+.25] if round(line*4) % 2 else [line]
    return sum(price-1 if total > x else 0. if total == x else -1. for x in legs)/len(legs)


def distribution(lam, goals, line, price):
    if num(lam) is None or not 0 <= lam <= 15:
        raise ValueError('Invalid remaining-goals mean')
    weights = dict(full_win=0., half_win=0., push=0., half_loss=0., loss=0.)
    p, total = math.exp(-lam), 0.
    for n in range(80):
        v = payoff(goals+n, line, price)
        key = 'full_win' if abs(v-(price-1)) < 1e-10 else 'half_win' if v > 0 else 'push' if v == 0 else 'half_loss' if v > -1 else 'loss'
        weights[key] += p
        total += p
        p *= lam/(n+1)
    weights['full_win'] += max(0., 1-total)
    ev = (price-1)*(weights['full_win']+.5*weights['half_win']) - weights['loss']-.5*weights['half_loss']
    return dict(probabilities=weights, expected_profit=ev)


def nll(y, lam):
    return lam-y*math.log(max(lam, 1e-12))+math.lgamma(y+1)


def calibration_metrics(rows, model):
    if not rows:
        return dict(n=0)
    predictions = [predict(r, model) for r in rows]
    return dict(n=len(rows), observed_mean=statistics.mean(r['additional'] for r in rows),
                predicted_mean=statistics.mean(predictions),
                poisson_nll=statistics.mean(nll(r['additional'], p) for r, p in zip(rows, predictions)))


def train_model(rows, source):
    train, calibration, validation = chronological_split(rows)
    model = fit(train)
    before = calibration_metrics(calibration, model)
    factor = sum(r['additional'] for r in calibration)/sum(predict(r, model) for r in calibration)
    model['scale'] = max(.5, min(2., factor))
    return dict(version=VERSION, experiment_id=EXPERIMENT, features=FEATURES, protocol=PROTOCOL,
                model=model, source=source,
                created_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                split_ids={k:[r['id'] for r in a] for k, a in [('train',train), ('calibration',calibration), ('validation',validation)]},
                split_windows={k:dict(n=len(a),first_entry=min(r['t'] for r in a),last_entry=max(r['t'] for r in a),last_confirmed=max(r['confirmed_at'] for r in a)) for k,a in [('train',train),('calibration',calibration),('validation',validation)]},
                calibration_before=before, calibration_after=calibration_metrics(calibration, model),
                historical_validation=calibration_metrics(validation, model),
                historical_validation_is_forward_evidence=False)


def event_features(e):
    board = e.get('board_stats') or {}
    def total(k):
        a = board.get(k)
        values=[num(x) for x in a] if isinstance(a,list) and len(a)==2 else []
        return sum(values) if len(values)==2 and all(x is not None and x>=0 for x in values) else None
    f = (e.get('final_sports_selection') or {}).get('features') or e.get('research_sports_features') or {}
    return dict(minute=e['minute'], score=[int(x) for x in e['score'].split('-')],
                sot=total('on_target'), danger=total('dangerous_attacks'), redcards=total('redcards'),
                gp_ratio=(e.get('goal_profile') or {}).get('ranking_ratio'),
                fresh_sot5=f.get('on_target_5_total'), fresh_shots5=f.get('finishing_5_total'))


def load_config(root):
    p = Path(root)/'.github/live-quality-experiment.json'
    if not p.exists():
        return None
    cfg = read(p)
    if not cfg.get('enabled'):
        return None
    if cfg.get('version') != VERSION or cfg.get('id') != EXPERIMENT or cfg.get('protocol') != PROTOCOL or cfg.get('model_path') != MODEL_PATH:
        raise ValueError('Unversioned quality protocol change')
    return cfg


def load_model(root, cfg):
    p = Path(root)/cfg['model_path']
    if hashlib.sha256(p.read_bytes()).hexdigest() != cfg['model_sha256']:
        raise ValueError('Frozen model digest mismatch')
    model = read(p)
    if model['version'] != VERSION or model['protocol'] != PROTOCOL or model['features'] != FEATURES:
        raise ValueError('Frozen model schema mismatch')
    return model


def prior_state(receipts, cfg):
    records = [r.get('quality_experiment') for r in receipts if not r.get('failed')]
    states = [r for r in records if r and r.get('id') == cfg['id'] and r.get('status') == 'active']
    if not states:
        return None
    state = max(states, key=lambda x: stamp(x['observed_at_utc']))
    if state.get('model_sha256') != cfg['model_sha256']:
        raise ValueError('Model changed inside a forward experiment')
    return state


def summary(entries, outcomes):
    rows = []
    for entry in entries:
        if entry.get('status') != 'priced':
            continue
        outcome = outcomes.get(entry['event_id']) or {}
        final = outcome.get('final')
        if not outcome.get('settled') or outcome.get('result_conflict') or not isinstance(final, list) or len(final) != 2 or any(num(x) is None or x < y or int(x) != x for x, y in zip(final, entry['score'])):
            continue
        row = dict(entry)
        row['additional'] = sum(final)-sum(entry['score'])
        row['profit'] = payoff(sum(final), entry['market']['line'], entry['market']['price'])
        row['full_win'] = float(abs(row['profit']-(entry['market']['price']-1)) < 1e-9)
        rows.append(row)
    def metrics(a):
        values = [r['profit'] for r in a]
        return dict(n=len(a), unit_profit=math.fsum(values), roi=statistics.mean(values) if values else None,
                    without_top3_roi=statistics.mean(sorted(values)[:-3]) if len(values)>3 else None)
    accepted = [r for r in rows if r['candidate_accepted']]
    paired_legacy = [r for r in rows if r.get('legacy_distribution')]
    bins = []
    for i in range(len(EDGES)+1):
        group = [r for r in rows if sum(r['candidate_distribution']['expected_profit'] >= x for x in EDGES) == i]
        bins.append(dict(index=i, lower=EDGES[i-1] if i else None, upper=EDGES[i] if i<len(EDGES) else None,
                         n=len(group), predicted_roi=statistics.mean(r['candidate_distribution']['expected_profit'] for r in group) if group else None,
                         realized_roi=statistics.mean(r['profit'] for r in group) if group else None))
    reliable = [b for b in bins if b['n'] >= PROTOCOL['minimum_bin_n']]
    monotone = all(b['realized_roi'] >= a['realized_roi'] for a,b in zip(reliable,reliable[1:])) if len(reliable)>=2 else None
    dates = sorted({r['entry_day_msk'] for r in rows})
    candidate_nll = statistics.mean(nll(r['additional'], r['candidate_lambda']) for r in rows) if rows else None
    legacy_nll = statistics.mean(nll(r['additional'], r['legacy_lambda']) for r in paired_legacy) if paired_legacy else None
    same_candidate_nll = statistics.mean(nll(r['additional'], r['candidate_lambda']) for r in paired_legacy) if paired_legacy else None
    brier = lambda name: statistics.mean((r[name]['probabilities']['full_win']-r['full_win'])**2 for r in paired_legacy) if paired_legacy else None
    predicted = math.fsum(r['candidate_lambda'] for r in rows)
    observed = sum(r['additional'] for r in rows)
    relative_error = abs(predicted-observed)/max(1, observed) if rows else None
    delta = [r['profit'] if r['candidate_accepted'] else 0. for r in rows]
    differences = [p-r['profit'] for p,r in zip(delta,rows)]
    ci = None
    if len(dates) >= 7:
        byday = {d:[v for r,v in zip(rows,differences) if r['entry_day_msk']==d] for d in dates}
        rng = random.Random(20261024)
        means=[]
        for _ in range(2000):
            values=[v for d in rng.choices(dates,k=len(dates)) for v in byday[d]]
            means.append(statistics.mean(values))
        means.sort(); ci=[means[50],means[1949]]
    tests = dict(complete_required_features=all(r.get('status')!='feature_unavailable' for r in entries),
                 enough_matches=len(rows)>=500, enough_days=len(dates)>=7,
                 enough_candidate_matches=len(accepted)>=100,
                 positive_candidate_roi=bool(accepted) and metrics(accepted)['roi']>0,
                 positive_candidate_without_top3=len(accepted)>3 and metrics(accepted)['without_top3_roi']>0,
                 calibrated_mean=relative_error is not None and relative_error<=.10,
                 comparable_probability_sample=len(rows)>0 and len(paired_legacy)>=.8*len(rows),
                 improved_count_log_score=same_candidate_nll is not None and same_candidate_nll<legacy_nll,
                 improved_full_win_brier=brier('candidate_distribution') is not None and brier('candidate_distribution')<brier('legacy_distribution'),
                 quality_order_confirmed=monotone is True,
                 positive_day_cluster_delta=ci is not None and ci[0]>0)
    return dict(selected=len(entries), priced=sum(r.get('status')=='priced' for r in entries),
                unpriced=sum(r.get('status')=='unpriced' for r in entries),
                feature_unavailable=sum(r.get('status')=='feature_unavailable' for r in entries),
                benchmark=metrics(rows), candidate=metrics(accepted), entry_days=len(dates),
                predicted_remaining=predicted, observed_remaining=observed, remaining_relative_error=relative_error,
                candidate_poisson_nll=candidate_nll, paired_probability_n=len(paired_legacy),
                candidate_paired_nll=same_candidate_nll, legacy_paired_nll=legacy_nll,
                candidate_full_win_brier=brier('candidate_distribution'), legacy_full_win_brier=brier('legacy_distribution'),
                expected_profit_bins=bins, quality_monotone=monotone,
                candidate_profit_per_benchmark_opportunity=statistics.mean(delta) if rows else None,
                paired_difference_per_opportunity=statistics.mean(differences) if rows else None,
                paired_difference_day_cluster_ci95=ci, criteria=tests,
                evidence_ready=all(tests.values()), automatic_promotion=False)


def evaluate(snapshot, base_choices, receipts, old_targets, outcomes, cfg, model):
    prior = prior_state(receipts, cfg)
    observed = snapshot['board_observed_at_utc']
    parent=snapshot.get('statistics_epoch')
    if parent and parent.get('id')!=PROTOCOL['parent_epoch_id']:
        raise ValueError('Parent selection version changed')
    if stamp(model['created_at_utc']) >= stamp(observed):
        raise ValueError('Model must precede prospective observations')
    if prior:
        state = {k:v for k,v in prior.items() if k != 'progress'}
        state['entries'] = list(prior['entries'])
    else:
        excluded = set(map(str, old_targets))
        excluded.update(eid for ids in model['split_ids'].values() for eid in ids)
        state = dict(id=cfg['id'], version=VERSION, status='active', model_sha256=cfg['model_sha256'],
                     started_at_utc=observed, entries=[], excluded_event_ids=sorted(excluded), protocol=PROTOCOL)
    excluded = set(state['excluded_event_ids'])
    seen = {r['event_id'] for r in state['entries']}
    byid = {str(e['event_id']):e for e in snapshot['events']}
    for base in base_choices:
        eid = str(base['event_id'])
        if eid in excluded or eid in seen:
            continue
        seen.add(eid)
        entry = dict(event_id=eid, entry_at_utc=observed,
                     entry_day_msk=dt.datetime.fromtimestamp(stamp(observed),MSK).date().isoformat(),
                     source_snapshot_id=snapshot['snapshot_id'], sports_rank=base['sports_rank'],
                     minute=base['minute'], score=[int(x) for x in base['score'].split('-')],
                     market=base.get('market'), candidate_accepted=False)
        if not entry['market']:
            entry['status']='unpriced'
        else:
            try:
                r = event_features(byid[eid]); x = vector(r)
                lam = predict(r, model['model'])
                q = entry['market']; predicted = distribution(lam,sum(entry['score']),q['line'],q['price'])
                legacy = num(base.get('model_lambda'))
                legacy = legacy if legacy is not None and 0<=legacy<=15 else None
                entry.update(status='priced', features=x, candidate_lambda=lam,
                             candidate_distribution=predicted, legacy_lambda=legacy,
                             legacy_distribution=distribution(legacy,sum(entry['score']),q['line'],q['price']) if legacy is not None else None,
                             candidate_accepted=predicted['expected_profit']>=PROTOCOL['candidate_ev_min'])
            except (ValueError,KeyError,TypeError,OverflowError) as exc:
                entry.update(status='feature_unavailable', reason=type(exc).__name__)
        state['entries'].append(entry)
    state['observed_at_utc']=observed
    state['progress']=summary(state['entries'],outcomes)
    return state


def report_from_receipts(root, receipts, outcomes):
    cfg=load_config(root)
    if not cfg:
        return None
    state=prior_state(receipts,cfg)
    if not state:
        return dict(id=cfg['id'],version=VERSION,status='awaiting_first_successful_capture')
    errors=[r.get('quality_experiment') for r in receipts if not r.get('failed')
            and (r.get('quality_experiment')or{}).get('id')==cfg['id']
            and (r.get('quality_experiment')or{}).get('status')=='error'
            and stamp((r.get('quality_experiment')or{})['observed_at_utc'])>=stamp(state['started_at_utc'])]
    progress=summary(state['entries'],outcomes)
    progress['integrity_error_captures']=len(errors)
    progress['criteria']['complete_capture_history']=not errors
    progress['evidence_ready']=all(progress['criteria'].values())
    return {k:state[k] for k in ('id','version','status','model_sha256','started_at_utc','protocol')}|dict(progress=progress)


def capture_evaluation(root, data, snapshot, base_choices, old_targets, outcomes):
    cfg = load_config(root)
    if not cfg:
        return None
    model = load_model(root,cfg)
    # Descending UTC-date/capture IDs; only this background contour owns the experiment.
    recent_errors=[]
    def receipts():
        for path in sorted((Path(data)/'receipts').rglob('*.json*'),reverse=True):
            record=read(path)
            quality=record.get('quality_experiment')
            if not record.get('failed') and quality and quality.get('id')==cfg['id'] and quality.get('status')=='error':
                recent_errors.append(quality)
            if not record.get('failed') and quality and quality.get('id')==cfg['id'] and quality.get('status')=='active':
                yield record
                return
    state=evaluate(snapshot,base_choices,receipts(),old_targets,outcomes,cfg,model)
    count=state.get('integrity_error_captures',0)
    count+=sum(stamp(r['observed_at_utc'])>=stamp(state['started_at_utc']) for r in recent_errors)
    state['integrity_error_captures']=count
    state['progress']['integrity_error_captures']=count
    state['progress']['criteria']['complete_capture_history']=not count
    state['progress']['evidence_ready']=all(state['progress']['criteria'].values())
    return state
