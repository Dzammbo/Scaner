"""Canonical chat presentation of frozen statistics; never changes the ledgers."""
import argparse
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path

VERSION = 'statistics-over-only-20261026-v3'
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

    def add(key, label, market, row, old=None):
        # Archived under entries never enter the active report, including old JSON.
        if market != 'over':
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
            first_result=bool(old and old['n'] == 0 and n > 0 and roi is not None)))

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
    )
    for key, title, market, prefix in sections:
        section, prior = report.get(key) or {}, previous.get(key) or {}
        if section.get('status') == 'error' or not isinstance(section.get('pockets'), dict):
            result['errors'].append(title + ': ' + section.get('error_type', 'данные отсутствуют'))
            continue
        same = compatible(section, prior, ('id', 'version', 'catalog_sha256'))
        continuity = (section.get('continuity_backfilled') and
                      section.get('predecessor_id') == prior.get('id') and
                      section.get('predecessor_catalog_sha256') == prior.get('catalog_sha256'))
        for pocket_id, row in section['pockets'].items():
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
    population = report.get('population') or {}
    if population:
        lines.extend([f"Всего наблюдалось {count(population['unique_observed'])} уникальных матчей, "
                      f"подтверждённо завершены {count(population['confirmed_closed'])}, "
                      f"из них {count(population['closed_with_provider_price'])} с сохранённой ценой.", ''])
    lines.extend(['N - рассчитанные матчи; в скобках прирост к предыдущему отчёту. '
                  'Открытые исключены из ROI. При N≤30 ROI без трёх не выводится: '
                  'такие группы стоят внизу по убыванию обычного ROI.', ''])
    for market, title, metric in (('over', 'ТБ', 'без 3'),):
        lines.extend([f'**Все {title} - по убыванию ROI без трёх**', '',
                      f'| Группа | N (+новые) | ROI | ROI без 3 | Δ {metric}, п.п. |',
                      '|---|---:|---:|---:|---:|'])
        for row in tables[market]:
            growth = f" ({row['new_closed']:+d})" if row['new_closed'] is not None else ''
            change = (signed(row['delta_pp']) if row['delta_pp'] is not None else
                      'первый результат' if row['first_result'] else 'н/д')
            lines.append(f"| {cell(row['name'])} | {count(row['n'])}{growth} | {percent(row['roi'])} | "
                         f"{percent(row['roi_without_top3'])} | {change} |")
        lines.append('')
    lines.extend(['Δ - изменение ROI без трёх лучших, в процентных пунктах. '
                  'При отсутствии сопоставимой предыдущей базы - н/д. Группы пересекаются, '
                  'их N и ROI не складываются. Контроль, широкий пул и эксперимент качества '
                  'имеют собственные условия и периоды: сортировка не доказывает превосходство метода. '
                  'ТМ приостановлен и исключён из этого контура; история сохранена.', ''])
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
    return '\n'.join(lines).rstrip() + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    current = json.loads(args.report.read_text())
    previous = json.loads(args.previous.read_text()) if args.previous else None
    print(render_statistics(current, previous), end='')
