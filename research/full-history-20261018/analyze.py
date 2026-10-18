"""Read-only full-history exploratory audit. No changes to Live or workbooks."""
import json,re,math,datetime,collections,sys,itertools
from pathlib import Path
import openpyxl,numpy as np
R=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
sys.path.insert(0,str(R/'scripts'))
from live_publications import merge_publications
MSK=datetime.timezone(datetime.timedelta(hours=3))
def read(p):return json.loads(p.read_text())
def pair(s):
 if isinstance(s,dict):s=[s.get('home'),s.get('away')]
 if isinstance(s,str):s=re.split('[:-]',s)
 try:return [int(x) for x in s] if len(s)==2 else None
 except:return None
def dt(s):
 s=str(s)
 for fmt in ['%d.%m.%Y %H:%M:%S','%d.%m.%Y %H:%M']:
  try:return datetime.datetime.strptime(s,fmt).replace(tzinfo=MSK)
  except ValueError:pass
 x=datetime.datetime.fromisoformat(s.replace('Z','+00:00'));return x.replace(tzinfo=MSK)if x.tzinfo is None else x.astimezone(MSK)
def settle(total,line,k):
 q=round(line*4);assert abs(line*4-q)<1e-6
 ls=[line-.25,line+.25]if q%2 else [line]
 return sum(k-1 if total>l else 0 if total==l else -1 for l in ls)/len(ls)
def num(x):
 try:return float(x)
 except:return None
def leaguegroup(l):
 l=l.lower()
 if re.search(r'women|женщ|u(?:17|18|19|20|21|23)|youth|юнош|reserve|резерв',l):return 'women_youth_reserve'
 if re.search(r'cup|copa|кубок|трофей|champions league|europa league|conference|лига чемпион',l):return 'cups'
 if re.search(r'терсера|tercera|regionalliga|landesliga|земельн|межрегион|iv liga|iv лига|iii liga|liga bet|serie c|j3-league|ligue 3|isthmian|division 3|дивизион 2|division 2 group|denmark division 2|segunda division rfef|mexico liga premier|primera b metropolitana|esiliiga b|munster senior|england league 1|3-я лига|второй дивизион',l):return 'lower_heuristic'
 return 'other_adult'
w=openpyxl.load_workbook(R/'Live ТБ/Live ТБ.xlsx',read_only=True,data_only=True)
a=list(w['Реестр'].values);h=next(i for i,r in enumerate(a)if r and r[0]=='№');book=[dict(zip(a[h],r))for r in a[h+1:]if r and isinstance(r[0],(int,float))]
ledger=merge_publications(read(R/'.github/live-selected-events.json'),R/'.github/live-publications')['selections']
snaps={p.stem:read(p)for p in (R/'snapshots/live').glob('*.json')}
events={(sid,str(e['event_id'])):e for sid,s in snaps.items()for e in s.get('events',[])}
results=read(R/'research/live-results-20261018/outcomes.json')['results'];results.update(read(OUT/'outcomes.json'))
window={x['id']:x for x in read(R/'reports/Расчёт рабочего окна 17 октября 2026.json')['records']}
rows=[]
for x in book:
 line=x['Линия ТБ']if x['Линия ТБ']is not None else x['Справочный ТБ, матч'];k=x['Коэффициент']if x['Коэффициент']is not None else x['Справочный коэффициент']
 if line is None:
  m=re.search(r'ТБ\s*(\d+(?:[.,]\d+)?)',str(x['Рынок']));line=float(m[1].replace(',','.'))if m else None
 rows.append(dict(id=str(x['ID события'])if x['ID события']else 'name:'+x['Матч'],match=x['Матч'],sid=x['ID снимка'],time=dt(x['Дата и время МСК']).isoformat(),minute=num(x['Минута']),score=pair(x['Счёт']),league=x['Турнир'],period='HT'if 'тайм'in str(x['Период рынка'])or'тайм'in str(x['Рынок'])else'FT',line=num(line),odds=num(k),final=pair(x['Финальный счёт']),book_profit=x['Расчётный итог, ед.']if x['Расчётный итог, ед.']is not None else x['Справочный итог, ед.'],source='book:'+str(x['№']),version=x['Версия правил'],stats_text=x['Статистика при отборе'],completeness=x['Полнота данных'],contour=x['Контур']))
added=0
for x in ledger:
 eid=str(x['event_id']);sid=x['snapshot_id']
 if any(r['id']==eid and (r['sid']==sid or (r['sid']is None and r['score']==pair(x['score'])and r['minute']==num(x['minute'])))for r in rows):continue
 ref=x.get('reference_main_total')or{};s=snaps.get(sid,{})
 rows.append(dict(id=eid,match=x['home']+' - '+x['away'],sid=sid,time=dt(x.get('board_observed_at_utc')or x['selected_at_utc']).isoformat(),minute=num(x['minute']),score=pair(x['score']),league=x['league'],period='FT',line=num(ref.get('line')),odds=num(ref.get('over')),final=None,book_profit=None,source='ledger:'+sid,version=x['rules_version'],stats_text='',completeness=None,contour='Основной'));added+=1
confirmed_added=0
for x in read(R/'research/live-results-20261018/comparison.json')['details']['published']:
 if any(r['id']==x['id'] and r['sid']==x['snapshot_id']for r in rows):continue
 e=events[(x['snapshot_id'],x['id'])];s=snaps[x['snapshot_id']];ref=x.get('reference')or{}
 rows.append(dict(id=x['id'],match=x['match'],sid=x['snapshot_id'],time=dt(s['board_observed_at_utc']).isoformat(),minute=num(e['minute']),score=pair(x['initial']),league=e['league'],period='FT',line=num(ref.get('line')),odds=num(ref.get('over')),final=x['final']if x['status']=='closed'else None,book_profit=x['profit'],source='confirmed_report:'+x['snapshot_id'],version=s['rules_version'],stats_text='',completeness=None,contour='Основной'));confirmed_added+=1
mismatches=[]
for r in rows:
 e=events.get((r['sid'],r['id']),{});s=snaps.get(r['sid'],{});r['snapshot_match']=bool(e)and pair(e['score'])==r['score']
 if e and not r['snapshot_match']:e={}
 if r['final']is None and r['period']=='FT'and r['id']in window:r['final']=pair(window[r['id']]['final']);r['result_source']='window_report'
 o=results.get(r['id'],{});api=pair((o.get('scores')or{}).get('1'if r['period']=='HT'else'2'))
 if api is None and r['period']=='FT'and not any(k in (o.get('scores')or{})for k in ['3','4']):api=pair(o.get('ss'))
 if str(o.get('time_status'))=='3'and api:
  if r['final']and r['final']!=api:mismatches.append({'kind':'final','id':r['id'],'old':r['final'],'api':api})
  r['final']=api;r['result_source']='BetsAPI';r['checked_at']=o.get('checked_at')
 r['closed']=r['final']is not None
 if r['closed']and any(a>b for a,b in zip(r['score'],r['final'])):r['closed']=False;r['result_conflict']=True;mismatches.append({'kind':'score_conflict_excluded','id':r['id'],'initial':r['score'],'final':r['final'],'source':r['source']})
 r['profit']=None;r['additional']=sum(r['final'])-sum(r['score'])if r['closed']else None
 if r['closed']and r['line']is not None and r['odds']is not None and r['odds']>1:
  r['profit']=settle(sum(r['final']),r['line'],r['odds'])
  if r['book_profit']is not None and abs(r['profit']-r['book_profit'])>.001:mismatches.append({'kind':'profit','id':r['id'],'old':r['book_profit'],'new':r['profit']})
 r['day']=r['time'][:10];r['run']=r['sid']or r['time'];r['goals']=sum(r['score']);r['draw']=r['score'][0]==r['score'][1];r['margin']=abs(r['score'][0]-r['score'][1]);r['league_group']=leaguegroup(r['league']);r['rank']=(e.get('final_sports_selection')or{}).get('sports_rank');r['version']=s.get('rules_version',r['version']);r['country']=e.get('country');r['category']=e.get('category');r['raw_live_count']=s.get('raw_live_count');r['need']=None;r['line_gap']=None;r['line_type']=None
 if r['line']is not None:
  q=round(r['line']*4);r['need']=math.floor(r['line']+(.25 if q%2 else 0))+1-r['goals'];r['line_gap']=r['line']-r['goals'];r['line_type']='quarter'if q%2 else 'integer'if q%4==0 else'half'
 st=e.get('board_stats')or{};f=(e.get('final_sports_selection')or{}).get('features')or{};gp=e.get('goal_profile')or{}
 for field in ['on_target','off_target','dangerous_attacks','corners','xg','redcards']:
  v=st.get(field);v=[num(z)for z in v]if isinstance(v,list)and len(v)==2 else None
  # Older narratives are parsed only for explicitly numeric paired counters.
  if not v and field!='xg':
   patterns={'on_target':r'(?:удары в створ|створ)\s*(\d+)\s*[:\-]\s*(\d+)','off_target':r'(?:мимо|удары мимо)\s*(\d+)\s*[:\-]\s*(\d+)','dangerous_attacks':r'опасные атаки\s*(\d+)\s*[:\-]\s*(\d+)','corners':r'угловые\s*(\d+)\s*[:\-]\s*(\d+)','redcards':r'удаления\s*(\d+)\s*[:\-]\s*(\d+)'}
   m=re.search(patterns[field],r['stats_text']or'',re.I);v=[float(m[1]),float(m[2])]if m else None
  r[field]=sum(v)if v and all(z is not None for z in v)else None
  if field=='on_target':r['both_on_target']=all(z>0 for z in v)if v and all(z is not None for z in v)else None
 r['xg_known']=r['xg']is not None if e else None
 for k in ['on_target_5_total','finishing_5_total','dangerous_attacks_5_total','on_target_10_total','supported_xg_5','pressure_trend','two_sided_shots_5_total']:
  r[k]=f.get(k)
 r['gp_known']=bool(gp.get('available'))if e else None;r['gp_ratio']=num(gp.get('ranking_ratio'));r['gp_used']=gp.get('ranking_used');r['gp_min_n']=min([(gp.get(side,{}).get('all')or{}).get('n',0)for side in ['home','away']])if gp else None
 r['league_goals']=num((gp.get('league')or{}).get('mean_total'));r['trend_lag']=((e.get('stats_trend')or{}).get('time_context')or{}).get('board_minus_trend_minutes');r['confidence']=(e.get('selection_confidence')or{}).get('level')
 for field in ['on_target','dangerous_attacks']:
  r[field+'_rate']=r[field]/r['minute']if r[field]is not None and r['minute']and r['minute']>0 else None
# Primary: first FT line per event, earliest snapshot/time then original workbook row order.
rows.sort(key=lambda r:r['time']);primary=[];seen=set()
for r in rows:
 if r['period']!='FT'or r['id']in seen:continue
 seen.add(r['id']);primary.append(r)
meta={'source_commit':'139aa2295747ab6948f426d8ff573c360e780ef7','workbook_rows':len(book),'added_ledger_records':added,'confirmed_report_added':confirmed_added,'all_rows':len(rows),'unique_all':len({r['id']for r in rows}),'primary_ft':len(primary),'ht_rows':sum(r['period']=='HT'for r in rows),'issues':mismatches}
json.dump({'meta':meta,'all_rows':rows,'primary':primary},open(OUT/'rows.json','w'),ensure_ascii=False,indent=2)
print(json.dumps(meta,ensure_ascii=False));print('COVERAGE',collections.Counter(r['day']for r in primary),'closed',sum(r['closed']for r in primary),'priced',sum(r['profit']is not None for r in primary),'snapshots',sum(r['snapshot_match']for r in primary))
