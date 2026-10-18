"""Prospective research labels only; never admission, ranking or API calls."""
import math
from live_enrichment import number, score_goals

VERSION = 'live-hypotheses-2026-10-19-v2-provider-main'
DEFINITIONS = {
    'H1': 'Один гол и двусторонняя угроза',
    'H2': 'Начало второго тайма',
    'H3': 'Один гол и умеренный тотал',
}


def finite(value):
    value = number(value)
    return value if value is not None and math.isfinite(value) else None


def conjunction(*values):
    if False in values:
        return False
    return None if None in values else True


def evaluate(event):
    """Missing data remains unknown. Display and reference markets stay separate."""
    goals = score_goals(event.get('score'))
    minute = finite(event.get('minute'))
    on = (event.get('board_stats') or {}).get('on_target')
    on = [finite(x) for x in on] if isinstance(on, (list, tuple)) and len(on) == 2 else [None, None]
    both = all(x >= 1 for x in on) if all(x is not None and x >= 0 for x in on) else None
    one = goals == 1 if goals is not None else None
    window = 46 <= minute <= 60 if minute is not None else None
    reference = event.get('events_main_total') or {}
    line = finite(reference.get('line')) if event.get('main_total_status') == 'available' else None
    if line is not None and (line < 0 or abs(line * 4 - round(line * 4)) > 1e-8):
        line = None
    quarter = line is not None and round(line * 4) % 2 != 0
    rounded_down_line = line - .25 if quarter else line
    price = finite(reference.get('over')) if line is not None else None
    price = price if price is not None and price > 1 else None
    reference_need = (math.floor(line + (.25 if quarter else 0)) + 1 - goals
                      if line is not None and goals is not None else None)
    rounded_down_need = (math.floor(rounded_down_line) + 1 - goals
                         if rounded_down_line is not None and goals is not None else None)

    def labels(required):
        two = required == 2 if required is not None else None
        return {'H1': conjunction(one, both), 'H2': conjunction(window, two),
                'H3': conjunction(one, two)}

    flags = labels(reference_need)
    return {
        'version': VERSION, 'scope': 'prospective_research_only',
        'matched': [key for key, value in flags.items() if value is True],
        'unknown': [key for key, value in flags.items() if value is None],
        'flags': flags, 'reference_flags': labels(reference_need),
        'inputs': {'score': event.get('score'), 'minute': minute, 'on_target': on},
        'display_market': {'line': line, 'over': price,
                           'additional_goals_for_full_win': reference_need,
                           'price_status': 'exact' if price is not None else 'проверить',
                           'conditional_roi_eligible': price is not None and reference_need is not None,
                           'market_basis': 'provider_main_total_exact'},
        'rounded_down_reference': {'line': rounded_down_line if quarter else None,
                                   'additional_goals_for_full_win': rounded_down_need if quarter else None,
                                   'over': None, 'calculation_effect': False},
        'reference_market': {'line': line, 'over': price,
                             'additional_goals_for_full_win': reference_need,
                             'source': reference.get('source') if line is not None else None},
        'entry_confirmed': False,
    }
