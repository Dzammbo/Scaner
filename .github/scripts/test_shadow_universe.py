import gzip
import json
import tempfile
import unittest
from pathlib import Path

import shadow_universe as s


class ShadowTests(unittest.TestCase):
    def test_freezes_first_quoted_point_and_separates_rejections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [dict(event_id='1', minute=23, score='0-0', home='A', away='B', league='L',
                           research_enriched=True, selection_confidence=dict(eligible=True, data_sufficient=True)),
                      dict(event_id='2', minute=23, score='0-0', research_enriched=True,
                           selection_confidence=dict(eligible=False, data_sufficient=True)),
                      dict(event_id='3', minute=23, score='0-0', research_enriched=False),
                      dict(event_id='4', minute=23, score='0-0', research_enriched=False)]
            for e in events:
                e['events_main_total'] = dict(line=2.5, price=1.9) if e['event_id'] != '4' else None
            snapshot = dict(snapshot_id='10-1', board_observed_at_utc='2026-10-24T17:00:00+00:00', events=events)
            market = lambda e, _: (e['events_main_total'], None if e['events_main_total'] else 'unavailable')
            settle = lambda r, o: (r['market']['price']-1 if o.get('final') == [3, 0] else -1) if o.get('settled') else None
            first = s.evaluate(root, snapshot, events[:1], market, {}, settle)
            self.assertEqual([r['decision'] for r in first['entries']],
                             ['strong_v8', 'rejected_strong_v8', 'not_deep_evaluated'])
            self.assertEqual(first['coverage']['missing_exact_quote_unavailable'], 1)
            journal = root/s.JOURNAL/'2026-10-24'/'10-1.json.gz'
            journal.parent.mkdir(parents=True)
            journal.write_bytes(gzip.compress(json.dumps(dict(shadow_universe=first)).encode()))
            events[0]['events_main_total'] = dict(line=3.5, price=1.6)
            events[3]['events_main_total'] = dict(line=2.5, price=2.0)
            second = s.evaluate(root, dict(snapshot, snapshot_id='11-1'), events[:1], market, {}, settle)
            self.assertEqual([r['event_id'] for r in second['entries']], ['4'])
            final = s.report(root, {'1': dict(settled=True, final=[3, 0]),
                                    '2': dict(settled=True, final=[0, 0])}, settle)
            self.assertEqual(final['total']['n'], 2)
            self.assertAlmostEqual(final['by_decision']['strong_v8']['roi'], .9)
            self.assertEqual(final['by_decision']['rejected_strong_v8']['roi'], -1)


if __name__ == '__main__':
    unittest.main()
