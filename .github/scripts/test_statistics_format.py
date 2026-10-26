import copy
import unittest

from statistics_format import build_tables, render_statistics, percent


def row(name, n=40, roi=.1, robust=.05, market='over'):
    return dict(name=name, n=n, selected=n, roi=roi, market=market,
                without_top3=dict(n=n-3, roi=robust))


def report():
    return dict(generated_at_utc='2026-10-26T04:00:00+00:00',
                statistics_epoch=dict(id='control', rules_version='fixed'),
                current=dict(standard_v8=row('control')),
                independent_pockets=dict(id='tb', version=1, catalog_sha256='fixed', pockets={}),
                under_pockets=dict(id='tm', version=1, catalog_sha256='fixed', pockets={}),
                distinct_pockets=dict(id='distinct', version=1, catalog_sha256='fixed', pockets={}),
                seven_direction_pockets=dict(id='new', version=1, catalog_sha256='fixed', pockets={}))


class StatisticsFormatTest(unittest.TestCase):
    def test_percent_rounding_keeps_the_agreed_two_decimal_display(self):
        self.assertEqual(percent(.17825), '+17,83%')
        self.assertEqual(percent(-.17825), '−17,83%')
        self.assertEqual(percent(None), 'н/д')

    def test_global_market_ranking_and_small_samples_do_not_outrank_valid_rows(self):
        data = report()
        data['independent_pockets']['pockets']['a'] = row('самостоятельный', robust=.08)
        data['distinct_pockets']['pockets']['b'] = row('ТБ сочетание', robust=.2)
        data['under_pockets']['pockets']['u'] = row('ТМ красная', robust=-.08, market='under')
        data['seven_direction_pockets']['pockets'] = {
            'tiny': row('ТБ новый', n=30, roi=.9, robust=.8),
            'under': row('ТМ новый', n=31, robust=-.05, market='under')}
        tables = build_tables(data)
        self.assertEqual([r['name'] for r in tables['over']], [
            'Сочетание: сочетание', 'самостоятельный', 'Контрольная пятёрка', 'Новый: новый'])
        self.assertIsNone(tables['over'][-1]['roi_without_top3'])
        self.assertEqual([r['name'] for r in tables['under']], ['Новый: новый', 'ТМ красная'])

    def test_delta_uses_the_same_robust_metric_for_each_market(self):
        old = report()
        old['independent_pockets']['pockets']['a'] = row('ТБ', n=40, roi=.9, robust=.1)
        old['under_pockets']['pockets']['b'] = row('ТМ', n=40, roi=.1, robust=.9, market='under')
        new = copy.deepcopy(old)
        new['independent_pockets']['pockets']['a'] = row('ТБ', n=45, roi=.8, robust=.2)
        new['under_pockets']['pockets']['b'] = row('ТМ', n=43, roi=.2, robust=.8, market='under')
        tables = build_tables(new, old)
        tb = next(r for r in tables['over'] if r['name'] == 'ТБ')
        self.assertAlmostEqual(tb['delta_pp'], 10)
        self.assertEqual(tb['new_closed'], 5)
        self.assertAlmostEqual(tables['under'][0]['delta_pp'], -10)
        self.assertEqual(tables['under'][0]['new_closed'], 3)
        new['independent_pockets']['catalog_sha256'] = 'different'
        tb = next(r for r in build_tables(new, old)['over'] if r['name'] == 'ТБ')
        self.assertIsNone(tb['delta_pp'])
        self.assertIsNone(tb['new_closed'])

    def test_aliases_are_not_duplicated_and_supplementary_groups_join_the_tb_table(self):
        data = report()
        data['current']['sports_v8_fresh'] = data['current']['standard_v8']
        data['current']['provider_main_total_exact'] = data['current']['standard_v8']
        data['quality_comparison'] = dict(progress=dict(
            benchmark=dict(n=50, roi=.1, without_top3_roi=.07),
            candidate=dict(n=35, roi=.1, without_top3_roi=.06)))
        data['shadow_universe'] = dict(total=dict(n=40, roi=.1, without_top3=.08))
        self.assertEqual(len(build_tables(data)['over']), 4)
        text = render_statistics(data)
        self.assertEqual(text.count('| Группа |'), 2)
        self.assertEqual(text.count('| Δ без 3, п.п. |'), 2)
        self.assertNotIn('| Δ ROI, п.п. |', text)
        self.assertEqual(text.count('| Контрольная пятёрка |'), 1)
        self.assertIn('26.10, 07:00 МСК', text)

    def test_missing_baseline_and_errors_are_visible_without_mutating_data(self):
        data = report()
        data['under_pockets'] = dict(status='error', error_type='ValueError')
        original = copy.deepcopy(data)
        text = render_statistics(data)
        self.assertIn('Самостоятельные ТМ: ValueError', text)
        self.assertIn('| 40 | +10,00% | +5,00% | н/д |', text)
        self.assertEqual(data, original)

    def test_first_result_and_threshold_crossing_are_not_a_zero_roi_baseline(self):
        old = report()
        old['seven_direction_pockets']['pockets']['a'] = row('ТБ новый', n=0, roi=None)
        new = copy.deepcopy(old)
        new['seven_direction_pockets']['pockets']['a'] = row('ТБ новый', n=7, roi=.5)
        self.assertIn('первый результат', render_statistics(new, old))
        old['seven_direction_pockets']['pockets']['a'] = row('ТБ новый', n=30)
        new['seven_direction_pockets']['pockets']['a'] = row('ТБ новый', n=31)
        actual = next(r for r in build_tables(new, old)['over'] if r['key'].endswith('/a'))
        self.assertIsNone(actual['delta_pp'])


if __name__ == '__main__':
    unittest.main()
