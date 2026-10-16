import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from analyze import at,delta,profit,profile,quote,search,feature_rows
class ResearchTests(unittest.TestCase):
    def test_no_future_revision_or_future_point(self):
        points=[(100,20,1),(120,25,2),(180,25,9),(125,31,10)]
        self.assertEqual(at(sorted(points),30,130),2)
    def test_counter_correction_is_not_low_threat(self):
        s={'on_target':{'home':[(1,0,0),(2,20,4),(3,25,2),(4,30,3)],'away':[(1,0,0)]}}
        self.assertIsNone(delta(s,'on_target',20,30,4))
    def test_asian_under_settlement(self):
        cases=[(2,2.25,2,.5),(3,2.75,2,-.5),(3,3,1.9,0),(2,2.75,1.8,.8),(4,2.5,2,-1)]
        for ft,line,p,result in cases:self.assertAlmostEqual(profit(ft,line,p),result)
    def test_quote_freshness_score_and_future(self):
        row={'add_time':'100','time_str':'30','ss':'1-0','handicap':'2.5','under_od':'1.9','over_od':'1.9'}
        p={'success':1,'results':{'odds':{'1_3':[row]}}}
        self.assertIsNotNone(quote(p,30,110,[1,0]))
        self.assertIsNone(quote(p,30,99,[1,0]))
        self.assertIsNone(quote(p,30,221,[1,0]))
        self.assertIsNone(quote(p,30,110,[0,0]))
    def test_profile_excludes_current_and_later_matches(self):
        def row(i,ts,score):return {'id':str(i),'time':str(ts),'league':{'id':'l'},'home':{'id':'h'},'away':{'id':'a'},'scores':{'2':{'home':str(score),'away':'0'}}}
        past=row(1,1000,2);now=row(2,20000,10);future=row(3,30000,20)
        p=profile(now,[past,now,future])
        self.assertEqual(p['home']['n'],1);self.assertEqual(p['home']['mean_total'],2)
    def test_empty_data_never_creates_rules(self):
        self.assertEqual(search([])['finalists'],[])
    def test_future_goal_correction_does_not_exclude_earlier_snapshot(self):
        row={'id':'x','time':'1000','league':{'id':'l'},'home':{'id':'h','name':'H'},'away':{'id':'a','name':'A'},'scores':{'1':{'home':'0','away':'0'},'2':{'home':'1','away':'0'}},'stats':{}}
        def point(t,v):return {'time_str':str(t),'val':str(v),'created_at':str(1000+t*60)}
        data={'success':1,'results':{'goals':{'home':[point(0,0),point(30,0),point(60,1),point(61,0),point(70,1)],'away':[point(0,0)]}}}
        rows,cov=feature_rows([row],{'x'},{'x':{'trend':{'payload':data}}})
        self.assertTrue(any(r['minute']==30 for r in rows))
if __name__=='__main__':unittest.main()
