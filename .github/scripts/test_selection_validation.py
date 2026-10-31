import unittest
from selection_validation import exact_profit,holm,screen,summary


class ValidationChecks(unittest.TestCase):
    def test_quarter_prices_and_score_conflicts(self):
        entry=dict(score='1-0',market=dict(score='1-0',line=2.25,price=1.9))
        self.assertEqual(exact_profit(entry,dict(settled=True,final=[1,1])),-.5)
        entry['market']['line']=2.75
        self.assertAlmostEqual(exact_profit(entry,dict(settled=True,final=[2,1])),.45)
        self.assertIsNone(exact_profit(entry,dict(settled=True,final=[0,0])))
        self.assertIsNone(exact_profit(entry,dict(settled=True,final=[2,1],result_conflict=True)))

    def test_lucky_day_cannot_pass_stability(self):
        rows=[dict(event_id=str(i),day='2026-10-26' if i<10 else '2026-10-29',
                   league='a' if i%2 else 'b',profit=.9 if i<10 else -.5) for i in range(12)]
        result=screen(rows,'2026-10-31',dict(temporal_blocks=[['2026-10-26','2026-10-27'],['2026-10-28','2026-10-30']]))
        self.assertGreater(result['total']['roi'],0)
        self.assertFalse(result['descriptive_stability_pass'])
        self.assertFalse(result['checks']['positive_without_best_day'])

    def test_pending_and_current_day_are_not_losses_or_mature_results(self):
        rows=[dict(day='2026-10-29',league='a',profit=.8),dict(day='2026-10-30',league='b',profit=None),
              dict(day='2026-10-31',league='a',profit=-1)]
        result=screen(rows,'2026-10-31',dict(temporal_blocks=[['start','2026-10-30']]))
        self.assertEqual(result['mature']['n'],1)
        self.assertEqual(result['mature']['unresolved'],1)
        self.assertAlmostEqual(result['mature']['roi'],.8)
        self.assertAlmostEqual(result['mature']['worst_unresolved_profit_bound'],-.2)
        self.assertFalse(result['descriptive_stability_pass'])

    def test_holm_keeps_multiplicity(self):
        self.assertEqual(holm({'a':.01,'b':.04,'c':.5}),{'a':.03,'b':.08,'c':.5})

    def test_single_day_cannot_have_cluster_evidence(self):
        result=summary([dict(day='2026-10-29',league=str(i),profit=.9) for i in range(20)])
        self.assertNotIn('conservative_cluster_ci95',result)
        self.assertIsNone(result['without_best_day'])


if __name__=='__main__':unittest.main()
