"""Project annotations on the existing local server and Orama projection."""
from __future__ import annotations
import copy,json,threading
from urllib.parse import urlsplit
from .project_store import ProjectStore,TagConflict
from .search_projection import project_documents

class MetadataAPI:
 def __init__(self,server):
  self.people_migration_lock=threading.Lock();self.people_migration_ready=False
  self.server=server;self.stop=threading.Event();self.changed=threading.Event();self.error=None
  self.thread=threading.Thread(target=self._loop,name='annotation-index',daemon=True);self.thread.start()
  if (server.project/'_DIT_NOTES/project.sqlite3').is_file():self.changed.set()
 def ensure_people_ready(self):
  if self.people_migration_ready:return
  db=self.server.project/'_DIT_NOTES/project.sqlite3'
  if not db.is_file():return
  with self.people_migration_lock:
   if self.people_migration_ready:return
   import sqlite3
   with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
    migrated=conn.execute("SELECT 1 FROM meta WHERE key='unified_people_version'").fetchone()
   if not migrated:
    from deploy.people import migrate
    migrate(self.server.project)
   self.people_migration_ready=True
 def close(self):self.stop.set();self.changed.set();self.thread.join()
 def store(self,catalog=None):
  catalog=self.server.catalog.read() if catalog is None else catalog
  identity=catalog.get('project',{}).get('id')
  if identity=='waiting-for-ingest':raise ValueError('No indexed clips yet')
  return ProjectStore(self.server.project/'_DIT_NOTES/project.sqlite3',project_id=identity)
 def catalog(self):
  # Catalog and vectors remain one sealed publication; edits are immediately
  # visible in metadata state while the shared index catches up atomically.
  return self.server.catalog.read()
 def _known_asset(self,content_id):
  known={a.get('content_id') or a['id'] for a in self.server.catalog.read().get('assets',[])}
  if not isinstance(content_id,str) or content_id not in known:raise ValueError('Clip is not in the project')
 def asset_tags(self,content_id):
  self._known_asset(content_id)
  with self.store() as store:return store.asset_tags(content_id)
 def browse(self):
  self.ensure_people_ready()
  """Cached navigation projection, with no embeddings or voice windows."""
  catalog=self.server.catalog.read();assets=catalog.get('assets',[])
  known={a.get('content_id') or a['id'] for a in assets}
  signature=tuple((a['id'],a.get('content_id'),a.get('relative_path')) for a in assets)
  if not (self.server.project/'_DIT_NOTES/project.sqlite3').is_file():return {'tags':[],'faces':[],'asset_times':{}}
  with self.store(catalog) as store:
   store.ensure_unified_people()
   store.db.execute('BEGIN')
   try:
    revision=store.edit_revision
    cached=getattr(self,'_browse_cache',None)
    if cached and cached[0]==(signature,revision):return cached[1]
    if getattr(self,'_browse_time_signature',None)!=signature:
     self._browse_times={}
     for a in assets:
      try:self._browse_times[a['id']]=(self.server.project/a['relative_path']).stat().st_mtime
      except (KeyError,OSError):pass
     self._browse_time_signature=signature
    result={'tags':[],'faces':[],'asset_times':self._browse_times,'revision':revision}
    times={r['id']:r['created_at'] for r in store.db.execute('SELECT id,created_at FROM tag_vocabulary')}
    assigned={}
    for row in store.asset_tag_assignments():
     if row['content_id'] in known:assigned.setdefault(row['id'],set()).add(row['content_id'])
    result['tags']=[dict(t,created_at=times.get(t['id'],0),contents=sorted(assigned.get(t['id'],set()))) for t in store.tag_vocabulary()]
    faces={r['id']:dict(r) for r in store.db.execute('SELECT id,name,merged_into,created_at FROM faces')}
    canonical={}
    for face in faces:
     current=face;seen=set()
     while faces.get(current,{}).get('merged_into') and current not in seen:
      seen.add(current);current=faces[current]['merged_into']
     canonical[face]=current
    observed={}
    for row in store.db.execute('SELECT face_id,content_id,seconds,crop_ref,frame_ref FROM face_observations ORDER BY created_at,id'):
     if row['content_id'] not in known:continue
     face=canonical[row['face_id']];group=observed.setdefault(face,{'contents':set(),'sample':dict(row)})
     group['contents'].add(row['content_id'])
    for face in faces.values():
     if face['merged_into']:continue
     group=observed.get(face['id'],{'contents':set(),'sample':None})
     result['faces'].append(dict(id=face['id'],name=face['name'],created_at=face['created_at'],contents=sorted(group['contents']),sample=group['sample']))
    subjects=store.unified_people(ensure=False);face_subject={m['evidence_id']:p['id'] for p in subjects for m in p['members'] if m['modality']=='face'};voice_subject={m['evidence_id']:p['id'] for p in subjects for m in p['members'] if m['modality']=='voice'}
    present={p['id']:{'visible':set(),'heard':set(),'sample':None} for p in subjects}
    for f in result['faces']:
     pid=face_subject.get(f['id'])
     if pid:present[pid]['visible'].update(f['contents']);present[pid]['sample']=present[pid]['sample'] or f['sample']
    voice_roots={r['id']:r['merged_into'] for r in store.db.execute('SELECT id,merged_into FROM people')}
    for r in store.db.execute('SELECT DISTINCT person_id,content_id FROM observations'):
     current=r['person_id']
     while voice_roots.get(current):current=voice_roots[current]
     pid=voice_subject.get(current)
     if pid and r['content_id'] in known:present[pid]['heard'].add(r['content_id'])
    result['people']=[dict(id=p['id'],name=p['name'],name_conflict=p['name_conflict'],created_at=p['created_at'],contents=sorted(present[p['id']]['visible']|present[p['id']]['heard']),visible_contents=sorted(present[p['id']]['visible']),heard_contents=sorted(present[p['id']]['heard']),sample=present[p['id']]['sample']) for p in subjects]
    self._browse_cache=((signature,revision),result)
    return result
   finally:store.db.rollback()
 def people_review(self):
  self.ensure_people_ready()
  from .people_api import review_people
  catalog=self.server.catalog.read()
  with self.store(catalog) as store:
   store.ensure_unified_people()
   revision=store.edit_revision;signature=(revision,tuple((a['id'],a.get('content_id')) for a in catalog.get('assets',[])))
   cached=getattr(self,'_people_review_cache',None)
   if cached and cached[0]==signature:return cached[1]
   with store._transaction():result=review_people(store,catalog)
   self._people_review_cache=(signature,result)
   self.changed.set()
   return result
 def state(self):
  if not (self.server.project/'_DIT_NOTES/project.sqlite3').is_file() and not self.server.catalog.read().get('assets'):
   return {'people':[],'observations':[],'faces':[],'face_observations':[],'annotations':[],'links':[],'asset_tags':[],'tag_vocabulary':[],'edit_revision':0,'indexed_edit_revision':0,'index_error':None}
  with self.store() as store:
   # A controller publication may resolve our revision gap after a retry failed.
   if store.edit_revision<=store.indexed_edit_revision:self.error=None
   return {'people':store.people(),'observations':[{k:v for k,v in o.items() if k!='embedding'} for o in store.observations()],'faces':store.faces(),'face_observations':[{k:v for k,v in o.items() if k!='embedding'} for o in store.face_observations()],'annotations':store.annotations(),'links':store.links(status='suggested'),'asset_tags':store.asset_tag_assignments(),'tag_vocabulary':store.tag_vocabulary(),'edit_revision':store.edit_revision,'indexed_edit_revision':store.indexed_edit_revision,'index_error':self.error}
 def face_review(self):
  import numpy as np
  from .features import features
  state=self.state();suggestions={}
  with self.store() as s:
   active={f['id'] for f in state['faces'] if not f['merged_into']}
   rows=[dict(r) for r in s.db.execute('SELECT * FROM face_profiles') if r['face_id'] in active]
   for a in rows:
    scores=[];av=np.array(json.loads(a['centroid']))
    for b in rows:
     if a['face_id']==b['face_id'] or a['model_revision']!=b['model_revision'] or a['dimension']!=b['dimension']:continue
     if s.faces_marked_different(a['face_id'],b['face_id']):continue
     scores.append({'face_id':b['face_id'],'cosine':float(av@np.array(json.loads(b['centroid'])))})
    suggestions[a['face_id']]=sorted(scores,key=lambda r:-r['cosine'])[:6]
   feedback=s.face_feedback()
   rejected={(s.canonical_face(r['face_id']),s.canonical_person(r['person_id'])) for r in s.db.execute("SELECT * FROM av_link_feedback WHERE decision='rejected'")}
  from .av_links import proposals
  av=[r for r in proposals(state) if (r['face_id'],r['person_id']) not in rejected]
  return {**state,'similar_faces':suggestions,'matching_feedback':feedback,'voice_candidates':av,'features':features()}
 def mutate(self,path,v):
  if path in ('/api/people/name','/api/people/note','/api/people/combine','/api/people/detach','/api/people/undo','/api/people/reject'):
   self.ensure_people_ready()
   with self.store() as s:
    s.ensure_unified_people()
    with s._transaction():
     if type(v.get('base_revision')) is not int or v['base_revision']!=s.edit_revision:raise TagConflict({'revision':s.edit_revision,'entity':'people'})
     if path=='/api/people/name':result=s.rename_unified_person(v['person_id'],v.get('name') or None)
     elif path=='/api/people/note':result=s.note_unified_person(v['person_id'],v.get('text',''))
     elif path=='/api/people/combine':result=s.merge_unified_people(v['source_id'],v['target_id'],v.get('reason') or 'Operator reviewed same person',chosen_name=v.get('chosen_name'))
     elif path=='/api/people/detach':result=s.detach_person_evidence(v['person_id'],v['modality'],v['evidence_id'],v.get('reason') or 'Operator separated incorrect match')
     elif path=='/api/people/undo':result=s.undo_person_edit(v['event_id'])
     else:result=s.reject_person_pair(v['person_a'],v['person_b'])
   self.changed.set();return result
  if path=='/api/asset-tags':self._known_asset(v.get('content_id'))
  with self.store() as s:
   if path=='/api/asset-tags':result=s.set_asset_tags(v['content_id'],v.get('tags'),v.get('base_revision'))
   elif path=='/api/name':s.name_person(v['person_id'],v.get('name') or None);result={'saved':True}
   elif path=='/api/face/name':s.name_face(v['face_id'],v.get('name') or None);result={'saved':True}
   elif path=='/api/face/link':s.link_face_voice(v['face_id'],v['person_id'],v.get('reason') or 'Operator verified face and voice');result={'saved':True}
   elif path=='/api/face/unlink':s.unlink_face_voice(v['face_id'],v.get('reason') or 'Operator removed face and voice link');result={'saved':True}
   elif path=='/api/people/merge':result={'person_id':s.merge_people(v['source_id'],v['target_id'],v.get('reason') or 'Operator confirmed same voice',chosen_name=v.get('chosen_name'))}
   elif path=='/api/face/merge':result={'face_id':s.merge_faces(v['source_id'],v['target_id'],v.get('reason') or 'Operator confirmed same face',chosen_name=v.get('chosen_name'))}
   elif path=='/api/face/review':
    if v['decision']=='same':result={'face_id':s.merge_faces(v['face_a'],v['face_b'],'Operator confirmed same person in face review',chosen_name=v.get('chosen_name'),feedback=True)}
    else:result={'feedback_id':s.review_face_matching(v['decision'],v.get('face_a'),v.get('face_b'),v.get('comment',''))}
   elif path=='/api/face/reject-voice':s.reject_voice_candidate(v['face_id'],v['person_id']);result={'saved':True}
   elif path=='/api/people/split':result={'person_id':s.split_person(v['person_id'],v['observation_ids'],v['reason'],new_name=v.get('new_name'))}
   elif path=='/api/face/split':result={'face_id':s.split_face(v['face_id'],v['observation_ids'],v['reason'],new_name=v.get('new_name'))}
   elif path=='/api/annotation':
    known={a.get('content_id') or a['id'] for a in self.server.catalog.read().get('assets',[])}
    if v['content_id'] not in known:raise ValueError('Clip is not in the project')
    result={'annotation_id':s.add_annotation(v['content_id'],v['text'],start=v.get('start'),end=v.get('end'),metadata=v.get('metadata'))}
   elif path=='/api/annotation/update':s.update_annotation(v['annotation_id'],v['text'],metadata=v.get('metadata'));result={'saved':True}
   elif path=='/api/annotation/remove-inherited':s.remove_inherited_annotation(v['annotation_id']);result={'saved':True}
   elif path=='/api/link/accept':result={'annotation_id':s.accept_link(v['link_id'])}
   elif path=='/api/link/reject':s.reject_link(v['link_id']);result={'saved':True}
   else:raise KeyError('Unknown metadata action')
  self.changed.set();return result
 def _loop(self):
  while not self.stop.is_set():
   self.changed.wait(2)
   if self.stop.is_set():break
   self.changed.clear()
   try:
    if not (self.server.project/'_DIT_NOTES/project.sqlite3').is_file():continue
    with self.store() as store:pending=store.edit_revision>store.indexed_edit_revision
    if not pending:
     self.error=None
     continue
    if self.stop.wait(.3):break
    from .enrichment import publish_index
    publish_index(self.server.project,self.server.search_backend,projection_only=True)
    self.error=None
   except Exception as e:
    # The ingest controller owns this same project lock while publishing.
    # Normal contention is retryable updating work, not a failed index.
    self.error=None if isinstance(e,RuntimeError) and str(e)=='Another process is updating this project' else str(e)
    # Revision gap is durable; retry periodically without dropping edits.
    if self.stop.wait(5):break

ACTIONS={'/api/people/note','/api/people/reject','/api/people/name','/api/people/combine','/api/people/detach','/api/people/undo','/api/asset-tags','/api/face/reject-voice','/api/face/review','/api/people/merge','/api/face/merge','/api/people/split','/api/face/split','/api/name','/api/face/name','/api/face/link','/api/face/unlink','/api/annotation','/api/annotation/update','/api/annotation/remove-inherited','/api/link/accept','/api/link/reject'}
def handle_post(handler):
 path=urlsplit(handler.path).path
 if path not in ACTIONS:return False
 host=handler.headers.get('Host','');origin=handler.headers.get('Origin');port=handler.server.server_port
 if host not in (f'127.0.0.1:{port}',f'localhost:{port}') or origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
  handler.send_error(403);return True
 if handler.headers.get('Content-Type','').split(';')[0]!='application/json':handler.send_error(415);return True
 try:
  length=int(handler.headers.get('Content-Length','0'))
  if not 0<length<=1024*1024:raise ValueError('Invalid request size')
  values=json.loads(handler.rfile.read(length))
  if not isinstance(values,dict):raise ValueError('Expected a JSON object')
  handler._json(handler.server.metadata.mutate(path,values),False)
 except TagConflict as e:handler._json({'error':'People changed in another session. Your entry is kept; refresh and review before saving.' if e.current.get('entity')=='people' else str(e),'current':e.current},False,409)
 except Exception as e:handler._json({'error':str(e)},False,400)
 return True
