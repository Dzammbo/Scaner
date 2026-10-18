"""Frozen experimental admission/ranking rules; not production Live."""
from live_ranking import fresh_sports_features, partial_pair, _windows
from live_enrichment import number

def checks(e):
 f=fresh_sports_features(e);p=e.get('goal_profile') or {};w=_windows(e);t=e.get('stats_trend')or{};st=e.get('board_stats')or{}
 on5=f['on_target_5_home_away']; on10=f['on_target_10_home_away']
 aligned=f['previous_interval']['actual_duration_minutes'] is not None
 prev=[b-a if aligned and a is not None and b is not None and b>=a else None for a,b in zip(on5,on10)]
 repeated=any(a is not None and a>0 and b is not None and b>0 for a,b in zip(on5,prev))
 quality=(f.get('on_target_5_known_total')or 0)>0 or (f.get('supported_xg_5')or 0)>0
 strict=repeated or (f.get('supported_xg_5')or 0)>=.15
 cutoff=number(e.get('start_time'));history=True;counts={}
 for side in ['home','away','league']:
  rows=p.get(side,{}).get('matches')
  if not isinstance(rows,list) or not cutoff:history=False;counts[side]=None;continue
  recent=[x for x in rows if cutoff-180*86400<=x.get('time',0)<cutoff and x.get('league_id')==e.get('league_id')]
  counts[side]=len({x.get('event_id')for x in recent})
  if counts[side]<(10 if side=='league' else 5):history=False
 conflicts=[];ctx=t.get('time_context')or{}
 # Compare contemporaneous board and series only when the game clocks coincide.
 # A lagging series cannot establish a contradiction with a later board count.
 if ctx.get('board_minus_trend_minutes')==0:
  for field in ['on_target','off_target','goals','redcards']:
   for side,board in zip(['home','away'],partial_pair(st.get(field))):
    series=(t.get('series')or{}).get(field,{}).get(side,[])
    if not series or board is None:continue
    last=series[-1];v=number(last.get('value'))
    if v is not None and v!=board:conflicts.append(field+':'+side)
 previous_pressure=f['previous_interval']['dangerous_attacks_per_minute'];cur=f['dangerous_attacks_5_known_per_minute']
 prev_on=sum(v or 0 for v in prev);current_on=sum(v or 0 for v in on5)
 fading=(previous_pressure is not None and cur is not None and previous_pressure>0
         and cur<previous_pressure*.5 and current_on<prev_on)
 # Sports before history. xG presence itself never gives a blanket rank bonus.
 key=(not repeated,-current_on,-(f.get('finishing_5_known_total')or 0),
      -(f.get('on_target_10_known_total')or 0),f['pressure_trend']=='fading',
      -(p.get('ranking_ratio')or 1),str(e['event_id']))
 return {'quality':quality,'strict':strict,'history':history,'history_n':counts,'conflicts':conflicts,'fading':fading,'key':key,'on5':current_on,'shots5':f.get('finishing_5_known_total')}
