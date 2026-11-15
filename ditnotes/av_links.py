"""Face/voice co-occurrence proposals, never automatic identity claims."""
from collections import defaultdict
def proposals(state):
    pairs=defaultdict(list);face_totals=defaultdict(int);voice_totals=defaultdict(int);last={}
    faces=state['face_observations'];voices=state['observations']
    faces_by_clip=defaultdict(list);voices_by_clip=defaultdict(list)
    for f in faces:faces_by_clip[f['content_id']].append(f)
    for v in voices:voices_by_clip[v['content_id']].append(v)
    for f in sorted(faces,key=lambda o:(o['content_id'],o['seconds'])):
        c=f['content_id'];t=f['seconds']
        visible={o['face_id'] for o in faces_by_clip[c] if abs(o['seconds']-t)<.02}
        active=[o for o in voices_by_clip[c] if o['start']<t+.25 and o['end']>t-.25]
        ids={o['person_id'] for o in active}
        if len(visible)!=1 or len(ids)!=1:continue
        voice=next(iter(ids));key=(f['face_id'],voice,c)
        if t-last.get(key,-100)<10:continue
        if not any(o['start']<=t-.25 and o['end']>=t+.25 for o in active):continue
        last[key]=t;face_totals[f['face_id']]+=1;voice_totals[voice]+=1
        pairs[(f['face_id'],voice)].append({'content_id':c,'seconds':t,'start':max(0,t-2),'end':t+3,'face_observation_id':f['observation_id'],'voice_observation_ids':[o['observation_id'] for o in active]})
    result=[]
    for (face,voice),windows in pairs.items():
        clips=len({w['content_id'] for w in windows});fs=len(windows)/face_totals[face];vs=len(windows)/voice_totals[voice]
        runner=max([len(v) for (f,p),v in pairs.items() if f==face and p!=voice] or [0])
        result.append({'face_id':face,'person_id':voice,'windows':windows[:5],'window_count':len(windows),'clip_count':clips,'face_consistency':fs,'voice_consistency':vs,
                       'strong_suggestion':len(windows)>=5 and clips>=3 and fs>=.8 and vs>=.8 and len(windows)-runner>=2,
                       'basis':'Repeated temporal co-occurrence, not verified lip sync or proof of identity.'})
    return sorted(result,key=lambda r:(-r['window_count'],-r['clip_count']))
