"""Offline selection lab. No API, production imports are read-only pure functions."""
import math,json,sys,copy,datetime,collections,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from live_ranking import rank_final_candidates
from live_enrichment import full_win_total

def number(x):
 try:
  x=float(x);return x if math.isfinite(x) else None
 except (ValueError,TypeError):return None

def settle(total,line,price):
 if line is None or price is None or price<=1 or abs(line*4-round(line*4))>1e-8:raise ValueError('Invalid market')
 legs=[line-.25,line+.25] if round(line*4)%2 else [line]
 return sum(price-1 if total>v else 0 if total==v else -1 for v in legs)/len(legs)

def display_line(line):
 return line-.25 if round(line*4)%2 else line

def parse_time(x):
 if isinstance(x,(int,float)):return float(x)
 try:return datetime.datetime.fromisoformat(str(x).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return None

def market_for_event(e,snapshot):
 reference=e.get('events_main_total')or{}
 target=number(reference.get('line'))
 if target is None:return None,'reference_market_missing'
 target=display_line(target)
 candidates=[reference]+list(e.get('totals')or[])+list(e.get('eligible_full_time_totals')or[])
 reasons=[]
 for q in candidates:
  if number(q.get('line'))!=target:continue
  k=number(q.get('over'))
  if k is None or k<=1:reasons.append('price_missing');continue
  if not q.get('source'):reasons.append('source_missing');continue
  # totals is the full-time collection. Reject explicitly labelled first-half markets too.
  if 'half' in str(q.get('market','')).lower() or 'тайм' in str(q.get('market','')).lower():reasons.append('wrong_period');continue
  if str(q.get('quote_score','')).replace(':','-')!=str(e.get('score','')).replace(':','-'):reasons.append('quote_score_unknown_or_conflicting');continue
  checked=parse_time(q.get('market_checked_at'))
  end=parse_time(snapshot.get('odds_checked_at_utc')or snapshot.get('generated_at_utc'))
  if checked is None or end is None:reasons.append('quote_time_missing');continue
  if checked>end+1:reasons.append('quote_from_future');continue
  board=parse_time(snapshot.get('board_observed_at_utc'))
  if board is not None and checked<board-120:reasons.append('quote_not_contemporaneous');continue
  return dict(line=target,price=k,source=q['source'],checked_at=checked,score=q['quote_score']),None
 return None,','.join(sorted(set(reasons))) or 'exact_lowered_line_price_missing'

def sports_order(events):
 # Repeated batches preserve the production function's exact sporting order without changing its cap.
 remaining=copy.deepcopy(events);result=[]
 while remaining:
  batch=rank_final_candidates(remaining,limit=5)
  if not batch:break
  ids={e['event_id'] for e in batch};result.extend(batch)
  remaining=[e for e in remaining if e['event_id'] not in ids]
 return result

def features(r):
 rate=number(r.get('on_target_rate'))
 return [1.,min(6,sum(r['score']))/3.,float(r['score'][0]==r['score'][1]),min(3.,rate/.2) if rate is not None else 0.,float(rate is None)]

def exposure(r):return max(1.,90.-r['minute'])/45.

def solve(a,b):
 a=[list(row)+[v] for row,v in zip(a,b)];n=len(b)
 for i in range(n):
  pivot=max(range(i,n),key=lambda j:abs(a[j][i]));a[i],a[pivot]=a[pivot],a[i]
  if abs(a[i][i])<1e-12:raise ValueError('Singular fit')
  d=a[i][i];a[i]=[v/d for v in a[i]]
  for j in range(n):
   if j!=i:
    q=a[j][i];a[j]=[u-q*v for u,v in zip(a[j],a[i])]
 return [row[-1] for row in a]

def fit(rows,ridge=10,iterations=40):
 beta=[math.log(max(.01,sum(r['additional'] for r in rows)/sum(exposure(r) for r in rows))),0.,0.,0.,0.]
 for _ in range(iterations):
  h=[[0.]*5 for _ in range(5)];g=[0.]*5
  for r in rows:
   x=features(r);mu=math.exp(max(-12,min(8,sum(a*b for a,b in zip(x,beta))+math.log(exposure(r)))))
   for i in range(5):
    g[i]+=x[i]*(r['additional']-mu)
    for j in range(5):h[i][j]+=mu*x[i]*x[j]
  for i in range(1,5):h[i][i]+=ridge;g[i]-=ridge*beta[i]
  h[0][0]+=1e-8
  delta=solve(h,g);scale=max(1.,max(abs(d) for d in delta)/1.)
  beta=[b+d/scale for b,d in zip(beta,delta)]
  if max(abs(d) for d in delta)<1e-7:break
 return beta

def predict(r,beta):return math.exp(max(-12,min(8,sum(a*b for a,b in zip(features(r),beta))+math.log(exposure(r)))))

def expected_profit(lam,initial,line,price):
 # All sufficiently large counts have identical full-win payoff; assign the exact residual tail there.
 p=math.exp(-lam);s=0.;ev=0.
 cutoff=max(30,int(lam+12*math.sqrt(lam+1)))
 for n in range(cutoff+1):
  s+=p;ev+=p*settle(initial+n,line,price);p*=lam/(n+1)
 return ev+max(0.,1.-s)*(price-1.)

def p_two(lam):return 1-math.exp(-lam)*(1+lam)

def summarize(rows):
 closed=[r for r in rows if r.get('additional') is not None];priced=[r for r in closed if r.get('profit') is not None]
 profit=sum(r['profit'] for r in priced)
 peak=bal=dd=0.
 for r in priced:
  bal+=r['profit'];peak=max(peak,bal);dd=max(dd,peak-bal)
 return dict(selected=len(rows),closed=len(closed),priced=len(priced),profit=profit,roi=profit/len(priced) if priced else None,
  one_plus=sum(r['additional']>=1 for r in closed),two_plus=sum(r['additional']>=2 for r in closed),
  losses=sum(r['profit']<0 for r in priced),full_losses=sum(r['profit']==-1 for r in priced),positive=sum(r['profit']>0 for r in priced),
  max_drawdown=dd,days={d:summarize_day([r for r in priced if r['day']==d]) for d in sorted({r['day'] for r in priced})})

def summarize_day(rows):
 return dict(n=len(rows),profit=sum(r['profit'] for r in rows),roi=sum(r['profit'] for r in rows)/len(rows) if rows else None)
