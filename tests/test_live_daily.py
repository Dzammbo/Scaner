import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import contextlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import live_daily as d
import live_snapshot as live
from live_selection import record_choices
import test_live_ranking as ranking_fixtures


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'.github').mkdir()
        for path in list(d.FAMILIES.values())+[d.POLICY]:
            (self.root/path).write_bytes((ROOT/path).read_bytes())
        self.history=self.root/'snapshots/live';self.history.mkdir(parents=True)
        self.ledger=self.root/'.github/live-selected-events.json'
        self.ledger.write_text('{"schema_version":1,"events":[],"selections":[]}')
        self.report=dict(generated_at_utc='2026-10-27T09:52:53+00:00',statistics_tables=dict(over=[],errors=[]))
        for section,path in d.FAMILIES.items():
            cfg,digest=d.catalog(self.root,section)
            pockets={p['id']:dict(n=0) for p in cfg['pockets'] if p.get('market','over')=='over'}
            self.report[section]=dict(status='active',catalog_sha256=digest,pockets=pockets)
            self.report['statistics_tables']['over'].extend(dict(key=section+'/'+key,n=0,roi=None,roi_without_top3=None) for key in pockets)
        self.setrow('distinct_pockets/tb_45_59_lead2_exact2',137,.183)
        for key,n,robust in [('need_four_plus',163,.11125),('combo_sot_score1',171,.10007),
                             ('provider_main_danger_rate_lt1',329,.08144),('combo_minute_integer',122,.05882)]:
            self.setrow('independent_pockets/'+key,n,robust)
        self.report['statistics_tables']['over'].append(dict(key='current/control',n=1000,roi=2,roi_without_top3=1))
        self.setrow('refined_pockets/lead2_line35',3,1.025)
        self.daily=d.freeze(self.report,self.root,'2026-10-27','report.json',now='2026-10-27T10:00:00+00:00')
        (self.root/d.POINTER).write_text(json.dumps(self.daily))

    def tearDown(self):self.tmp.cleanup()

    def setrow(self,key,n,robust):
        row=next(r for r in self.report['statistics_tables']['over'] if r['key']==key)
        row.update(n=n,roi=robust+.01,roi_without_top3=robust)

    def event(self,eid,score='1-0',minute=53,line=4.5,second=0):
        e=ranking_fixtures.RankingTests().trend_event(str(eid));e.update(score=score,minute=minute,league='Test',home='A',away='B')
        e['board_stats'].update(goals=list(map(int,score.split('-'))),on_target=[4,3],off_target=[5,4],dangerous_attacks=[15,15])
        ts=1793094000+second
        e['events_main_total']=dict(line=line,over=1.9,under=1.9,source='events_odds_main',quote_score=score,market_checked_at=ts)
        e['research_sports_features']=live.rank_final_candidates.__globals__['fresh_sports_features'](e)
        return e

    def snapshot(self,events,second=0,groups=None):
        clock=dt.datetime.fromtimestamp(1793094000+second,dt.timezone.utc).isoformat()
        return dict(snapshot_id='100-'+str(second+1),rules_version=d.VERSION,
                    board_observed_at_utc=clock,generated_at_utc=clock,odds_checked_at_utc=clock,
                    daily_pockets=dict(self.daily,groups=groups or self.daily['groups']),events=events)

    def test_daily_freeze_excludes_small_control_and_never_reranks_intraday(self):
        self.assertEqual([g['pocket_id'] for g in self.daily['groups']],
            ['tb_45_59_lead2_exact2','need_four_plus','combo_sot_score1','provider_main_danger_rate_lt1','combo_minute_integer'])
        self.setrow('refined_pockets/lead2_line35',100,10)
        self.assertEqual(d.freeze(self.report,self.root,'2026-10-27','new.json'),self.daily)
        self.report['generated_at_utc']='2026-10-28T03:05:00+00:00'
        tomorrow=d.freeze(self.report,self.root,'2026-10-28','new.json')
        self.assertEqual(tomorrow['groups'][0]['pocket_id'],'lead2_line35')
        self.report['generated_at_utc']='2026-10-29T03:05:00+00:00'
        self.report['statistics_tables']['errors']=['missing']
        with self.assertRaises(ValueError):d.freeze(self.report,self.root,'2026-10-29','bad.json')

    def test_frozen_archive_and_catalog_are_validated(self):
        self.assertEqual(d.load_daily(self.root,dt.datetime(2026,10,27,12,tzinfo=d.MSK))[0],self.daily)
        cfg=self.root/d.FAMILIES['distinct_pockets'];cfg.write_text(cfg.read_text()+' ')
        with self.assertRaises(ValueError):d.load_daily(self.root,dt.datetime(2026,10,27,12,tzinfo=d.MSK))

    def test_actual_catalog_priority_and_overlap_without_per_group_quota(self):
        events=[self.event(i,'2-0',line=3.5) for i in (1,2)]
        events += [self.event(i,line=4.5) for i in (3,4,5)]
        events += [self.event(i,line=2.5) for i in (6,7)]
        snapshot=self.snapshot(events)
        # The actual strong-v8 admission still controls independent pockets.
        ordered=[];remaining=list(events)
        while remaining:
            batch=live.rank_final_candidates(remaining)
            if not batch:break
            ordered.extend(batch);ids={e['event_id'] for e in batch};remaining=[e for e in remaining if e['event_id'] not in ids]
        evaluations=d.evaluate_pockets(self.root,snapshot,ordered,self.history)
        final=d.choose(events,self.daily['groups'],evaluations)
        self.assertEqual([e['daily_group']['rank'] for e in final],[1,1,2,2,2])
        self.assertEqual(len({e['event_id'] for e in final}),5)
        all_first=[self.event(i,'2-0',line=3.5) for i in range(10,17)]
        snap=self.snapshot(all_first)
        evaluated=d.evaluate_pockets(self.root,snap,[],self.history)
        selected=d.choose(all_first,self.daily['groups'],evaluated)
        self.assertEqual(len(selected),5)
        self.assertEqual({e['daily_group']['rank'] for e in selected},{1})

    def test_missing_quote_cannot_occupy_slot_and_broad_group_does_not_require_strong(self):
        events=[self.event(1,'2-0',line=3.5),self.event(2,'2-0',line=3.5)]
        events[0]['events_main_total']['quote_score']='1-0'
        evaluated=d.evaluate_pockets(self.root,self.snapshot(events),[],self.history)
        selected=d.choose(events,self.daily['groups'],evaluated)
        self.assertEqual([e['event_id'] for e in selected],['2'])
        for key,entries in evaluated['independent_pockets']['choices'].items():self.assertEqual(entries,[])

    def test_delayed_group_reuses_manual_trigger_and_current_price_without_seen_leakage(self):
        group=dict(rank=1,section='distinct_pockets',pocket_id='tb_45_49_goal1_gap125_150_wait5',name='wait')
        first=self.snapshot([self.event(1,minute=45,line=2.5)],groups=[group])
        first['daily_pocket_evaluations']=d.evaluate_pockets(self.root,first,[],self.history)
        (self.history/'first.json').write_text(json.dumps(first))
        for seconds,expected in [(299,0),(300,1),(360,1)]:
            e=self.event(1,minute=49,line=2.25,second=seconds)
            snap=self.snapshot([e],second=seconds,groups=[group])
            result=d.evaluate_pockets(self.root,snap,[],self.history)
            chosen=d.choose([e],[group],result)
            self.assertEqual(len(chosen),expected)
            if expected:self.assertEqual(chosen[0]['events_main_total']['line'],2.25)

    def test_other_families_rotate_and_seven_direction_keeps_three_minute_delay(self):
        groups=[dict(rank=1,section='seven_direction_pockets',pocket_id='two_goals_shift_up_over',name='shift'),
                dict(rank=2,section='refined_pockets',pocket_id='lead2_line35',name='refined'),
                dict(rank=3,section='external_pockets',pocket_id='draw_60_65',name='draw')]
        e=self.event(1,'1-1',minute=35,line=4.0);ts=1793094000
        e['start_time']=ts-2100
        e['lab_odds_payload']=dict(results=dict(odds={'1_3':[dict(add_time=ts-2200,time_str='',ss='0-0',handicap='2.5')]}))
        first=self.snapshot([e],groups=groups);first['daily_pocket_evaluations']=d.evaluate_pockets(self.root,first,[],self.history)
        self.assertEqual(first['daily_pocket_evaluations']['seven_direction_pockets']['choices']['two_goals_shift_up_over'],[])
        (self.history/'first.json').write_text(json.dumps(first))
        later=copy.deepcopy(e);later.update(minute=38);later['events_main_total']['market_checked_at']=ts+180
        other=[self.event(2,'2-0',minute=53,line=3.5,second=180),self.event(3,'1-1',minute=62,line=3.5,second=180)]
        snap=self.snapshot([later]+other,second=180,groups=groups)
        result=d.evaluate_pockets(self.root,snap,[],self.history)
        selected=d.choose(snap['events'],groups,result)
        self.assertEqual([e['daily_group']['rank'] for e in selected],[1,2,3])

    def test_renderer_has_exactly_four_lines_and_handles_missing_statistics(self):
        e=self.event(1,'2-0',line=3.75);e['daily_group']=self.daily['groups'][0]
        e['board_stats']['xg']=None;e['board_stats']['redcards']=['?','?']
        snap=self.snapshot([e]);snap['display_event_ids']=['1']
        text=d.render(snap)
        lines=[s for s in text.splitlines() if s.strip()]
        self.assertEqual(len(lines),5)
        self.assertIn('ТБ 3,75 × 1,90',lines[2]);self.assertIn('xG н/д',lines[3])
        self.assertIn('Группа №1',lines[4]);self.assertNotIn('ROI',text);self.assertNotIn('ГП:',text)

    def test_real_provider_names_render_in_russian_without_changing_identity(self):
        examples=[('Türkiye Cup','Yeni Mersin Idmanyurdu','Mus Spor FC',
                   'Кубок Турции','Ени Мерсин Идманюрду','Муш Спор'),
                  ('Bosnia & Herzegovina Cup','FK Laktasi','Zrinjski Mostar',
                   'Кубок Боснии и Герцеговины','Лакташи','Зриньски Мостар'),
                  ('Italy Serie A','Sassuolo','Lazio',
                   'Италия, Серия А','Сассуоло','Лацио'),
                  ('Türkiye Cup','Alanyaspor','12 Bingolspor',
                   'Кубок Турции','Аланьяспор','12 Бингёльспор'),
                  ('Slovenia 2. SNL','Tabor Sezana','ND Slovan Ljubljana',
                   'Словения, 2-я лига','Табор Сежана','Слован Любляна'),
                  ('Bulgaria Second League','Ludogorets Razgrad II','PFC Dobrudzha Dobrich',
                   'Болгария, Вторая лига','Лудогорец Разград-2','Добруджа Добрич'),
                  ('Georgia Cup','FC Gagra','FC Rustavi',
                   'Кубок Грузии','Гагра','Рустави')]
        for source_league,home,away,league_ru,home_ru,away_ru in examples:
            e=self.event(1,'2-0',line=3.25)
            e.update(league=source_league,home=home,away=away,daily_group=self.daily['groups'][0])
            snap=self.snapshot([e]);snap['display_event_ids']=['1']
            heading=d.render(snap).splitlines()[2]
            self.assertIn(f'{league_ru} · {home_ru} - {away_ru}',heading)
            self.assertNotRegex(heading,r'[A-Za-z]')
            self.assertEqual((e['league'],e['home'],e['away']),
                             (source_league,home,away))
        from live_ru import russian_name
        self.assertEqual(russian_name('Portugal Primeira Liga',league=True),
                         'Португалия, Примейра-лига')

    def test_budget_is_a_hard_api_cap(self):
        calls=[];budget=d.Budget(lambda *a,**kw:calls.append(a),96)
        for _ in range(96):budget('test')
        with self.assertRaises(RuntimeError):budget('test')
        self.assertEqual(len(calls),96);self.assertTrue(budget.exhausted)

    def test_complete_collect_keeps_priced_events_and_publication_metadata(self):
        rows=[dict(id=str(i),home=dict(id='11',name='A'),away=dict(id='22',name='B'),
                   league=dict(id='33',name='Test'),timer=dict(tm=53),ss='2-0',stats={}) for i in [1,2]]
        ts=int(__import__('time').time())
        def get(path,**params):
            if path.endswith('inplay'):return dict(success=1,results=rows),{}
            if path.endswith('/odds'):return dict(success=1,results=dict(stats=dict(odds_update={'1_3':ts}),odds={'1_3':[
                dict(add_time=ts,ss='2-0',handicap='3.5',over_od='1.9',under_od='1.9')]})),{}
            raise AssertionError(path)
        def enrich(events,*a,**kw):
            for e in events:e['stats_trend']=dict(available=False)
            return 0
        with patch.object(live,'get',get),patch.object(live,'enrich_events',enrich),patch.object(live,'enrich_profiles',return_value={}):
            snap=d.collect(live,'full',[],self.history,self.ledger,self.root)
        self.assertEqual(snap['display_event_ids'],['1','2'])
        self.assertEqual(snap['rules_version'],d.VERSION)
        choices=[dict(event_id=eid,sports_reason='Group conditions match',expected_scenario='FT over') for eid in snap['display_event_ids']]
        saved=record_choices(dict(schema_version=1,events=[],selections=[]),snap,choices,snap['generated_at_utc'])
        self.assertEqual(saved['selections'][0]['daily_group']['rank'],1)
        self.assertEqual(saved['selections'][0]['daily_pockets'],self.daily)
        self.assertIs(live.complete_odds(snap),snap)
        with self.assertRaises(ValueError):record_choices(dict(schema_version=1,events=[],selections=[]),snap,list(reversed(choices)),snap['generated_at_utc'])

    def test_manual_entrypoint_uses_daily_collector_and_ready_markdown(self):
        output=io.StringIO();stub=dict(snapshot_id='test-1',live_markdown='four lines')
        def save(*args):
            self.assertTrue(output.getvalue().startswith('LIVE_MARKDOWN_JSON='))
            return 'snapshots/live/test-1.json'
        with patch.object(live,'read_request',return_value=('full',[])),patch('live_daily.collect',return_value=stub) as collector,patch.object(live,'save_snapshot',side_effect=save),patch.dict(os.environ,{'GITHUB_OUTPUT':''}),contextlib.redirect_stdout(output):
            live.main()
        self.assertTrue(collector.called);self.assertTrue(output.getvalue().startswith('LIVE_MARKDOWN_JSON='))


if __name__=='__main__':unittest.main()
