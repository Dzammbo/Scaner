import unittest
import capture as c

def row(i, event='1', model='p', minute=20, **changes):
    return dict(dict(entry_id=str(i),event_id=event,model=model,entry_version=c.ENTRY_VERSION,
        snapshot_id=str(i),observed_at_utc=f'2026-10-20T10:{i:02d}:00+00:00',minute=minute,
        score='0-0',market=dict(score='0-0',line=2.5,price=2),top5_benchmark=False),**changes)

class FirstEntryTests(unittest.TestCase):
    def test_repeated_winner_cannot_overweight_other_matches(self):
        rows=[row(i) for i in range(1,11)]+[row(11,event='2'),row(12,event='3')]
        outcomes={'1':dict(settled=True,final=[3,0]),'2':dict(settled=True,final=[0,0]),'3':dict(settled=True,final=[0,0])}
        result=c.entry_progress({}, {'p':list(reversed(rows))},outcomes)
        first=result['first_entry_progress']['by_model']['p']
        self.assertEqual(result['entries'],12)
        self.assertEqual(first['entries'],3)
        self.assertAlmostEqual(first['roi'],-1/3)
        self.assertGreater(result['by_model']['p']['roi'],0)

    def test_price_and_version_and_models_and_window(self):
        rows=[row(1,market=None),row(2,minute=30),row(3,minute=50),row(4,model='other'),row(0,entry_version='old')]
        chosen=c.first_entries(rows,c.ENTRY_VERSION)
        self.assertEqual([r['entry_id']for r in chosen],['2','4'])
        window=c.first_entries(rows,c.ENTRY_VERSION,lambda r:46<=r['minute']<=60)
        self.assertEqual([r['entry_id']for r in window],['3'])

    def test_later_win_never_replaces_first_loss(self):
        early=row(1,market=dict(score='0-0',line=3.5,price=2))
        late=row(2,market=dict(score='0-0',line=1.5,price=2))
        chosen=c.first_entries([late,early],c.ENTRY_VERSION)
        self.assertEqual(chosen,[early])
        self.assertEqual(c.settle_entry(chosen[0],dict(settled=True,final=[2,0])),-1)
        self.assertEqual(c.first_entries([dict(early,profit=None),dict(late,profit=1)],c.ENTRY_VERSION)[0]['entry_id'],'1')

if __name__=='__main__':unittest.main()
