"""Reproducible contribution audit and complete fixed slice grid. No network or rule tuning."""
import collections,datetime,decimal,hashlib,itertools,json,math,re,subprocess
from pathlib import Path
import capture as c
from lab import full_win_total,settle
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
NAMES={
 'all':'Вся база','standard_v8':'Стандартный отбор v8','sports_v8_fresh':'Спортивный отбор исследовательского пула',
 'provider_main_total_exact':'Основной тотал с точной ценой','provider_main_need_le2':'Нужно ≤2 голов',
 'provider_main_need_le3':'Нужно ≤3 голов','provider_main_ev_ge005':'EV ≥0,05',
 'provider_main_sot_rate_010_020':'Темп створов 0,1–0,2','provider_main_minute_46_60':'Минута 46–60',
 'provider_main_score_one':'Ровно один гол','provider_main_integer_line':'Целая линия','provider_main_danger_rate_lt1':'Опасные атаки <1/мин',
 'combo_sot_minute':'Створы + 46–60','combo_sot_need2':'Створы + ≤2','combo_minute_need2':'46–60 + ≤2',
 'combo_sot_score1':'Створы + один гол','combo_score1_need2':'Один гол + ≤2','combo_minute_score1':'46–60 + один гол',
 'combo_integer_need2':'Целая линия + ≤2','combo_minute_integer':'46–60 + целая линия',
 'combo_sot_minute_need2':'Створы + 46–60 + ≤2','combo_score1_minute_need2':'Один гол + 46–60 + ≤2'}
REGION_CODES={
 'Европа':set('ad al at ba be bg by ch cy cz de dk ee es eu fi fo fr gb ge gr hr hu ie is it lt lu lv md me mk mt nl no pl pt ro rs ru se si sk sm tr ua xk'.split()),
 'Западная Азия и Северная Африка':set('ae bh dz eg il iq ir jo kw lb ly ma om ps qa sa sy tn ye'.split()),
 'Америка':set('ar bo br ca cl co cr cu do ec gt hn ht jm mx ni pa pe pr py sv tt us uy ve'.split()),
 'Восточная и Юго-Восточная Азия':set('cn hk id jp kh kr la mm mn mo my ph sg th tl tw vn'.split()),
 'Прочие регионы':set('au bd bw ci cm et gh in ke kg kz lr mu mw ne ng np nz pk rw sn tj ug uz za zm zw'.split())}
REGIONS=list(REGION_CODES)+['Неизвестный регион']
MINUTES=['до 5','5–14','15–29','30–45','46–60','61–74','75–89','90+','неизвестно']
NEEDS=['≤2','3','≥4','неизвестно'];SOTS=['<0,1','0,1–0,2','≥0,2','неизвестно']

def region(country):
 return next((k for k,v in REGION_CODES.items()if str(country).lower()in v),'Неизвестный регион')

def minute_band(value):
 if value is None:return 'неизвестно'
 return 'до 5'if value<5 else '5–14'if value<15 else '15–29'if value<30 else '30–45'if value<46 else '46–60'if value<=60 else '61–74'if value<75 else '75–89'if value<90 else '90+'

def dimensions(r):
 need=r.get('need');sot=r.get('on_target_rate')
 return (region(r.get('country')),minute_band(r.get('minute')),'неизвестно'if need is None else '≤2'if need<=2 else '3'if need==3 else '≥4','неизвестно'if sot is None else '<0,1'if sot<.1 else '0,1–0,2'if sot<.2 else '≥0,2')

def independent_profit(final,line,price):
 # Independent Decimal implementation for cross-check, splitting into half-goal neighbours.
 D=decimal.Decimal;line=D(str(line));price=D(str(price));final=D(str(sum(final)))
 if line*2==(line*2).to_integral_value():legs=[line]
 else:
  lo=(line*2).to_integral_value(rounding=decimal.ROUND_FLOOR)/2;legs=[lo,lo+D('.5')]
 return float(sum(price-1 if final>x else D(0)if final==x else D(-1)for x in legs)/len(legs))

def stats(rows):
 values=[r['profit']for r in rows if r.get('profit')is not None];n=len(values)
 return dict(selected=len(rows),n=n,unit_profit=round(math.fsum(values),8),roi=math.fsum(values)/n if n else None,
  unique_matches=len({r['id']for r in rows}),positive=sum(x>0 for x in values),negative=sum(x<0 for x in values),push=sum(x==0 for x in values))

def contribution(rows,key):
 groups=collections.defaultdict(list)
 for r in rows:groups[str(r.get(key)or'неизвестно')].append(r)
 return {k:stats(v)for k,v in sorted(groups.items())}

def audit(rows):
 priced=[r for r in rows if r.get('profit')is not None];daily=contribution(priced,'day');leagues=contribution(priced,'league')
 best=max(daily,key=lambda k:daily[k]['unit_profit'])if daily else None
 top=sorted(priced,key=lambda r:(-r['profit'],r['id']))[:3]
 remove={id(r)for r in top}
 return dict(total=stats(rows),daily=daily,leagues=leagues,regions=contribution(priced,'region'),minute_windows=contribution(priced,'minute_window'),
  best_day=best,without_best_day=stats([r for r in priced if r['day']!=best]),without_top3=stats([r for r in priced if id(r)not in remove]),
  top3=[{k:r.get(k)for k in ('id','match','day','minute','score','line','odds','final','profit','league','source_path','snapshot_id')}for r in top],
  positive_days=sum(v['unit_profit']>0 for v in daily.values()),negative_days=sum(v['unit_profit']<0 for v in daily.values()))

def grid(rows):
 cells=collections.defaultdict(list)
 for r in rows:cells[dimensions(r)].append(r)
 output=[]
 for keys in itertools.product(REGIONS,MINUTES,NEEDS,SOTS):
  s=stats(cells[keys]);output.append(list(keys)+[s['selected'],s['n'],s['unit_profit'],s['roi']])
 assert sum(r[4]for r in output)==len(rows)
 assert sum(r[5]for r in output)==stats(rows)['n']
 assert abs(math.fsum(r[6]for r in output)-stats(rows)['unit_profit'])<1e-6
 return dict(columns=['region','minute_window','goals_needed','sot_rate','selected','priced_settled_n','unit_profit','roi'],
  axes=dict(region=REGIONS,minute_window=MINUTES,goals_needed=NEEDS,sot_rate=SOTS),cells=output,
  empty_cells=sum(r[4]==0 for r in output),nonempty_cells=sum(r[4]>0 for r in output))

def normalize(r):
 r=dict(r);r['region']=region(r.get('country'));r['minute_window']=minute_band(r.get('minute'))
 return r

def main():
 manifest={}
 def load(path):
  manifest[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest();return c.load_json(path)
 conflicts=set(map(str,load(ROOT/'research/selection-audit-20261018/results.json')['outcome_conflicts']))
 all_history=load(ROOT/'research/full-history-20261018/data.json.gz')['rows']['primary'];history=[];crosschecks=0
 for row in all_history:
  if str(row['id'])in conflicts:continue
  r=dict(row,source_path='research/full-history-20261018/data.json.gz',profit=None)
  if row.get('closed')and row.get('line')is not None and row.get('odds')is not None and isinstance(row.get('final'),list):
   r['profit']=settle(sum(row['final']),row['line'],row['odds'])
   assert abs(r['profit']-independent_profit(row['final'],row['line'],row['odds']))<1e-9
   assert row.get('profit')is None or abs(r['profit']-row['profit'])<1e-6
   crosschecks+=1
  history.append(normalize(r))
 def matches(policy,r):
  if policy=='all':return True
  if policy=='provider_main_need_le3':return r.get('need')is not None and r['need']<=3
  return c.pocket_matches(policy,dict(minute=r.get('minute'),goals=sum(r['score']),line=r.get('line'),on_target_per_minute=r.get('on_target_rate'),dangerous_attacks_per_minute=r.get('dangerous_attacks_rate')))
 hist_keys=['all','provider_main_need_le2','provider_main_need_le3']+list(c.POCKET_POLICIES)+list(c.COMBINATION_POLICIES)
 historical={p:[r for r in history if matches(p,r)]for p in hist_keys}
 bases=[ROOT/'research/live-background/primary',ROOT/'research/live-background/experimental',ROOT/'research/live-lab'];outcomes={};receipts=[]
 for base in bases:
  for path in sorted((base/'outcomes').rglob('*.json*')):
   for row in load(path).get('events',[]):
    eid=str(row['event_id']);old=outcomes.get(eid)
    if old is None or row['checked_at_utc']>old['checked_at_utc']:outcomes[eid]=row
  for path in sorted((base/'receipts').rglob('*.json*')):
   record=load(path)
   if not record.get('failed'):receipts.append((record.get('request',{}).get('requested_at_utc',''),base,path,record))
 selected={p:{}for p in ('standard_v8',)+c.EXPERIMENTAL_POLICIES};broad_first={};repeat={};raw_entries={};latest='';observed_ids=set();missing_snapshots=0
 for timestamp,base,path,receipt in sorted(receipts,key=lambda x:(x[0],str(x[2]))):
  snapshot_path=base/'snapshots'/path.relative_to(base/'receipts')
  if not snapshot_path.exists():missing_snapshots+=1;continue
  snapshot=load(snapshot_path);latest=max(latest,snapshot.get('board_observed_at_utc')or'');events={str(e['event_id']):e for e in snapshot.get('events',[])}
  observed_ids.update(events)
  def record(e,choice=None):
   nonlocal crosschecks
   q=choice.get('market')if choice is not None else c.market_for_event(e,snapshot)[0]
   score=(choice or e).get('score','');minute=(choice or e).get('minute');feat=c.pocket_features(dict(e,score=score,minute=minute),q)if re.fullmatch(r'\d+-\d+',str(score))else{}
   eid=str(e['event_id']);outcome=outcomes.get(eid,{})
   entry=dict(score=score,market=q);profit=c.settle_entry(entry,outcome)if re.fullmatch(r'\d+-\d+',str(score))else None
   if profit is not None:
    assert abs(profit-independent_profit(outcome['final'],q['line'],q['price']))<1e-9;crosschecks+=1
   time=snapshot['board_observed_at_utc'];day=datetime.datetime.fromisoformat(time).astimezone(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat()
   return normalize(dict(id=eid,match=e.get('home','')+' - '+e.get('away',''),day=day,time=time,minute=minute,score=score,
    line=q['line']if q else None,odds=q['price']if q else None,need=full_win_total(q['line'])-feat['goals']if q and feat else None,
    on_target_rate=feat.get('on_target_per_minute'),dangerous_attacks_rate=feat.get('dangerous_attacks_per_minute'),country=e.get('country'),
    league=e.get('league'),category=e.get('category'),top5_benchmark=(choice or{}).get('top5_benchmark'),entry_version=(choice or{}).get('entry_version'),profit=profit,final=outcome.get('final'),source_path=str(snapshot_path.relative_to(ROOT)),snapshot_id=snapshot['snapshot_id']))
  for eid,e in events.items():
   # First *priced* fresh deep observation, not first raw row; explicit separate population.
   if eid not in broad_first and(e.get('stats_trend_shortlisted')or e.get('control_shortlisted')or e.get('repeat_control_shortlisted'))and e.get('minute')is not None and 5<=e['minute']<=89 and re.fullmatch(r'\d+-\d+',str(e.get('score',''))):
    r=record(e)
    if r['line']is not None:broad_first[eid]=r
  groups=dict(receipt.get('choices',{}))
  if base.name=='primary':groups={'standard_v8':groups.get('sports_v8_fresh',[])}
  else:groups['standard_v8']=receipt.get('control_evaluation',{}).get('choices',[])
  for p,rows in groups.items():
   if p not in selected:continue
   for choice in rows:
    eid=str(choice['event_id'])
    if eid not in selected[p]:
     assert eid in events,(p,eid);selected[p][eid]=record(events[eid],choice)
  for p,rows in receipt.get('model_entries',{}).items():
   for entry in rows:
    raw_entries.setdefault(entry['entry_id'],entry)
    repeat.setdefault(entry['entry_id'],(p,entry.get('entry_version','unknown'),record(events[entry['event_id']],entry)))
 first=c.first_entries(raw_entries.values(),c.ENTRY_VERSION)
 first_groups={p:[]for p in selected}
 for entry in first:first_groups.setdefault(entry['model'],[]).append(repeat[entry['entry_id']][2])
 frozen_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 result=dict(generated_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_revision=frozen_head,latest_snapshot_utc=latest,
  definitions=dict(profit='net units, equal unit per entry; historical recorded FT line/price, current exact provider main FT line/price; quarter split',roi='sum(net units)/priced settled N',
   historical='primary first full-time records; exclude known outcome conflicts; all historical strategies exploratory',
   current='LEGACY first selection per model/event with original Top5 quota; do not merge with first_entries',
   first_entries=dict(version=c.FIRST_ENTRY_VERSION,source_entry_version=c.ENTRY_VERSION,rule='Earliest qualified exact-priced saved entry per model/event, no rank cap; selection ignores outcomes',first_observed_at_utc=min((e['observed_at_utc']for e in first),default=None),selected_entry_ids=[e['entry_id']for e in first],report_default=True),
   broad_grid='first priced fresh deep observation per event in 5-89; NOT all raw observed or only strong selections',
   repeated='model_entries only; versions separated; current all-qualified entries and their Top5 subset reported separately; no backfill; correlated entries',region='explicit country code only; no inference from league name; null/unmapped unknown; explicit provider eu maps Europe',
   region_map={k:sorted(v)for k,v in REGION_CODES.items()},region_convention='Turkey/Georgia/Cyprus in Europe for reproducible convention, no claim of causal grouping',
   grid='fixed bins, all Cartesian combinations including empty; no optimized thresholds',
   without_best_day='remove day with maximum NET PROFIT, recompute remaining N; not subtract from original N',
   without_top3='remove three highest net unit returns, recompute remaining N; no claim of formal significance'),
  quality=dict(historical_records=len(all_history),historical_after_conflicts=len(history),historical_conflicts=sorted(conflicts),
   observed_unique=len(observed_ids),broad_first_priced_unique=len(broad_first),missing_snapshot_files=missing_snapshots,independent_decimal_checks=crosschecks,
   historical_unknown_region=sum(r['region']=='Неизвестный регион'for r in history),outcome_events=len(outcomes)),
  historical={p:audit(rows)for p,rows in historical.items()},current={p:audit(list(rows.values()))for p,rows in selected.items()},
  first_entries={p:audit(rows)for p,rows in first_groups.items()},
  repeated={p:audit([r for policy,version,r in repeat.values()if policy==p and version==c.ENTRY_VERSION])for p in sorted({p for p,version,r in repeat.values()if version==c.ENTRY_VERSION})},
  repeated_by_version={version:{p:audit([r for policy,v,r in repeat.values()if policy==p and v==version])for p in sorted({policy for policy,v,r in repeat.values()if v==version})}for version in sorted({v for p,v,r in repeat.values()})},
  repeated_top5={p:audit([r for policy,version,r in repeat.values()if policy==p and version==c.ENTRY_VERSION and r.get('top5_benchmark')])for p in sorted({p for p,v,r in repeat.values()if v==c.ENTRY_VERSION})},
  slice_maps=dict(historical_all=grid(history),current_broad_first_priced=grid(list(broad_first.values()))),
  source_sha256=manifest)
 (HERE/'pocket-audit-results.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
 render(result)
 print(json.dumps(dict(quality=result['quality'],latest=latest,history={p:v['total']for p,v in result['historical'].items()},current={p:v['total']for p,v in result['current'].items()}),ensure_ascii=False))
 return result

def render(result):
 def pct(x):return 'нет данных'if x is None else f'{x*100:+.1f}%'
 out=['# Прибыль карманов и карта срезов','',f"Исходный коммит: {result['source_revision']}. Последний снимок: {result['latest_snapshot_utc']} (UTC).",'',
 'Условный флет 1 единица; ROI = чистый результат / число завершённых входов с точной ценой. N включает возвраты. Дни по Москве. Сегменты и пересечения не складываются в независимую выборку. Это исследование, не подбор новых правил по результату.','',
 'Старые первые выборы и новые повторные входы разделены. Карта широкого пула берёт первое свежее обогащённое наблюдение с ценой в 5–89, а не все сырые строки. Неизвестные страны не восстановлены догадкой по названию турнира. Названия турниров сохраняются как в источнике, русские и английские алиасы не объединяются автоматически.','']
 out+=['## Что показала проверка','']
 for key in ['provider_main_sot_rate_010_020','provider_main_integer_line','provider_main_need_le2']:
  v=result['historical'][key];out.append(f"- {NAMES[key]}: история {pct(v['total']['roi'])} (N={v['total']['n']}), без лучшего дня {pct(v['without_best_day']['roi'])} (N={v['without_best_day']['n']}), без трёх лучших {pct(v['without_top3']['roi'])} (N={v['without_top3']['n']}).")
 old=result['historical']['provider_main_need_le2']['minute_windows'].get('46–60',{})
 new=result['current']['provider_main_need_le2']['minute_windows'].get('46–60',{})
 out += ['',f"Окно 46–60 внутри ≤2: история {pct(old.get('roi'))} (N={old.get('n',0)}), новые первые выборы {pct(new.get('roi'))} (N={new.get('n',0)}). Разница наблюдаемая, но периоды и составы различаются; это не доказательство вреда позднего входа.",'',
 'Целые линии имеют заметную зависимость от лучшего дня. Темп створов выдержал эту проверку концентрации, но его короткая новая выборка пока не подтверждает историческую доходность. По этим результатам ничего не исключаем и условия моделей не меняем.','']
 for section,title in [('first_entries','Основной учёт: первый подходящий вход на матч, без лимита Top5'),('historical','История 13–18 октября, исследовательский срез'),('current','Архив: прежние первые выборы с лимитом Top5')]:
  out+=['## '+title,'','| Вариант | N | ROI | Без лучшего дня, N / ROI | Без трёх лучших, N / ROI |','|---|---:|---:|---:|---:|']
  for policy,v in result[section].items():
   t=v['total'];d=v['without_best_day'];w=v['without_top3'];out.append(f"| {NAMES.get(policy,policy)} | {t['n']} | {pct(t['roi'])} | {d['n']} / {pct(d['roi'])} | {w['n']} / {pct(w['roi'])} |")
  out+=['']
  for policy,v in result[section].items():
   if not v['total']['n']:continue
   out+=['### '+NAMES.get(policy,policy),'',f"Чистый результат {v['total']['unit_profit']:+.4f} ед.; положительных дней {v['positive_days']}, отрицательных {v['negative_days']}. Лучший день по прибыли: {v['best_day']}.",'',
    '| День | N | Чистый результат, ед. | ROI |','|---|---:|---:|---:|']
   for day,s in v['daily'].items():out.append(f"| {day} | {s['n']} | {s['unit_profit']:+.4f} | {pct(s['roi'])} |")
   out+=['','Вклад турниров, полный перечень:','','| Турнир | N | Чистый результат, ед. | ROI |','|---|---:|---:|---:|']
   for league,s in sorted(v['leagues'].items(),key=lambda kv:-kv[1]['unit_profit']):out.append(f"| {league.replace('|','/')} | {s['n']} | {s['unit_profit']:+.4f} | {pct(s['roi'])} |")
   out+=['','Три наибольших результата:','','| Матч | Минута | Линия входа | Цена | Итог | Результат, ед. |','|---|---:|---:|---:|---|---:|']
   for r in v['top3']:out.append(f"| {r['match']} ({r['id']}) | {r['minute']} | {r['line']} | {r['odds']} | {r['final']} | {r['profit']:+.4f} |")
   out+=['']
 out+=['## Карта срезов','',
  'Полная сетка регион × минута × нужные голы × темп створов сохранена в pocket-audit-results.json, slice_maps. Пустая ячейка: selected=0, N=0, ROI=null. Наблюдение без рассчитанного результата: selected>0, N=0. Пустота не означает минус.','',
  'Разрезы по регионам и минутам для всех первых выборов моделей находятся там же в current. Ниже карта широкого пула и истории, ячейки с N≥10. Порог отображения не является правилом отбора и не убирает остальные ячейки из данных.','']
 for key,g in result['slice_maps'].items():
  out += [f"### {key}",'',f"Всего ячеек {len(g['cells'])}, непустых {g['nonempty_cells']}, пустых {g['empty_cells']}.",'',
   '| Регион | Минута | Нужно голов | Створы/мин | N | ROI |','|---|---|---|---|---:|---:|']
  for row in sorted([r for r in g['cells']if r[5]>=10],key=lambda r:-r[5]):out.append('| '+' | '.join(map(str,row[:4]))+f' | {row[5]} | {pct(row[7])} |')
  out+=['']
 out+=['## Ограничения','',
  'Исторические карманы выбраны после известных результатов, поэтому их плюс не является независимым подтверждением. Новые варианты наблюдались короткое время, некоторые только в одном времени суток/регионе. Это описательная раскладка без причинных выводов и без поправки на множество проверенных срезов. Удаление лучшего дня/трёх выигрышей - проверка концентрации, не статистический тест.','',
  'Повторные входы по одному матчу коррелируют. Первый обогащённый вход в широком пуле зависит от маршрутизации и выборки; поздние окна нельзя считать равномерным экспериментом. Счёт и линия берутся из сохранённого входа; итоговые данные не используются как признаки прошлого. Регион по стране турнира не описывает силу лиги.','',
  'Полные определения, состав стран, SHA256 источников, N и финансовые суммы сохранены в pocket-audit-results.json. Незавершённые и непроверенные цены исключены из ROI. Ручной Live и правила моделей не менялись.']
 (HERE/'Разбор прибыли и срезов.md').write_text('\n'.join(out)+'\n')
if __name__=='__main__':main()
