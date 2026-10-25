"""Build and execute a portable notebook using a plain Python cell runner.
No Jupyter packages are installed in this environment; the notebook is also
rendered from its saved cell outputs for visual review.
"""
import json,io,contextlib,base64,html,os,hashlib,gzip
from pathlib import Path
ROOT=Path(__file__).resolve().parent
reps=json.loads((ROOT/'representatives.json').read_text())
with gzip.open(ROOT/'representatives.json.gz','wt') as f:json.dump(reps,f,ensure_ascii=False,separators=(',',':'))
def md(s):return dict(cell_type='markdown',metadata={},source=s)
def code(s):return dict(cell_type='code',metadata={},source=s,execution_count=None,outputs=[])
cells=[md('# Аудит семи направлений поиска карманов\n\n49 731 точка, 3 961 матч, 3 628 пригодных финалов. Срез исходников 25.10.2026 22:38 МСК. 42 468 правил/политик, 37 864 различных набора входов.\n\n92 варианта прошли три временные части; 83 сохранились после дополнительных диагностик, представлены 32 группами. **После поправки на перебор статистически подтверждённых преимуществ нет.** Период 24–25 октября уже просматривался и не является независимым будущим тестом.'),md('## Контекст и метод\n\nВсе сохранённые валидные основные FT-линии обеих сторон, включая наблюдения вне топ-5. Первый подходящий вход на матч и правило; задержки ≥3/≥5 минут проверяются по реально следующей котировке с тем же счётом и повторной проверкой условий. Asian-четверти и возвраты учитываются отдельно.\n\nРанняя часть: до 22 октября МСК; средняя: 22–23 октября; поздняя: 24–25 октября. Матч целиком относится к одной части. Финалы, ещё не известные к границе обучения, исключены из соответствующего обучения. Точные условия находятся в protocol.json и исходном отчёте Markdown.\n\nОтсутствие данных о стадии кубка и подтверждённых финалов для старого архива соседних линий означает ограничение аудита, а не нулевую доходность.'),code("""import json, gzip
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
ROOT = Path.cwd()
summary = json.loads((ROOT/'summary.json').read_text())
checks = json.loads((ROOT/'validation.json').read_text())
families = json.loads((ROOT/'family_results.json').read_text())
short = json.loads((ROOT/'shortlist.json').read_text())
print('Проверок:', sum(summary['evaluated_rules'].values()))
print('Различных наборов входов:', summary['unique_entry_selections'])
print('После диагностики:', checks['concentration_freshness_pass'])
print('Представителей:', checks['representative_count'])
assert summary['statistical_pass'] == 0
"""),md('## Результаты по направлениям'),code("""table = pd.DataFrame([{'Направление':r['name'], 'Проверок':r['evaluated_rules'],
                       'Три части +':r['stable_variants'],
                       'После диагностики':r['additional_diagnostic_pass']} for r in families])
print(table.to_string(index=False))
print('Соседние линии: финансовый расчёт невозможен без пригодных финалов.')
"""),md('## Восемь приоритетных кандидатов\n\nЭто отбор после просмотра результатов, а не новый тест. Номера соответствуют условиям в shortlist.json и основном отчёте.'),code("""table = pd.DataFrame([{'№':i+1, 'N':r['total']['n'],
    'Ранний ROI %':round(100*r['folds'][0]['roi'],2),
    'Средний ROI %':round(100*r['folds'][1]['roi'],2),
    'Поздний ROI %':round(100*r['folds'][2]['roi'],2),
    'Поздний без 3 %':round(100*r['folds'][2]['without_top3'],2),
    'Поздний без лиги и 3 %':round(100*r['late_without_best_league']['without_top3'],2)} for i,r in enumerate(short)])
print(table.to_string(index=False))
"""),code("""names=['ТМ: сравнивающий гол', 'ТБ: исходный тотал не изменился',
       'ТБ: лидер забил, четвертная линия', 'ТБ: тотал вырос на 0,25–0,75',
       'ТБ: два гола, тотал вырос на 1+', 'ТБ: тотал снизился, мало опасных атак',
       'ТМ: ничья после 15 минут без голов', 'ТБ: домашний фаворит ведёт']
fig, ax = plt.subplots(figsize=(12,7))
y=np.arange(len(short)); colors=['#5976a3','#5c9c83','#d69b4b']
for f, name in enumerate(['До 22 октября', '22–23 октября', '24–25 октября']):
    ax.barh(y+(f-1)*.22,[100*r['folds'][f]['roi'] for r in short],height=.20,label=name,color=colors[f])
ax.set_yticks(y,names); ax.invert_yaxis(); ax.axvline(0,color='#333333',lw=.8)
ax.set_xlabel('ROI, %'); ax.set_title('Описательные кандидаты: ROI в трёх частях истории')
ax.grid(axis='x',alpha=.18); ax.legend(loc='lower right'); fig.tight_layout()
plt.show()
"""),md('## Независимая проверка расчёта\n\nВходы представителей доступны в representative_ledger.json.gz с исходным путём и blob SHA. Проверка ниже заново считает четвертные исходы через Decimal и сверяет суммы с таблицей представителей.'),code("""from decimal import Decimal as D, ROUND_FLOOR
ledger=json.load(gzip.open(ROOT/'representative_ledger.json.gz'))
representatives=json.load(gzip.open(ROOT/'representatives.json.gz'))
totals={}; count=0
for r in ledger:
    if r['final'] is None: continue
    line=D(str(r['line'])); price=D(str(r['price'])); final=D(str(sum(r['final'])))
    low=(line*2).to_integral_value(rounding=ROUND_FLOOR)/2
    legs=[line] if line*2==(line*2).to_integral_value() else [low,low+D('.5')]
    result=sum(price-1 if (final>x if r['side']=='over' else final<x) else D(0) if final==x else D(-1) for x in legs)/len(legs)
    assert abs(float(result)-r['profit'])<1e-9
    count+=1
    if r['temporally_usable']:totals.setdefault(r['representative'],[]).append(float(result))
for i,r in enumerate(representatives,1):
    assert len(totals[i])==r['total']['n']
    assert abs(sum(totals[i])-r['total']['net'])<1e-8
print('Проверено сохранённых расчётов:',count)
print('Суммы и N всех представителей совпали.')
"""),md('## Вывод\n\nНаиболее интересны наблюдаемые реакции после гола и путь прематчевой линии к текущей. По отдельным лигам не получено устойчивых кандидатов; тип соревнования часто повторяет общий сценарий и не добавляет преимущества. Все найденные плюсы остаются ретроспективными. Действующие правила сбора и результаты не менялись.\n\nДля полного воспроизведения исходной сетки: SCANNER_GIT_DIR указывает на клон Scaner, затем acquire.py → extract.py → derive.py → search.py → validate.py → report.py. Этот ноутбук проверяет сохранённую доказательную выборку и визуализирует результаты.')]
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
namespace={'__name__':'__main__'};current=None
def show():
    for n in plt.get_fignums():
        fig=plt.figure(n);buf=io.BytesIO();fig.savefig(buf,format='png',dpi=140,bbox_inches='tight');raw=buf.getvalue()
        current['outputs'].append(dict(output_type='display_data',data={'image/png':base64.b64encode(raw).decode(),'text/plain':['<Figure>']},metadata={}))
        (ROOT/'Проверка кандидатов.png').write_bytes(raw)
    plt.close('all')
plt.show=show
oldcwd=Path.cwd();os.chdir(ROOT)
try:
    count=0
    for i,c in enumerate(cells):
        c['id']=hashlib.sha256(str(i).encode()).hexdigest()[:12]
        if c['cell_type']!='code':continue
        count+=1;c['execution_count']=count;current=c;out=io.StringIO()
        with contextlib.redirect_stdout(out):exec(compile(c['source'],f'notebook_cell_{i}','exec'),namespace)
        if out.getvalue():c['outputs'].insert(0,dict(output_type='stream',name='stdout',text=out.getvalue()))
finally:os.chdir(oldcwd)
nb=dict(cells=cells,metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},'language_info':{'name':'python','version':'3.12'},'execution_note':'Executed sequentially with a plain Python cell runner; no Jupyter kernel was launched.'},nbformat=4,nbformat_minor=5)
assert all(c['cell_type'] in ('markdown','code') and isinstance(c['source'],str) for c in nb['cells'])
assert all(c['execution_count'] is not None and c['outputs'] for c in nb['cells'] if c['cell_type']=='code')
(ROOT/'Проверка семи направлений.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=1))
parts=['<html><meta charset="utf-8"><style>body{max-width:1150px;margin:35px auto;font-family:serif}pre{white-space:pre-wrap;background:#f4f4f4;padding:14px}img{max-width:100%}</style>']
for c in cells:
    if c['cell_type']=='markdown':parts.append('<pre>'+html.escape(c['source'])+'</pre>')
    else:
        for o in c['outputs']:
            if o['output_type']=='stream':parts.append('<pre>'+html.escape(o['text'])+'</pre>')
            elif 'image/png' in o.get('data',{}):parts.append('<img src="data:image/png;base64,'+o['data']['image/png']+'">')
parts.append('</html>');(ROOT/'Проверка семи направлений.html').write_text('\n'.join(parts))
print('NOTEBOOK EXECUTED',count,'cells')
