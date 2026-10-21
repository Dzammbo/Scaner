import contextlib,datetime,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capture as c
import broad
import statistics_epoch as ep
from test_background import settings

def event(eid,minute=20):
 return dict(event_id=str(eid),minute=minute,score='0-0',home='H',away='A',league='L',
  board_stats={'on_target':[1,1],'dangerous_attacks':[2,2]},stats_trend_shortlisted=True,
  control_shortlisted=True,repeat_control_shortlisted=True,legacy_window_8_60=True,
  full_collection_window_5_89=True,research_sampling=dict(inclusion_probability=1,selected=True))

def quote(e,s):
 return (None,'missing')if e['event_id']=='2'else(dict(line=1.5,price=2,score='0-0',source='test'),None)

class EpochTests(unittest.TestCase):
 def state(self):return dict(id='test-epoch',started_at_utc='2026-10-21T08:00:00+00:00',selected=set(),excluded=set(),previous_capture_count=0)
 def test_single_base_no_refill_no_price_backfill_and_shared_ranks(self):
  events=[event(i)for i in range(1,8)]
  # Only sports rank 6 qualifies for the score-one pocket: it must NOT be recruited.
  events[5]['score']='1-0'
  with patch.object(c,'market_for_event',side_effect=quote):
   groups,_,base=ep.choose_common(c,{},events,self.state(),None)
  self.assertEqual([r['event_id']for r in base],['1','2','3','4','5'])
  self.assertEqual(groups['provider_main_score_one'],[])
  self.assertEqual([r['rank']for r in groups['provider_main_need_le2']],[1,3,4,5])
  self.assertEqual([r['rank_cohort']for r in groups['provider_main_need_le2']],['1-3','1-3','4-5','4-5'])
  state=self.state();state['selected']={r['event_id']for r in base}
  with patch.object(c,'market_for_event',side_effect=quote):
   later,_,second=ep.choose_common(c,{},events,state,None)
  self.assertEqual([r['event_id']for r in second],['6','7'])
  self.assertNotIn('2',[r['event_id']for rows in later.values()for r in rows])
  self.assertTrue(all({r['event_id']for r in rows}<={r['event_id']for r in base}for rows in groups.values()))
 def test_boundaries_and_exclusions_before_common_quota(self):
  state=self.state();state['excluded']={'old'};state['selected']={'seen'}
  events=[event('5',5),event('old'),event('seen'),event('8',8),event('60',60),event('61',61)]
  with patch.object(c,'market_for_event',side_effect=quote):
   _,_,base=ep.choose_common(c,{},events,state,None)
  self.assertEqual([r['event_id']for r in base],['8','60'])
 def test_restart_uses_first_successful_marker_not_wall_clock_or_failed_attempt(self):
  state=self.state();meta=ep.metadata(state)
  receipts=[dict(statistics_epoch=meta,epoch_base_choices=[{'event_id':'first'}]),
   dict(failed=True,statistics_epoch=dict(meta,started_at_utc='2026-10-20T00:00:00+00:00')),
   dict(statistics_epoch=dict(meta,id='old-epoch'),epoch_base_choices=[{'event_id':'wrong'}])]
  targets={'old':{'board_observed_at_utc':'2026-10-21T07:59:59+00:00'},'new':{'board_observed_at_utc':'2026-10-21T08:01:00+00:00'}}
  restored=ep.read_state({'id':'test-epoch'},receipts,targets,'2026-10-22T00:00:00+00:00')
  self.assertEqual(restored['started_at_utc'],state['started_at_utc']);self.assertEqual(restored['selected'],{'first'})
  self.assertEqual(restored['excluded'],{'old'});self.assertEqual(set(ep.new_universe(restored,targets)),{'new'})
 def test_capture_roundtrip_reset_and_restart_keep_legacy_archive(self):
  with tempfile.TemporaryDirectory()as directory,settings():
   root=Path(directory);data=root/'research/live-lab';request=root/'request.json'
   now=datetime.datetime.now(datetime.timezone.utc);request.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now.isoformat())))
   (data/'receipts').mkdir(parents=True)
   old_time=(now-datetime.timedelta(hours=2)).isoformat()
   old=dict(contour='experimental',choices={},deep_entry_event_ids=[],outcome_targets=[dict(event_id='old',board_observed_at_utc=old_time,score='0-0')])
   legacy=data/'receipts/legacy.json';legacy.write_text(json.dumps(old));before=legacy.read_bytes()
   def collect(module,budget,history,seed,excluded_deep=(),control_seen=None):
    self.assertIn('old',control_seen)
    ts=datetime.datetime.now(datetime.timezone.utc).isoformat()
    events=[event('old')]+[event(i)for i in range(1,8)]
    for e in events:e['control_shortlisted']=e['event_id']not in control_seen
    return dict(events=events,board_observed_at_utc=ts,generated_at_utc=ts,snapshot_id='test')
   config=dict(id='test-epoch',enabled=True,rules_version=ep.VERSION)
   with patch.object(c,'ROOT',root),patch.object(c,'DATA',data),patch.object(ep,'load_config',return_value=config),patch.object(broad,'collect',side_effect=collect),patch.object(c,'sports_order',side_effect=lambda rows:rows),patch.object(c,'enrich_markets',return_value=None),patch.object(c,'market_for_event',side_effect=quote),patch.object(c,'training_model',return_value=(None,{})),patch.object(c,'collect_outcomes',return_value=dict(events=[],queued_after_run=0)),patch.dict(os.environ,dict(LAB_REQUEST_FILE=str(request),GITHUB_RUN_ID='99991',GITHUB_RUN_ATTEMPT='1')),contextlib.redirect_stdout(io.StringIO()):
    c.main()
    first=json.loads((data/'snapshots/99991-1.json').read_text())
    with patch.dict(os.environ,dict(GITHUB_RUN_ATTEMPT='2')):c.main()
    second=json.loads((data/'snapshots/99991-2.json').read_text())
   self.assertEqual(legacy.read_bytes(),before)
   self.assertEqual(first['research_progress']['unique_observed'],7)
   self.assertEqual(first['research_progress_lifetime']['unique_observed'],8)
   self.assertEqual([r['event_id']for r in first['epoch_base_choices']],['1','2','3','4','5'])
   self.assertEqual([r['event_id']for r in second['epoch_base_choices']],['6','7'])
   self.assertEqual(first['statistics_epoch']['started_at_utc'],second['statistics_epoch']['started_at_utc'])
   self.assertEqual(first['control_evaluation']['choices'],first['experimental_choices']['standard_v8'])
   self.assertTrue(all(r.get('statistics_epoch_id')is None for rows in first['model_entries'].values()for r in rows if r['event_id']=='old'))

if __name__=='__main__':unittest.main()
