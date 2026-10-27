"""Manual LIVE: a frozen daily pocket priority, exact shared predicates, four lines."""
import argparse
import copy
import datetime as dt
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT/'research/selection-lab-20261018'
VERSION = 'live-daily-pockets-20261027-v1'
MSK = dt.timezone(dt.timedelta(hours=3))
POLICY = '.github/live-daily-policy.json'
POINTER = '.github/live-daily-pockets.json'
FAMILIES = {
    'independent_pockets': '.github/live-independent-pockets.json',
    'distinct_pockets': '.github/live-distinct-pockets.json',
    'seven_direction_pockets': '.github/live-seven-direction-pockets.json',
    'refined_pockets': '.github/live-refined-pockets.json',
    'external_pockets': '.github/live-external-pockets.json',
}
FORMAT = dict(version='live-four-lines-20261027-v1',
    header='Live · {time_msk} МСК · {n}/5',
    match_lines=['{number}. {league} · {home} - {away} · {minute}′ · {score}',
                 'ТБ {line} × {price}', '{available_recent_stats_and_missing_data}',
                 'Группа №{group_rank}: «{group_name}».'],
    display_order=['frozen_daily_group_rank', 'fresh_sporting_order'],
    preliminary=None, missing_value='н/д', group_statistics_in_card=False,
    history_in_card=False, rounded_alternative_in_card=False)


def family(name):
    if name not in FAMILIES:
        raise ValueError('Unsupported daily pocket family: '+name)
    if str(LAB) not in sys.path:
        sys.path.append(str(LAB))
    return importlib.import_module(name)


def catalog(root, name):
    raw = (Path(root)/FAMILIES[name]).read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        raise ValueError('Daily pocket family disabled: '+name)
    return cfg, hashlib.sha256(raw).hexdigest()


def freeze(report, root, date_msk, report_path, now=None):
    """One immutable daily decision. Later statistics never replace today's set."""
    root = Path(root)
    dt.date.fromisoformat(date_msk)
    path = root/'.github/live-daily-pockets'/f'{date_msk}.json'
    if path.exists():
        return json.loads(path.read_text())
    report_time=dt.datetime.fromisoformat(report['generated_at_utc'].replace('Z','+00:00'))
    if report_time.tzinfo is None or str(report_time.astimezone(MSK).date()) != date_msk:
        raise ValueError('Daily freeze requires a report from the selected Moscow date')
    tables = report['statistics_tables']
    if tables.get('errors'):
        raise ValueError('Cannot freeze groups from an incomplete report')
    observed = {}
    for section in FAMILIES:
        cfgpath = root/FAMILIES[section]
        if not cfgpath.exists() or not json.loads(cfgpath.read_text()).get('enabled'):
            continue
        cfg, digest = catalog(root,section)
        data = report.get(section) or {}
        if data.get('status') == 'error' or not isinstance(data.get('pockets'),dict):
            raise ValueError('Missing report family: '+section)
        if data.get('catalog_sha256') != digest:
            raise ValueError('Report/catalog mismatch: '+section)
        for p in cfg['pockets']:
            if p.get('market','over') != 'over':
                continue
            if p['id'] not in data['pockets']:
                raise ValueError('Missing active pocket result')
            observed[section+'/'+p['id']] = (p, digest)
    rows = {r['key']: r for r in tables['over']}
    if not observed.keys() <= rows.keys():
        raise ValueError('Missing active pocket table row')
    eligible = [r for key,r in rows.items() if key in observed and r['n'] > 30
                and isinstance(r.get('roi_without_top3'),(int,float))
                and math.isfinite(r['roi_without_top3']) and r['roi_without_top3'] > 0]
    eligible.sort(key=lambda r:(-r['roi_without_top3'],-r['n'],r['key']))
    groups=[]
    for rank,r in enumerate(eligible[:5],1):
        section,key = r['key'].split('/',1)
        p,digest = observed[r['key']]
        groups.append(dict(rank=rank,section=section,pocket_id=key,
            name=re.sub(r'^ТБ\s+', '', p['name']),n=r['n'],roi=r['roi'],
            roi_without_top3=r['roi_without_top3'],catalog_sha256=digest))
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    result = dict(schema_version=1,selection_date_msk=date_msk,frozen_at_utc=now,
                  statistics_report_path=report_path,
                  statistics_generated_at_utc=report['generated_at_utc'],groups=groups)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2);handle.write('\n')
    return result


def load_daily(root=ROOT, now=None):
    root=Path(root)
    policy=json.loads((root/POLICY).read_text())
    if not policy.get('enabled'):
        raise ValueError('Daily LIVE policy is disabled')
    daily=json.loads((root/POINTER).read_text())
    day=dt.date.fromisoformat(daily['selection_date_msk'])
    today=(now or dt.datetime.now(MSK)).astimezone(MSK).date()
    if day > today or daily.get('schema_version') != 1:
        raise ValueError('Invalid daily decision date/schema')
    archive=root/'.github/live-daily-pockets'/f'{day}.json'
    if not archive.exists() or json.loads(archive.read_text()) != daily:
        raise ValueError('Daily pointer differs from immutable archive')
    groups=daily['groups']
    if len(groups)>5 or [g['rank'] for g in groups] != list(range(1,len(groups)+1)):
        raise ValueError('Invalid daily group priority')
    if len({(g['section'],g['pocket_id']) for g in groups}) != len(groups):
        raise ValueError('Duplicate daily group')
    for g in groups:
        cfg,digest=catalog(root,g['section'])
        p=next((p for p in cfg['pockets'] if p['id']==g['pocket_id']),None)
        if (not p or p.get('market','over')!='over' or digest!=g['catalog_sha256']
                or g['n']<=30 or not math.isfinite(g['roi_without_top3']) or g['roi_without_top3']<=0):
            raise ValueError('Frozen daily group no longer matches its approved catalog')
    return daily,policy


def pre_matches(event, group, root=ROOT):
    """Only cheap necessary conditions; never guess an unavailable market feature."""
    cfg,_=catalog(root,group['section'])
    p=next(p for p in cfg['pockets'] if p['id']==group['pocket_id'])
    lo,hi=p.get('minute_window',[5,89])
    if not lo <= event['minute'] <= hi:
        return False
    score=list(map(int,event['score'].split('-')));goals=sum(score)
    c=set(p.get('conditions',[]))
    if c & {'score_one','goals1'} and goals!=1:return False
    if c & {'goals_two','goals2'} and goals!=2:return False
    if 'lead2' in c and abs(score[0]-score[1])<2:return False
    if 'exact_lead2' in c and (goals!=2 or abs(score[0]-score[1])!=2):return False
    if 'score_01' in c and score!=[0,1]:return False
    if group['pocket_id'] in ('draw_60_65','late_draw_shots10') and score[0]!=score[1]:return False
    return True


def sporting_key(event):
    from live_ranking import fresh_sports_features
    f=fresh_sports_features(event);evidence=[]
    for w in (5,10,15):
        on=f.get(f'on_target_{w}_known_total') or 0
        shots=f.get(f'finishing_{w}_known_total') or 0
        xg=f.get(f'supported_xg_{w}') or 0
        da=f.get(f'dangerous_attacks_{w}_known_total') or 0
        if on>0 or xg>0 or w==5 and f.get('sustained_finishing'):
            evidence.append((0,w))
        elif shots>0 and (da>0 or (f.get(f'two_sided_shots_{w}_total') or 0)>0):
            evidence.append((1,w))
        elif f.get('persistent_dangerous_pressure') and (f.get(f'corners_{w}_known_total') or 0)>0:
            evidence.append((2,w))
    tier,w=min(evidence) if evidence else (9,99)
    profile=event.get('goal_profile') or {}
    ratio=profile.get('ranking_ratio',1) if profile.get('ranking_used') else 1
    return (tier,w,-(f.get(f'on_target_{w}_known_per_minute') or 0),
            -(f.get(f'finishing_{w}_known_per_minute') or 0),
            -(f.get(f'two_sided_shots_{w}_total') or 0),
            -(f.get(f'dangerous_attacks_{w}_known_per_minute') or 0),-ratio,str(event['event_id']))


def market_for_event(event,snapshot):
    from distinct_pockets import quote
    q,reason=quote(event,snapshot)
    if q is None or q['over'] is None or q['over']<=1:return None,reason or 'missing_over_price'
    return dict(line=q['line'],price=q['over'],source=q['source'],checked_at=q['checked_at'],
                score=q['score'],market_basis='provider_main_total_exact'),None


def evaluate_pockets(root,snapshot,ordered,history_dir):
    """Reuse every catalog's production evaluator, with manual-only trigger history.

    Research seen IDs and accounting ledgers are never read or modified here.
    Qualified but unpublished candidates remain available on subsequent LIVE calls.
    """
    sections={g['section'] for g in snapshot['daily_pockets']['groups']}
    results={}
    with tempfile.TemporaryDirectory(prefix='live-daily-predicates-') as tmp:
        work=Path(tmp)
        for section in sections:
            mod=family(section)
            p=work/FAMILIES[section];p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes((Path(root)/FAMILIES[section]).read_bytes())
            journal=(mod.JOURNAL if hasattr(mod,'JOURNAL') else
                     'research/live-background/experimental/pocket-receipts')
            for path in sorted(Path(history_dir).glob('*.json')):
                prior=json.loads(path.read_text())
                if prior.get('rules_version')!=VERSION:continue
                age=dt.datetime.fromisoformat(snapshot['board_observed_at_utc'])-dt.datetime.fromisoformat(prior['board_observed_at_utc'])
                if not 0 < age.total_seconds() <= 7200:continue
                item=copy.deepcopy((prior.get('daily_pocket_evaluations') or {}).get(section))
                if not item:continue
                # Preserve satisfied waiting periods even when the candidate lost a slot.
                if section=='seven_direction_pockets':
                    for key,entries in item.get('choices',{}).items():
                        active=item.setdefault('triggers',{}).setdefault(key,[])
                        ids={v['event_id'] for v in active}
                        active.extend(e['trigger'] for e in entries if e['event_id'] not in ids)
                item['choices']={k:[] for k in item.get('choices',{})}
                dest=work/journal/path.name;dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_text(json.dumps({section:item}))
            if section=='independent_pockets':
                result=mod.evaluate(work,snapshot,ordered,market_for_event,{},lambda e,o:None)
            elif section=='refined_pockets':
                result=mod.evaluate(work,snapshot,ordered,market_for_event,{})
            else:result=mod.evaluate(work,snapshot,{})
            if result is None or result.get('status')!='active':
                raise ValueError('Daily pocket evaluation unavailable: '+section)
            results[section]=result
    return results


def choose(events,groups,evaluations):
    by_id={str(e['event_id']):e for e in events};selected=[];seen=set()
    for group in groups:
        choices=evaluations[group['section']]['choices'][group['pocket_id']]
        matches=[by_id[str(c['event_id'])] for c in choices if str(c['event_id']) in by_id and str(c['event_id']) not in seen]
        for event in sorted(matches,key=sporting_key):
            event['daily_group']=copy.deepcopy(group)
            selected.append(event);seen.add(str(event['event_id']))
            if len(selected)==5:return selected
    return selected


class Budget:
    def __init__(self,get,cap):self.get=get;self.cap=cap;self.calls=0;self.exhausted=False;self.lock=threading.Lock()
    def __call__(self,*args,**kwargs):
        with self.lock:
            if self.calls>=self.cap:
                self.exhausted=True
                raise RuntimeError('Manual LIVE API budget exhausted')
            self.calls+=1
        return self.get(*args,**kwargs)


def collect(base,mode,requested_ids,history_dir,selected_file,root=ROOT):
    from live_ranking import rank_candidates,rank_final_candidates,fresh_sports_features
    from live_history import read_selected_events
    from concurrent.futures import ThreadPoolExecutor
    daily,policy=load_daily(root)
    # Load original shared predicates before resolving market_for_event's imports.
    for section in {g['section'] for g in daily['groups']}:family(section)
    family('distinct_pockets')
    published,warnings=read_selected_events(selected_file)
    if warnings:raise ValueError('Cannot read published LIVE exclusions')
    original=base.get;budget=Budget(original,int(policy['api_call_cap']));base.get=budget
    started=time.monotonic()
    try:
        board,http=base.get('/v3/events/inplay',sport_id=1)
        if board.get('success') not in (1,True) or not isinstance(board.get('results'),list):
            raise RuntimeError('Live board unavailable')
        clock=dt.datetime.now(dt.timezone.utc).isoformat();events=[];seen=set();skipped=[]
        for row in board['results']:
            if not isinstance(row,dict):continue
            eid=str(row.get('id') or '');minute=base.integer((row.get('timer') or {}).get('tm'))
            score=str(row.get('ss') or '').replace(':','-')
            league,home,away=[base.name(row.get(k)) or '' for k in ('league','home','away')]
            if (not eid or eid in seen or base.EXCLUDE.search(' '.join([league,home,away]))
                    or minute is None or not 5<=minute<=89 or not re.fullmatch(r'\d+-\d+',score)):
                continue
            seen.add(eid)
            if mode=='refresh' and eid not in requested_ids:continue
            if mode=='full' and eid in published:skipped.append(eid);continue
            events.append(dict(event_id=eid,minute=minute,score=score,league=league,home=home,away=away,
                home_id=str((row.get('home') or {}).get('id') or ''),away_id=str((row.get('away') or {}).get('id') or ''),
                league_id=str((row.get('league') or {}).get('id') or ''),start_time=row.get('time'),
                country=(row.get('league') or {}).get('cc'),category=base.category(' '.join([league,home,away])),
                board_stats=row.get('stats') or {},timer=row.get('timer'),time_status=row.get('time_status'),
                provider_board_row=row,board_observed_at_utc=clock,events_main_total=None,final_sports_selected=False))
        ranked=rank_candidates(events)
        events=ranked
        candidates=[]
        for e in ranked:
            ranks=[g['rank'] for g in daily['groups'] if pre_matches(e,g,root)]
            if ranks:e['daily_candidate_priority']=min(ranks);candidates.append(e)
        candidates.sort(key=lambda e:(e['daily_candidate_priority'],e['preliminary_selection']['rank']))
        review=candidates[:int(policy['deep_candidate_limit'])]
        base.enrich_events(review)
        def odds(e):
            try:
                payload,meta=base.get('/v2/event/odds',event_id=e['event_id'],source='bet365')
                e.update(base.current_main_total(payload,e,int(time.time())))
                e['lab_odds_payload']=payload;e['odds_http']=meta
            except Exception as exc:
                e.update(events_main_total=None,main_total_status='unavailable',main_total_reason=type(exc).__name__)
        with ThreadPoolExecutor(max_workers=base.MAX_WORKERS) as pool:list(pool.map(odds,review))
        strong_groups=[]
        for g in daily['groups']:
            cfg,_=catalog(root,g['section']);p=next(p for p in cfg['pockets'] if p['id']==g['pocket_id'])
            if g['section']=='independent_pockets' or p.get('pool')=='strong':strong_groups.append(g)
        need_profiles=[e for e in review if e.get('events_main_total') and any(pre_matches(e,g,root) for g in strong_groups)]
        before_profiles=budget.calls
        profile_meta=base.enrich_profiles(need_profiles,history_dir) if need_profiles else {}
        profile_meta['goal_profile_call_attempts']=profile_meta.get('goal_profile_api_calls',0)
        profile_meta['goal_profile_api_calls']=budget.calls-before_profiles
        for e in review:e['research_sports_features']=fresh_sports_features(e)
        remaining=list(review);ordered=[]
        while remaining:
            batch=rank_final_candidates(remaining,limit=5)
            if not batch:break
            ordered.extend(batch);ids={e['event_id'] for e in batch};remaining=[e for e in remaining if e['event_id'] not in ids]
        sid=os.environ.get('GITHUB_RUN_ID',str(time.time_ns()))+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
        snapshot=dict(schema_version=5,rules_version=VERSION,ranking_method='frozen_daily_group_then_fresh_sporting',
            final_ranking_method='frozen_daily_group_then_fresh_sporting',snapshot_id=sid,code_revision=os.environ.get('GITHUB_SHA'),
            board_observed_at_utc=clock,generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
            odds_checked_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),mode=mode,requested_event_ids=requested_ids,
            daily_pockets=daily,events=events,source='BetsAPI Events API',board_http=http,response_format=FORMAT,
            raw_live_count=len(board['results']),eligible_in_window_count=len(events),
            previously_selected_skipped_event_ids=skipped,previously_selected_skipped_count=len(skipped),
            candidate_count=len(candidates),reviewed_count=len(review),
            unreviewed_event_ids=[e['event_id'] for e in candidates[len(review):]],
            budget_exhausted=budget.exhausted,total_api_calls=budget.calls,api_call_cap=budget.cap,
            stats_trend_calls=len(review),detail_calls=len(review),**profile_meta,
            timing_seconds=dict(collection=round(time.monotonic()-started,3)),stage='daily_pockets_ready')
        evaluations=evaluate_pockets(root,snapshot,ordered,history_dir)
        selected=choose(events,daily['groups'],evaluations)
        for rank,e in enumerate(selected,1):
            e['final_sports_selected']=True
            e['final_sports_selection']=dict(sports_rank=rank,display_rank=rank,method=snapshot['ranking_method'],
                daily_group_rank=e['daily_group']['rank'],confidence=e.get('selection_confidence'),
                features=e.get('research_sports_features'))
        snapshot.update(daily_pocket_evaluations=evaluations,
            final_sports_selection_event_ids=[e['event_id'] for e in selected],
            display_event_ids=[e['event_id'] for e in selected],final_sports_selection_count=len(selected),
            final_sports_selection_limit=5,events_main_total_count=sum(bool(e.get('events_main_total')) for e in review))
        snapshot['live_markdown']=render(snapshot)
        return snapshot
    finally:base.get=original


def render(snapshot):
    from live_ru import russian_name
    def clean(s):return str(s).replace('\n',' ').replace('\r',' ').replace('|','/').replace('*','')
    def num(v):
        from live_enrichment import number
        v=number(v)
        return 'н/д' if v is None or not math.isfinite(v) else format(v,'g').replace('.',',')
    def price(v):
        s=num(v)
        if s=='н/д':return s
        a,_,b=s.partition(',')
        return a+','+b.ljust(2,'0')
    def pair_text(v):return ':'.join(num(x) for x in v) if isinstance(v,(list,tuple)) and len(v)==2 else 'н/д'
    by_id={e['event_id']:e for e in snapshot['events']}
    selected=[by_id[eid] for eid in snapshot['display_event_ids']]
    clock=dt.datetime.fromisoformat(snapshot['board_observed_at_utc']).astimezone(MSK)
    lines=[f"**Live · {clock:%H:%M} МСК · {len(selected)}/5**",'']
    for rank,e in enumerate(selected,1):
        q=e['events_main_total'];g=e['daily_group'];f=e.get('research_sports_features') or {};stats=e.get('board_stats') or {}
        trend=e.get('stats_trend') or {};w=(trend.get('current_windows',trend.get('windows',{}))).get('5',{})
        lag=(trend.get('time_context') or {}).get('board_minus_trend_minutes')
        if (trend.get('available') and isinstance(lag,(int,float)) and 0<=lag<=2
                and w.get('actual_duration_minutes') and w.get('to_minute') is not None):
            fresh=f"За {num(w.get('actual_duration_minutes'))}′: удары {pair_text(f.get('shots_5_home_away'))}, створы {pair_text(f.get('on_target_5_home_away'))}"
        else:fresh='динамика н/д'
        red=stats.get('redcards');reds='удалений нет' if isinstance(red,(list,tuple)) and len(red)==2 and all(str(v) in ('0','0.0') for v in red) else 'КК '+pair_text(red)
        lines.extend([f"**{rank}. {clean(russian_name(e['league'],league=True))} · {clean(russian_name(e['home']))} - {clean(russian_name(e['away']))} · {num(e['minute'])}′ · {e['score'].replace('-',':')}**  ",
            f"**ТБ {num(q['line'])} × {price(q['over'])}**  ",
            f"{fresh}; xG {pair_text(stats.get('xg'))}; {reds}.  ",
            f"**Группа №{g['rank']}: «{clean(g['name'])}».**",''])
    if not selected:lines.extend(['В проверенной части пула подходящих матчей нет.',''])
    if snapshot.get('unreviewed_event_ids') or snapshot.get('budget_exhausted'):
        lines.append(f"Покрытие ограничено: проверено {snapshot['reviewed_count']} из {snapshot['candidate_count']} кандидатов; лимит запросов {snapshot['api_call_cap']}.")
    day=snapshot['daily_pockets']['selection_date_msk']
    if day < str((clock-dt.timedelta(hours=6)).date()):lines.append(f'Используются группы от {day}: новая утренняя фиксация отсутствует.')
    return '\n'.join(lines).strip()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--freeze-report',required=True);parser.add_argument('--report-path',required=True)
    parser.add_argument('--date',required=True);parser.add_argument('--root',default=str(ROOT))
    args=parser.parse_args()
    root=Path(args.root)
    result=freeze(json.loads(Path(args.freeze_report).read_text()),root,args.date,args.report_path)
    pointer=root/POINTER
    if pointer.exists() and json.loads(pointer.read_text())['selection_date_msk']>args.date:
        raise ValueError('Cannot move daily pointer backwards')
    pointer.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
