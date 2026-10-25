import json,gzip,collections,datetime as dt,math,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
rows=json.load(gzip.open(ROOT/'points.json.gz'));boards=json.load(gzip.open(ROOT/'boards.json.gz'))
hist=collections.defaultdict(list)
for b in boards:hist[b['event_id']].append(b)
for bs in hist.values():bs.sort(key=lambda b:(b['ts'],b.get('available_ts',b['ts'])))
def total(p):return sum(p) if p is not None else None
quality=collections.Counter()
for r in rows:
    bs=[b for b in hist[r['event_id']] if b['ts']<=r['board_ts'] and b.get('available_ts',b['ts'])<=r['ts']+1]
    changes=[];reds=[]
    for a,b in zip(bs,bs[1:]):
        gap=(b['ts']-a['ts'])/60
        if not 0<gap<=12 or not 0<=b['minute']-a['minute']<=12:continue
        scdiff=[b['score'][i]-a['score'][i] for i in (0,1)]
        if all(x>=0 for x in scdiff) and sum(scdiff)>0:changes.append((a,b,scdiff))
        if a['red'] is not None and b['red'] is not None:
            diff=[b['red'][i]-a['red'][i] for i in (0,1)]
            if all(x>=0 for x in diff) and sum(diff)>0:reds.append((a,b,diff))
    recent=changes[-1] if changes else None
    r['observed_goal_age']=None;r['goal_reaction']=None;r['recent_equalizer']=False;r['two_goals_observed_recent']=False
    if recent and recent[1]['score']==r['score']:
        a,b,diff=recent;age=(r['ts']-b.get('available_ts',b['ts']))/60;r['observed_goal_age']=age
        if age<=10:
            if diff==[1,0]:scorer=0
            elif diff==[0,1]:scorer=1
            else:scorer=None
            if scorer is not None:
                oldmargin=a['score'][scorer]-a['score'][1-scorer]
                r['goal_reaction']='leader_scored' if oldmargin>0 else 'trailer_scored' if oldmargin<0 else 'draw_broken'
            r['recent_equalizer']=b['score'][0]==b['score'][1]
            r['two_goals_observed_recent']=sum(diff)>=2 and b['ts']-a['ts']<=7*60
    r['observed_red_age']=None
    if reds:
        a,b,diff=reds[-1];r['observed_red_age']=(r['ts']-b.get('available_ts',b['ts']))/60
    early=next(((a,b) for a,b,d in changes if sum(a['score'])==0 and b['minute']<=20),None)
    late=next(((a,b) for a,b,d in changes if sum(a['score'])==0 and a['minute']>=30),None)
    r['first_goal_seen_early']=bool(early);r['first_goal_seen_late']=bool(late)
    r['known_drought15']=any(b['score']==r['score'] and r['board_ts']-b['ts']>=15*60 and r['minute']-b['minute']>=15 for b in bs)
    currentmargin=r['score'][0]-r['score'][1]
    r['observed_lead_reversed']=currentmargin!=0 and any((b['score'][0]-b['score'][1])*currentmargin<0 for b in bs)
    pre=r.get('prematch_1x2');r['favorite_side']=None;r['favorite_probability']=None;r['favorite_state']=None
    if pre:
        side=0 if pre[0]>=pre[2] else 1;p=pre[0] if side==0 else pre[2]
        if p>=.50 and abs(pre[0]-pre[2])>=.10:
            r['favorite_side']=side;r['favorite_probability']=p
            margin=r['score'][side]-r['score'][1-side]
            r['favorite_state']='leading' if margin>0 else 'trailing' if margin<0 else 'drawing'
    pretotal=r.get('prematch_total');r['prematch_total_line']=pretotal[0] if pretotal else None;r['total_shift']=r['line']-pretotal[0] if pretotal else None
    title=str(r.get('league') or '').casefold();cat=str(r.get('category') or '')
    r['competition_kind']='friendly' if re.search(r'friendly|товарищ',title) else 'cup_label' if re.search(r'\bcup\b|кубок|copa|pokal|coupe',title) else 'other_competition'
    for k in ('observed_goal_age','observed_red_age','prematch_total_line','favorite_state'):
        if r[k] is not None:quality[k]+=1
    for k in ('first_goal_seen_early','first_goal_seen_late','known_drought15','observed_lead_reversed','two_goals_observed_recent'):
        quality[k]+=bool(r[k])
with gzip.open(ROOT/'derived.json.gz','wt') as f:json.dump(rows,f,ensure_ascii=False,separators=(',',':'))
(ROOT/'derived_coverage.json').write_text(json.dumps(dict(quality),indent=2));print('DERIVED',len(rows),dict(quality),flush=True)
