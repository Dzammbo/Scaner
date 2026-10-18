import unittest,math,json,sys,gzip
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from lab import *
sys.path.insert(0,str(ROOT/'scripts'))
from live_selection import evaluate_selection

class LabTests(unittest.TestCase):
 def test_asian_settlement(self):
  self.assertAlmostEqual(settle(2,1.75,1.9),.45)
  self.assertEqual(settle(2,2.25,1.9),-.5)
  self.assertEqual(settle(2,2.,1.9),0)
  self.assertAlmostEqual(settle(3,2.25,1.9),.9)
 def test_provider_main_quarter_price_is_used_without_line_change(self):
  e={'score':'0-0','events_main_total':{'line':2.25,'over':1.9,'source':'test','quote_score':'0-0','market_checked_at':1000}}
  s={'board_observed_at_utc':1000,'generated_at_utc':1000}
  self.assertEqual(market_for_event(e,s)[0]['line'],2.25)
  self.assertEqual(market_for_event(e,s)[0]['price'],1.9)
  e['events_main_total']['line']=2
  self.assertEqual(market_for_event(e,s)[0]['line'],2)
 def test_score_and_time_checks(self):
  e={'score':'0-0','events_main_total':{'line':2,'over':1.9,'source':'test','quote_score':'1-0','market_checked_at':1000}}
  s={'board_observed_at_utc':1000,'generated_at_utc':1000}
  self.assertIsNone(market_for_event(e,s)[0])
  e['events_main_total']['quote_score']='0-0';e['events_main_total']['market_checked_at']=1002
  self.assertIsNone(market_for_event(e,s)[0])
  e['events_main_total']['market_checked_at']=800
  self.assertIsNone(market_for_event(e,s)[0])
 def test_poisson_ev(self):
  for lam in [.1,1,3,8]:
   self.assertAlmostEqual(expected_profit(lam,0,1.5,1.9),p_two(lam)*1.9-1,places=10)
 def test_training_never_uses_current_day(self):
  r=json.loads((Path(__file__).parent/'results.json').read_text())
  for p in r['model_training']:
   if p['beta'] is not None:
    self.assertLess(p['max_training_day'],p['day']);self.assertGreaterEqual(p['training_n'],80)
 def test_replay_unique_and_cap(self):
  d=json.load(gzip.open(Path(__file__).parent/'details.json.gz'))
  for rows in d['snapshot_selections'].values():
   self.assertEqual(len(rows),len({r['id'] for r in rows}))
   counts=collections.Counter(r['sid'] for r in rows)
   self.assertTrue(all(n<=5 for n in counts.values()))
 def test_all_profits_with_production_settler(self):
  d=json.load(gzip.open(Path(__file__).parent/'details.json.gz'));n=0
  for rows in list(d['historical'].values())+list(d['snapshot_selections'].values()):
   for r in rows:
    if r.get('profit') is None:continue
    # Independent production settlement needs a valid per-team score, so add remaining to home only.
    final=[r['score'][0]+r['additional'],r['score'][1]]
    record={'event_id':r['id'],'score':'-'.join(map(str,r['score'])),'recommended_total':{'line':r['line'],'over':r['price'],'source':'captured'}}
    out={'event_id':r['id'],'time_status':'3','final_score':'-'.join(map(str,final)),'source':'archive'}
    self.assertAlmostEqual(evaluate_selection(record,out)['conditional_profit'],r['profit']);n+=1
  self.assertGreater(n,1000)

if __name__=='__main__':unittest.main()
