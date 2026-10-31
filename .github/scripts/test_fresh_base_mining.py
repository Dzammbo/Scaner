import unittest
import numpy as np
from fresh_base_mining import first_indices,eligible_labels,atoms_for,prepare,discover,evaluate,prepare_phase,evaluate_phase


class RawDiscoveryChecks(unittest.TestCase):
    def test_first_qualifying_point_not_best_return(self):
        events=np.array([0,1,0,2,1])
        np.testing.assert_array_equal(first_indices(np.array([1,0,1,1,1],dtype=bool),events),[0,3,4])

    def test_no_future_confirmation_in_training(self):
        rows=[dict(profit=.9,confirmed_at=10),dict(profit=-1,confirmed_at=30),dict(profit=.8,confirmed_at=None)]
        np.testing.assert_array_equal(eligible_labels(rows,20),[True,False,False])

    def test_cut_learning_does_not_read_test_feature_values(self):
        rows=[dict(event_id=str(i),features=dict(price=float(i))) for i in range(10)]
        mask=np.array([i<5 for i in range(10)]);events=np.arange(10)
        p=dict(minimum_discovery_matches=3,numeric_quantiles=[.5])
        a,_=atoms_for(rows,mask,events,p)
        for r in rows[5:]:r['features']['price']=1000000.
        b,_=atoms_for(rows,mask,events,p)
        self.assertEqual([x[0] for x in a],[x[0] for x in b])
        self.assertEqual(a[0][0]['value'],2.)

    def test_boundary_events_and_stale_quotes_are_disclosed(self):
        p=dict(discovery_end_msk='2026-10-24',validation_start_msk='2026-10-25',validation_end_msk='2026-10-27',
               test_start_msk='2026-10-28',test_end_msk='2026-10-30',price_alignment_seconds=120,game_minute_window=[5,89])
        rows=[dict(event_id='a',day='2026-10-24',quote_lag=0,features=dict(minute=20)),
              dict(event_id='a',day='2026-10-25',quote_lag=0,features=dict(minute=70)),
              dict(event_id='b',day='2026-10-26',quote_lag=121,features=dict(minute=10)),
              dict(event_id='c',day='2026-10-29',quote_lag=0,features=dict(minute=45))]
        result,quality=prepare(dict(points=rows),p)
        self.assertEqual([r['event_id'] for r in result],['c'])
        self.assertEqual(quality['boundary_crossing_events'],1)
        self.assertEqual(quality['unknown_or_misaligned_quote'],1)

    def test_discovery_matches_explicit_temporal_slice(self):
        rows=[dict(event_id=str(i//2),day='2026-10-'+str(20+i%3),league=str(i%4),profit=.9 if i%5 else -1,
                   features=dict(price=1.5+i/100,minute=float(i%15+8))) for i in range(40)]
        p=dict(minimum_discovery_matches=3,numeric_quantiles=[.2,.5,.8],maximum_training_jaccard=.8,discovery_shortlist_limit=4)
        mask=np.array([i%3!=0 for i in range(40)]);labels=np.ones(40,dtype=bool)
        a=discover(rows,mask,labels,p)
        b=discover([r for i,r in enumerate(rows) if mask[i]],np.ones(int(mask.sum()),dtype=bool),labels[mask],p)
        self.assertEqual(a,b)

    def test_cached_phase_evaluation_preserves_first_entries_and_missing_labels(self):
        rows=[dict(event_id=str(i//2),day='2026-10-'+str(20+i%3),league=str(i%4),profit=.9 if i%5 else -1,
                   features=dict(price=1.5+i/100,minute=float(i%15+8))) for i in range(40)]
        mask=np.array([i%3!=0 for i in range(40)]);labels=np.array([i%7!=0 for i in range(40)])
        rule=[dict(field='minute',op='>',value=10),dict(field='price',op='<=',value=1.8)]
        old,old_indices=evaluate(rows,rule,mask,labels)
        state=prepare_phase(rows,mask,labels);new,new_indices=evaluate_phase(rule,state)
        self.assertEqual(old,new)
        self.assertEqual([rows[int(i)]['event_id'] for i in old_indices],[state['rows'][int(i)]['event_id'] for i in new_indices])


if __name__=='__main__':unittest.main()
