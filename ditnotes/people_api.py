"""Compact unified People review projection from the shared durable registry."""
import json

def review_people(store,catalog):
 store.ensure_unified_people()
 known={a.get('content_id') or a['id'] for a in catalog.get('assets',[])}
 people=store.unified_people();by_id={p['id']:p for p in people};assignment={}
 for person in people:
  person.update(face_samples=[],voice_samples=[],appearances=[])
  for member in person['members']:assignment[member['modality'],member['evidence_id']]=person['id']
 _,roots=store._legacy_roots();appearances={};faces=[];voices=[]
 for r in store.db.execute('SELECT id,content_id,seconds,face_id,crop_ref,frame_ref,bbox FROM face_observations ORDER BY quality DESC,created_at,id'):
  if r['content_id'] not in known:continue
  face=roots['face',r['face_id']];pid=assignment['face',face];p=by_id[pid]
  sample=dict(observation_id=r['id'],content_id=r['content_id'],seconds=r['seconds'],evidence_id=face,crop_ref=r['crop_ref'],frame_ref=r['frame_ref'],bbox=json.loads(r['bbox']))
  if len([x for x in p['face_samples'] if x['evidence_id']==face])<4:p['face_samples'].append(sample)
  a=appearances.setdefault((pid,r['content_id']),{'content_id':r['content_id'],'visible':False,'heard':False,'seconds':r['seconds']})
  a['visible']=True;a['seconds']=min(a['seconds'],r['seconds'])
  faces.append(dict(sample,face_id=face))
 for r in store.db.execute('SELECT id,content_id,start,end,person_id FROM observations ORDER BY quality DESC,end-start DESC,id'):
  if r['content_id'] not in known:continue
  voice=roots['voice',r['person_id']];pid=assignment['voice',voice];p=by_id[pid]
  sample=dict(observation_id=r['id'],content_id=r['content_id'],start=r['start'],end=min(r['end'],r['start']+8),full_end=r['end'],evidence_id=voice)
  if len([x for x in p['voice_samples'] if x['evidence_id']==voice])<3:p['voice_samples'].append(sample)
  a=appearances.setdefault((pid,r['content_id']),{'content_id':r['content_id'],'visible':False,'heard':False,'seconds':r['start']})
  a['heard']=True;a['seconds']=min(a['seconds'],r['start'])
  voices.append(dict(sample,person_id=voice,end=r['end']))
 for (pid,_),a in appearances.items():by_id[pid]['appearances'].append(a)
 # Reuse the existing evidence proposal engine. Partition by clip to avoid
 # rescanning unrelated recordings for each face observation.
 from .av_links import proposals
 # proposals aggregates evidence across clips, and scans its supplied arrays.
 # In typical field projects observations remain small; only compact timing
 # dictionaries enter it, never embeddings or transcript payloads.
 candidates=[]
 rejected={(roots['face',r['face_id']],roots['voice',r['person_id']]) for r in store.db.execute("SELECT face_id,person_id FROM av_link_feedback WHERE decision='rejected'")}
 for candidate in proposals({'face_observations':faces,'observations':voices}):
  face,voice=candidate['face_id'],candidate['person_id'];a=assignment.get(('face',face));b=assignment.get(('voice',voice))
  if not a or not b or a==b or (face,voice) in rejected:continue
  candidates.append(dict(candidate,subject_id=a,candidate_id=b,kind='cooccurrence',review_required=True))
 # Same-modality profile similarity supplies review choices, never automatic
 # cross-modality embedding comparisons or a silent person merge.
 for modality,table,column in [('face','face_profiles','face_id'),('voice','profiles','person_id')]:
  profiles=[dict(r) for r in store.db.execute('SELECT * FROM '+table)];best={}
  for i,a in enumerate(profiles):
   for b in profiles[i+1:]:
    if a['model_revision']!=b['model_revision'] or a['dimension']!=b['dimension']:continue
    pa=assignment.get((modality,roots[modality,a[column]]));pb=assignment.get((modality,roots[modality,b[column]]))
    if not pa or not pb or pa==pb:continue
    av=json.loads(a['centroid']);bv=json.loads(b['centroid']);score=sum(x*y for x,y in zip(av,bv))
    if score<.4:continue
    key=tuple(sorted((pa,pb)));best[key]=max(score,best.get(key,-1))
  for (a,b),score in best.items():candidates.append({'subject_id':a,'candidate_id':b,'kind':modality+'_similarity','cosine':score,'review_required':True,'basis':'Same-model '+modality+' embedding similarity; review required.'})
 history=[{'id':r['id'],'operation':r['operation'],'reason':r['reason'],'created_at':r['created_at'],'undone':bool(r['undone_by'])} for r in store.db.execute('SELECT id,operation,reason,created_at,undone_by FROM unified_person_events ORDER BY created_at DESC LIMIT 30')]
 rejected_people={tuple(sorted((r['person_a'],r['person_b']))) for r in store.db.execute('SELECT * FROM unified_person_rejections')}
 candidates=[c for c in candidates if tuple(sorted((c['subject_id'],c['candidate_id']))) not in rejected_people]
 return {'people':people,'candidates':candidates,'history':history,'edit_revision':store.edit_revision}
