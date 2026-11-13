"""Canonical chat presentation of frozen statistics; never changes the ledgers."""
import argparse
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path

VERSION = 'statistics-all-active-20261108-v7'
MSK = dt.timezone(dt.timedelta(hours=3))


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def robust(row):
    # New-pocket reports can contain this statistic below the display threshold.
    if row.get('n', 0) <= 30:
        return None
    value = row.get('without_top3', row.get('without_top3_roi'))
    return number(value.get('roi') if isinstance(value, dict) else value)


def compatible(current, previous, keys):
    return bool(previous) and all(current.get(key) == previous.get(key) for key in keys)


def build_tables(report, previous=None):
    previous = previous or {}
    result = dict(format_version=VERSION, over=[], under=[], errors=[],
                  reference_at_utc=previous.get('generated_at_utc'))

    population = report.get('population') or {}
    prior_population = (previous.get('population') or {}) if compatible(
        report.get('statistics_epoch') or {}, previous.get('statistics_epoch') or {},
        ('id', 'rules_version')) else {}
    result['population'] = {
        key: dict(n=population[key], delta=population[key]-prior_population[key]
                  if prior_population.get(key) is not None else None)
        for key in ('unique_observed', 'confirmed_closed', 'closed_with_provider_price')
        if key in population}

    def add(key, label, market, row, old=None):
        # Archived under entries never enter the active report, including old JSON.
        if market != 'over' and not (market == 'under' and report.get('active_group_series') == 'tracked_groups' and (report.get('under_pockets') or {}).get('version') == 'ten-under-forward-20261104-v1'):
            return
        n = row['n']
        roi, without = number(row.get('roi')), robust(row)
        metric = without
        prior_metric = robust(old) if old else None
        delta = ((metric - prior_metric) * 100
                 if metric is not None and prior_metric is not None else None)
        result[market].append(dict(
            key=key, name=label, n=n, selected=row.get('selected'), roi=roi,
            roi_without_top3=without, without_top3_n=n-3 if without is not None else None,
            new_closed=n-old['n'] if old else None, delta_pp=delta,
            unresolved=row.get('unresolved'), unit_profit=row.get('unit_profit'),
            first_result=bool(old and old['n'] == 0 and n > 0 and roi is not None)))

    # An explicit replacement series must never fall back to historical totals.
    if report.get('active_group_series') == 'tracked_groups' or 'tracked_groups' in report:
        section = report.get('tracked_groups') or {}
        prior = previous.get('tracked_groups') or {}
        same = compatible(section, prior, ('id', 'version', 'catalog_sha256'))
        result['reference_at_utc'] = previous.get('generated_at_utc') if same else None
        result['tracking_series'] = section.get('id')
        result['series_started_at_utc'] = section.get('started_at_utc')
        if section.get('status') == 'error' or len(section.get('pockets') or {}) != 10:
            result['errors'].append('Новая десятка: неполный или ошибочный журнал')
            return result
        for key, row in section['pockets'].items():
            old = (prior.get('pockets') or {}).get(key) if same else None
            add('tracked_groups/' + key, row['name'], 'over', row, old)
            result['over'][-1]['priority'] = row.get('priority', 99)
        result['over'].sort(key=lambda r: (
            r['roi_without_top3'] is None, -(r['roi_without_top3'] or 0),
            r['roi'] is None, -(r['roi'] or 0), r['priority']))
        under = report.get('under_pockets')
        if under is not None:
            prior_under = previous.get('under_pockets') or {}
            same_under = compatible(under, prior_under, ('id', 'version', 'catalog_sha256'))
            if under.get('status') == 'error' or len(under.get('pockets') or {}) != 10:
                result['errors'].append('ТМ: неполный или ошибочный журнал')
            else:
                result['under_started_at_utc'] = under.get('started_at_utc')
                result['under_capture_errors'] = under.get('capture_errors', 0)
                for key, row in under['pockets'].items():
                    old = prior_under.get('pockets', {}).get(key) if same_under else None
                    if old and row['n'] < old['n']:
                        result['errors'].append('ТМ: уменьшилось число рассчитанных матчей')
                    add('under_pockets/' + key, row['name'], 'under', row, old)
                result['under'].sort(key=lambda row: (row['roi_without_top3'] is None,
                    -(row['roi_without_top3'] or 0), row['roi'] is None, -(row['roi'] or 0), row['name']))
        comparison = report.get('tracked_sot')
        if comparison is not None:
            prior_comparison = previous.get('tracked_sot') or {}
            same_comparison = compatible(comparison, prior_comparison, ('id', 'version', 'catalog_sha256'))
            try:
                if comparison.get('status') == 'error':
                    raise ValueError('Comparison error')
                arms = comparison['arms']
                if set(arms) != {'baseline', 'enhanced'} or any(len(a['pockets']) != 10 for a in arms.values()):
                    raise ValueError('Incomplete comparison')
                paired = []
                for key, baseline in sorted(arms['baseline']['pockets'].items(), key=lambda item:item[1]['priority']):
                    pair = dict(name=baseline['name'])
                    for arm in ('baseline', 'enhanced'):
                        row = dict(arms[arm]['pockets'][key])
                        old = ((prior_comparison.get('arms') or {}).get(arm, {}).get('pockets') or {}).get(key) if same_comparison else None
                        row['new_closed'] = row['n'] - old['n'] if old else None
                        if row['new_closed'] is not None and row['new_closed'] < 0:
                            raise ValueError('Comparison sample shrank')
                        pair[arm] = row
                    paired.append(pair)
                result['sot_comparison'] = dict(started_at_utc=comparison['started_at_utc'],
                    status=comparison['status'], capture_errors=comparison.get('capture_errors', 0),
                    rows=paired, portfolios={arm: arms[arm]['portfolio'] for arm in arms})
            except (KeyError, TypeError, ValueError):
                result['errors'].append('Сравнение отбора по створам: неполный или ошибочный журнал')
        return result

    current, old_current = report.get('current') or {}, previous.get('current') or {}
    if 'standard_v8' in current:
        old = old_current.get('standard_v8') if compatible(
            report.get('statistics_epoch') or {}, previous.get('statistics_epoch') or {},
            ('id', 'rules_version')) else None
        add('standard_v8', 'Контрольная пятёрка', 'over', current['standard_v8'], old)
    else:
        result['errors'].append('Контрольная пятёрка: данные отсутствуют')

    sections = (
        ('independent_pockets', 'Самостоятельные ТБ', 'over', ''),
        ('distinct_pockets', 'Отдельные сочетания', None, 'Сочетание: '),
        ('seven_direction_pockets', 'Новые карманы ТБ', None, 'Новый: '),
        ('refined_pockets', 'Двадцать уточнений ТБ', None, 'Уточнение: '),
        ('external_pockets', 'Внешние гипотезы ТБ', None, 'Гипотеза: '),
        ('strengthened_pockets', 'Усиление кармана ТБ', None, 'Усиление: '),
        ('hypothesis_pockets', 'Атомарные гипотезы ТБ', None, 'Гипотеза: '),
        ('historical_groups', 'Новые 30 групп ТБ', 'over', 'Новая группа: '),
    )
    for key, title, market, prefix in sections:
        section, prior = report.get(key) or {}, previous.get(key) or {}
        if key in ('refined_pockets', 'external_pockets', 'strengthened_pockets', 'hypothesis_pockets', 'historical_groups') and key not in report:
            continue
        if section.get('status') == 'error' or not isinstance(section.get('pockets'), dict):
            result['errors'].append(title + ': ' + section.get('error_type', 'данные отсутствуют'))
            continue
        same = compatible(section, prior, ('id', 'version', 'catalog_sha256'))
        continuity = (section.get('continuity_backfilled') and
                      section.get('predecessor_id') == prior.get('id') and
                      section.get('predecessor_catalog_sha256') == prior.get('catalog_sha256'))
        for pocket_id, row in section['pockets'].items():
            if pocket_id in (report.get('paused_pockets') or {}).get(key, []):
                continue
            side = market or row.get('market')
            if side not in ('over', 'under'):
                result['errors'].append(title + ': неизвестный рынок ' + pocket_id)
                continue
            name = row['name']
            if prefix and name.startswith(('ТБ ', 'ТМ ')):
                name = name[3:]
            old = (prior.get('pockets') or {}).get(pocket_id) if same or continuity else None
            add(key + '/' + pocket_id, prefix + name, side, row, old)

    quality, old_quality = report.get('quality_comparison'), previous.get('quality_comparison') or {}
    if quality:
        if quality.get('status') == 'error':
            result['errors'].append('Эксперимент качества: ' + quality.get('error_type', 'ошибка'))
        else:
            same = compatible(quality, old_quality, ('id', 'version', 'model_sha256'))
            for key, name in (('benchmark', 'контроль'), ('candidate', 'новый допуск')):
                row = (quality.get('progress') or {}).get(key)
                if row is not None:
                    old = (old_quality.get('progress') or {}).get(key) if same else None
                    add('quality/' + key, 'Эксперимент качества: ' + name, 'over', row, old)

    shadow, old_shadow = report.get('shadow_universe'), previous.get('shadow_universe') or {}
    if shadow:
        if shadow.get('status') == 'error':
            result['errors'].append('Широкий пул: ' + shadow.get('error_type', 'ошибка'))
        elif shadow.get('total'):
            old = old_shadow.get('total') if compatible(shadow, old_shadow, ('version', 'started_at_utc')) else None
            add('shadow_universe', 'Широкий пул с котировками, справочно', 'over', shadow['total'], old)

    def order(row):
        value = row['roi_without_top3']
        return (value is None, -(value or 0),
                row['roi'] is None if value is None else False,
                -(row['roi'] or 0) if value is None else 0, row['name'].casefold())
    result['over'].sort(key=order)
    result['under'].sort(key=order)
    return result


def signed(value):
    rounded = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    text = f'{rounded:+.2f}' if rounded else '0.00'
    return text.replace('-', '−').replace('.', ',')


def percent(value):
    return signed(value * 100) + '%' if value is not None else 'н/д'


def clock(value):
    if not value:
        return 'н/д'
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(MSK).strftime('%d.%m, %H:%M МСК')


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def count(value):
    return f'{value:,}'.replace(',', ' ')


def render_statistics(report, previous=None, tables=None):
    tables = tables if tables is not None else build_tables(report, previous)
    lines = ['**Срез: ' + clock(report['generated_at_utc']) + '.** Сравнение с ' +
             clock(tables['reference_at_utc']) + '.', '']
    if tables.get('tracking_series'):
        lines.extend(['Новая серия десяти групп с ' + clock(tables.get('series_started_at_utc')) +
                      '. Прежние входы и результаты в неё не переносятся.', ''])
    health = report.get('acquisition_health') or {}
    if health.get('status') == 'stale':
        last = clock(health.get('last_successful_request_at_utc'))
        lines.extend(['**Внимание: сбор матчей устарел. Последний успешный запрос: ' + last +
                      '. Таблица обновляет исходы ранее собранных матчей; свежий отбор не подтверждён.**', ''])
    population = tables.get('population') or {}
    def population_count(key):
        row = population[key]
        delta = row['delta']
        return count(row['n']) + (f" ({delta:+d})" if delta is not None else " (н/д)")
    if population:
        lines.extend([f"Всего наблюдалось {population_count('unique_observed')} уникальных матчей, "
                      f"подтверждённо завершены {population_count('confirmed_closed')}, "
                      f"из них {population_count('closed_with_provider_price')} с сохранённой ценой.", ''])
    lines.extend(['N - рассчитанные матчи; в скобках прирост к предыдущему отчёту. '
                  'Открытые исключены из ROI. При N≤30 ROI без трёх не выводится: '
                  'такие группы стоят внизу по убыванию обычного ROI.', ''])
    for market, title, metric in [('over', 'ТБ', 'без 3')] + ([('under', 'ТМ', 'без 3')] if tables.get('under') else []):
        if market == 'under':
            lines.extend(['Серия ТМ с ' + clock(tables.get('under_started_at_utc')) + '.', ''])
        lines.extend([f'**Все {title} - по убыванию ROI без трёх**', '',
                      f'| Группа | N (+новые) | ROI | ROI без 3 | Δ {metric}, п.п. | Не рассчитано |',
                      '|---|---:|---:|---:|---:|---:|'])
        for row in tables[market]:
            growth = f" ({row['new_closed']:+d})" if row['new_closed'] is not None else ''
            change = (signed(row['delta_pp']) if row['delta_pp'] is not None else
                      'первый результат' if row['first_result'] else 'н/д')
            lines.append(f"| {cell(row['name'])} | {count(row['n'])}{growth} | {percent(row['roi'])} | "
                         f"{percent(row['roi_without_top3'])} | {change} | {row.get('unresolved') if row.get('unresolved') is not None else 'н/д'} |")
        lines.append('')
    lines.extend(['Δ - изменение ROI без трёх лучших, в процентных пунктах. '
                  'При отсутствии сопоставимой предыдущей базы - н/д. Группы пересекаются, '
                  'их N и ROI не складываются. ' + ('Прежние группы сохранены в архиве. '
                  if tables.get('tracking_series') else 'Контроль и широкий пул '
                  'имеют собственные условия и периоды: сортировка не доказывает превосходство метода. '
                  ) + 'ROI без трёх исключает три лучших результата по прибыли.', ''])
    if tables.get('under_capture_errors'):
        lines.extend([f"Ошибочных проходов ТМ за период: {tables['under_capture_errors']}. Пропуски сохраняются в учёте.", ''])
    comparison = tables.get('sot_comparison')
    if comparison:
        lines.extend(['**ТБ: обычный отбор и достаточный темп ударов в створ**', '',
            'Обе версии учитывают только новые входы с ' + clock(comparison['started_at_utc']) +
            '. Усиление: суммарные створы / минута ≥0,10. У каждой версии свой первый вход и своя цена.', ''])
        if comparison['status'] == 'awaiting_first_successful_capture':
            lines.extend(['Первый успешный снимок для сравнения пока не сохранён.', ''])
        if comparison['capture_errors']:
            lines.extend([f"Ошибочных проходов сравнения: {comparison['capture_errors']}. Они не считаются проходами без сигналов.", ''])
        lines.extend(['| Карман | Обычный N (+новые) | Прибыль | ROI | Со створами N (+новые) | Прибыль | ROI | Не рассчитано обычный / со створами |',
                      '|---|---:|---:|---:|---:|---:|---:|---:|'])
        for pair in comparison['rows'] + [dict(name='Итого: уникальные матчи', **comparison['portfolios'])]:
            cells = []
            for arm in ('baseline', 'enhanced'):
                row = pair[arm]
                growth = f" ({row['new_closed']:+d})" if row.get('new_closed') is not None else ''
                cells.extend([count(row['n']) + growth, signed(row['unit_profit']), percent(row['roi'])])
            pending = f"{pair['baseline']['unresolved']} / {pair['enhanced']['unresolved']}"
            lines.append('| ' + cell(pair['name']) + ' | ' + ' | '.join(cells) + ' | ' + pending + ' |')
        lines.extend(['', 'Прибыль в единицах фиксированной ставки. Итог каждой версии считает один первый вход на матч '
                      'по всем карманам; строки не складываются. Это бумажное сравнение, преимущество фильтра пока не доказано.', ''])
    quality = report.get('quality_comparison') or {}
    progress = quality.get('progress') or {}
    if progress:
        predicted = number(progress.get('predicted_remaining'))
        predicted_text = f'{predicted:.0f}' if predicted is not None else 'н/д'
        state = 'критерии проверки выполнены' if progress.get('evidence_ready') else 'проверка продолжается'
        lines.extend([f"Эксперимент качества с {clock(quality.get('started_at_utc'))}: "
                      f"{state}; дней входов {progress.get('entry_days', 'н/д')}. "
                      f"Прогноз оставшихся голов {predicted_text}, "
                      f"факт {progress.get('observed_remaining', 'н/д')}.", ''])
    if tables['errors']:
        lines.extend(['Ошибки разделов: ' + '; '.join(map(cell, tables['errors'])) + '.', ''])
    foundation = report.get('research_foundation')
    if foundation:
        lines.extend(['**Независимый сбор и проверка цены**', ''])
        cumulative = foundation.get('cumulative')
        if cumulative:
            lines.extend([f"С начала новой схемы {clock(cumulative.get('started_at_utc'))}: "
                f"{count(cumulative['points'])} наблюдений в {count(cumulative['unique_matches'])} матчах. "
                f"Независимая выборка: {count(cumulative['independent_sample'])} точек в {count(cumulative['independent_matches'])} матчах. "
                f"Согласованная свежая цена ТБ/ТМ: {count(cumulative['fresh_quote_points'])} точек. "
                f"Сопоставление источников: {count(cumulative['comparable_prices'])} из {count(cumulative['comparison_requested'])} проверок. "
                f"Успешных проходов {cumulative['successful_captures']}, ошибочных {cumulative['capture_errors']}. "
                f"Допущенных входов {cumulative['admitted']}.", ''])
        if foundation.get('status') == 'active':
            lines.extend([f"Последний проход: {clock(foundation.get('observed_at_utc'))}. "
                          f"Наблюдений {foundation['points']}, независимая подробная выборка {foundation['independent_sample']}. "
                          f"Сравнение источников: {foundation['comparable_prices']} сопоставимых из {foundation['comparison_requested']} проверенных матчей.",
                          'Допуска к ставкам нет: модель не утверждена, цена исполнения пользователя не подключена. '
                          'Сохраняются данные и причины отказов; это не новая прибыльная стратегия.', ''])
        elif foundation.get('status') == 'error':
            lines.extend(['Последний проход новой схемы завершился ошибкой. Это не отсутствие подходящих сигналов.', ''])
        else:
            lines.extend(['Новая схема включена; первый сохранённый проход пока не подтверждён.', ''])
    independent = report.get('independent_candidates')
    if independent:
        lines.extend(['**Бумажное наблюдение ТБ и ТМ**', '',
            'Начало новых входов ' + clock(independent['activation_utc']) + '. Фиксированный порог ударов 0,3 в минуту. '
            'Первые две строки используют случайную выборку. Четыре новых кармана используют все пригодные наблюдения. '
            'Для каждого правила берётся первый подходящий снимок матча со свежей ценой и подтверждённой паузой после гола.', '',
            '| Правило | Начало, МСК | Рассчитано | Прибыль | ROI | Не рассчитано |',
            '|---|---|---:|---:|---:|---:|'])
        for row in independent['arms'].values():
            lines.append(f"| {cell(row['name'])} | {clock(row.get('activation_utc', independent['activation_utc']))} | {row['n']} | {signed(row['unit_profit'])} | {percent(row['roi'])} | {row['pending']} |")
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    current = json.loads(args.report.read_text())
    previous = json.loads(args.previous.read_text()) if args.previous else None
    print(render_statistics(current, previous), end='')

