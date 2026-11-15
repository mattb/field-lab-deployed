"""Additive person registry. Modality clusters remain intact as independent evidence.

Migration never deduces identity from equal names or co-occurrence. Legacy links
are imported once. All assignment edits retain before/after history for undo.
"""
import json,time,uuid

class UnifiedPeopleMixin:
 def _init_unified_people(self):
  # Individual statements do not implicitly commit a caller's transaction.
  for statement in (
   'CREATE TABLE IF NOT EXISTS unified_person_rejections(person_a TEXT NOT NULL,person_b TEXT NOT NULL,rejected_at REAL NOT NULL,PRIMARY KEY(person_a,person_b))',
   'CREATE TABLE IF NOT EXISTS unified_person_notes(person_id TEXT PRIMARY KEY,text TEXT NOT NULL,updated_at REAL NOT NULL)',
   'CREATE TABLE IF NOT EXISTS unified_people(id TEXT PRIMARY KEY,name TEXT,created_at REAL NOT NULL,name_conflict INTEGER NOT NULL DEFAULT 0)',
   '''CREATE TABLE IF NOT EXISTS unified_evidence(modality TEXT NOT NULL CHECK(modality IN ('face','voice')),evidence_id TEXT NOT NULL,person_id TEXT NOT NULL,provenance TEXT NOT NULL,assigned_at REAL NOT NULL,PRIMARY KEY(modality,evidence_id),FOREIGN KEY(person_id) REFERENCES unified_people(id))''',
   '''CREATE TABLE IF NOT EXISTS unified_names(modality TEXT NOT NULL,evidence_id TEXT NOT NULL,name TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(modality,evidence_id,name))''',
   '''CREATE TABLE IF NOT EXISTS unified_person_events(id TEXT PRIMARY KEY,operation TEXT NOT NULL,reason TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,created_at REAL NOT NULL,undone_by TEXT)'''):
   self.db.execute(statement)
 def _human_id(self,modality,evidence_id):
  return 'human_'+uuid.uuid5(uuid.NAMESPACE_URL,self.project_id+'\0'+modality+'\0'+evidence_id).hex
 def _legacy_roots(self):
  rows={kind:{r['id']:dict(r) for r in self.db.execute('SELECT * FROM '+table)} for kind,table in [('face','faces'),('voice','people')]}
  roots={}
  for kind,entities in rows.items():
   for key in entities:
    current=key;seen=set()
    while entities[current]['merged_into']:
     if current in seen:raise ValueError('Legacy identity merge cycle')
     seen.add(current);current=entities[current]['merged_into']
    roots[kind,key]=current
  return rows,roots
 def ensure_unified_people(self):
  from .project_store import _json
  with self._transaction():
   first=not self.db.execute("SELECT 1 FROM meta WHERE key='unified_people_version'").fetchone()
   rows,roots=self._legacy_roots();now=time.time();added=0
   for kind,entities in rows.items():
    for key,entity in entities.items():
     if key!=roots[kind,key]:continue
     if self.db.execute('SELECT 1 FROM unified_evidence WHERE modality=? AND evidence_id=?',(kind,key)).fetchone():continue
     person=self._human_id(kind,key)
     self.db.execute('INSERT OR IGNORE INTO unified_people VALUES(?,?,?,0)',(person,entity['name'],entity['created_at']))
     self.db.execute('INSERT INTO unified_evidence VALUES(?,?,?,?,?)',(kind,key,person,'legacy_identity',now));added+=1
   # All historic aliases, including merged groups, remain available for review.
   for kind,entities in rows.items():
    for key,entity in entities.items():
     if entity['name']:
      self.db.execute('INSERT OR IGNORE INTO unified_names VALUES(?,?,?,?)',(kind,key,entity['name'],entity['created_at']))
     root=roots[kind,key]
     if key!=root:
      target=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(kind,root)).fetchone()[0]
      previous=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(kind,key)).fetchone()
      self.db.execute('INSERT OR REPLACE INTO unified_evidence VALUES(?,?,?,?,?)',(kind,key,target,'legacy_merge',now)) if not previous or previous[0]!=target else None
   if first:
    # Historic operator links are assertions, not clustering suggestions. Consume
    # them only here; replay must never undo a later operator detach.
    for link in self.db.execute('SELECT * FROM face_voice_links ORDER BY linked_at,face_id').fetchall():
     face=roots['face',link['face_id']];voice=roots['voice',link['person_id']]
     a=self.db.execute("SELECT person_id FROM unified_evidence WHERE modality='face' AND evidence_id=?",(face,)).fetchone()[0]
     b=self.db.execute("SELECT person_id FROM unified_evidence WHERE modality='voice' AND evidence_id=?",(voice,)).fetchone()[0]
     if a!=b:self.db.execute('UPDATE unified_evidence SET person_id=?,provenance=?,assigned_at=? WHERE person_id=?',(b,'legacy_operator_link',link['linked_at'],a))
    for row in self.db.execute('SELECT id FROM unified_people').fetchall():
     labels=set()
     for m in self.db.execute('SELECT modality,evidence_id FROM unified_evidence WHERE person_id=?',(row[0],)):
      if roots[m['modality'],m['evidence_id']]!=m['evidence_id']:continue
      name=rows[m['modality']][m['evidence_id']]['name']
      if name:labels.add(name)
     self.db.execute('UPDATE unified_people SET name=?,name_conflict=? WHERE id=?',(next(iter(labels)) if len(labels)==1 else None,int(len(labels)>1),row[0]))
    decisions=[('face',r['face_a'],'face',r['face_b'],r['created_at']) for r in self.db.execute("SELECT * FROM face_feedback WHERE decision='different'")]
    decisions += [('face',r['face_id'],'voice',r['person_id'],r['created_at']) for r in self.db.execute("SELECT * FROM av_link_feedback WHERE decision='rejected'")]
    for ka,a,kb,b,when in decisions:
     if (ka,a) not in roots or (kb,b) not in roots:continue
     pa=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(ka,roots[ka,a])).fetchone()[0]
     pb=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(kb,roots[kb,b])).fetchone()[0]
     if pa!=pb:
      pa,pb=sorted((pa,pb));self.db.execute('INSERT OR IGNORE INTO unified_person_rejections VALUES(?,?,?)',(pa,pb,when))
    self.db.execute("INSERT INTO meta VALUES('unified_people_version','1')")
    self._audit('unified_people_migrated',{'version':1,'new_evidence':added,'legacy_data_preserved':True})
   elif added:self._audit('unified_people_enrolled',{'new_evidence':added})
  return {'version':1,'new_evidence':added,'migrated':first}
 def person_for_evidence(self,modality,evidence_id):
  if modality not in ('face','voice'):raise ValueError('Unknown identity modality')
  self.ensure_unified_people()
  evidence_id=self.canonical_face(evidence_id) if modality=='face' else self.canonical_person(evidence_id)
  row=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(modality,evidence_id)).fetchone()
  if not row:raise KeyError(evidence_id)
  return row[0]
 def unified_people(self,ensure=True):
  if ensure:self.ensure_unified_people()
  rows,roots=self._legacy_roots();result=[]
  members={};names={}
  for m in self.db.execute('SELECT * FROM unified_evidence ORDER BY modality,evidence_id'):
   if roots[m['modality'],m['evidence_id']]==m['evidence_id']:members.setdefault(m['person_id'],[]).append(dict(m))
  for alias in self.db.execute('SELECT * FROM unified_names ORDER BY created_at,modality,evidence_id'):
   owner=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(alias['modality'],alias['evidence_id'])).fetchone()
   if owner:names.setdefault(owner[0],[]).append(dict(alias))
  for person in self.db.execute('SELECT * FROM unified_people ORDER BY created_at,id'):
   if person['id'] not in members:continue
   result.append(dict(person,identity_basis='person',name_conflict=bool(person['name_conflict']),members=members[person['id']],aliases=names.get(person['id'],[]),notes=(self.db.execute('SELECT text FROM unified_person_notes WHERE person_id=?',(person['id'],)).fetchone() or [''])[0]))
  return result
 def unified_identity(self,modality,evidence_id):
  """Read effective names without triggering a migration from legacy callers."""
  row=self.db.execute('SELECT p.id,p.name,p.name_conflict FROM unified_evidence e JOIN unified_people p ON p.id=e.person_id WHERE e.modality=? AND e.evidence_id=?',(modality,evidence_id)).fetchone()
  return dict(row) if row else None
 def _unified_snapshot(self):
  return {table:[dict(r) for r in self.db.execute('SELECT * FROM '+table+' ORDER BY '+order)] for table,order in [('unified_people','id'),('unified_evidence','modality,evidence_id'),('unified_person_rejections','person_a,person_b'),('unified_person_notes','person_id')]}
 def _person_event(self,operation,reason,before):
  from .project_store import _id,_json
  event=_id('personedit');after=self._unified_snapshot()
  self.db.execute('INSERT INTO unified_person_events VALUES(?,?,?,?,?,?,NULL)',(event,operation,reason,_json(before),_json(after),time.time()))
  self._audit(operation,{'event_id':event,'reason':reason})
  return {'event_id':event}
 def _check_unified_person(self,person_id):
  if not isinstance(person_id,str) or not self.db.execute('SELECT 1 FROM unified_people WHERE id=?',(person_id,)).fetchone():raise ValueError('Unknown person')
 def _remember_unified_name(self,person_id,name):
  if not name:return
  for m in self.db.execute('SELECT modality,evidence_id FROM unified_evidence WHERE person_id=?',(person_id,)).fetchall():
   self.db.execute('INSERT OR IGNORE INTO unified_names VALUES(?,?,?,?)',(m['modality'],m['evidence_id'],name,time.time()))
 def rename_unified_person(self,person_id,name):
  from .project_store import _text
  name=_text(name,200) if name is not None else None
  self.ensure_unified_people()
  with self._transaction():
   self._check_unified_person(person_id);before=self._unified_snapshot()
   previous=self.db.execute('SELECT name FROM unified_people WHERE id=?',(person_id,)).fetchone()[0]
   self._remember_unified_name(person_id,previous);self._remember_unified_name(person_id,name)
   self.db.execute('UPDATE unified_people SET name=?,name_conflict=0 WHERE id=?',(name,person_id))
   return {'person_id':person_id,**self._person_event('unified_person_named','Operator named person',before)}
 def note_unified_person(self,person_id,text):
  if not isinstance(text,str) or len(text)>20000:raise ValueError('Invalid person notes')
  self.ensure_unified_people()
  with self._transaction():
   self._check_unified_person(person_id);before=self._unified_snapshot()
   self.db.execute('INSERT OR REPLACE INTO unified_person_notes VALUES(?,?,?)',(person_id,text.strip(),time.time()))
   return {'person_id':person_id,**self._person_event('unified_person_noted','Operator updated person notes',before)}
 def reject_person_pair(self,a,b):
  self.ensure_unified_people()
  with self._transaction():
   self._check_unified_person(a);self._check_unified_person(b)
   if a==b:raise ValueError('Choose two different people')
   before=self._unified_snapshot();a,b=sorted((a,b))
   self.db.execute('INSERT OR REPLACE INTO unified_person_rejections VALUES(?,?,?)',(a,b,time.time()))
   return self._person_event('unified_people_kept_separate','Operator reviewed different people',before)
 def merge_unified_people(self,source_id,target_id,reason,chosen_name=None):
  from .project_store import _text
  reason=_text(reason,1000);self.ensure_unified_people()
  with self._transaction():
   self._check_unified_person(source_id);self._check_unified_person(target_id)
   if source_id==target_id:raise ValueError('Choose two different people')
   before=self._unified_snapshot()
   names={r[0] for r in self.db.execute('SELECT name FROM unified_people WHERE id IN (?,?)',(source_id,target_id)) if r[0]}
   for row in self.db.execute('SELECT id,name FROM unified_people WHERE id IN (?,?)',(source_id,target_id)).fetchall():self._remember_unified_name(row['id'],row['name'])
   conflict=len(names)>1
   if chosen_name is not None:chosen_name=_text(chosen_name,200);conflict=False
   elif len(names)==1:chosen_name=next(iter(names))
   for r in self.db.execute('SELECT * FROM unified_person_rejections WHERE person_a=? OR person_b=?',(source_id,source_id)).fetchall():
    self.db.execute('DELETE FROM unified_person_rejections WHERE person_a=? AND person_b=?',(r['person_a'],r['person_b']))
    a,b=sorted(target_id if p==source_id else p for p in (r['person_a'],r['person_b']))
    if a!=b:self.db.execute('INSERT OR IGNORE INTO unified_person_rejections VALUES(?,?,?)',(a,b,r['rejected_at']))
   source_note=self.db.execute('SELECT text FROM unified_person_notes WHERE person_id=?',(source_id,)).fetchone()
   target_note=self.db.execute('SELECT text FROM unified_person_notes WHERE person_id=?',(target_id,)).fetchone()
   if source_note:
    combined='\n\n'.join(x for x in [(target_note or [''])[0],source_note[0]] if x)
    self.db.execute('INSERT OR REPLACE INTO unified_person_notes VALUES(?,?,?)',(target_id,combined,time.time()))
   self.db.execute('UPDATE unified_evidence SET person_id=?,provenance=?,assigned_at=? WHERE person_id=?',(target_id,'operator_reviewed',time.time(),source_id))
   self._remember_unified_name(target_id,chosen_name)
   self.db.execute('UPDATE unified_people SET name=?,name_conflict=? WHERE id=?',(chosen_name,int(conflict),target_id))
   return {'person_id':target_id,**self._person_event('unified_people_combined',reason,before)}
 def detach_person_evidence(self,person_id,modality,evidence_id,reason):
  from .project_store import _id,_text
  reason=_text(reason,1000);self.ensure_unified_people()
  if modality not in ('face','voice'):raise ValueError('Unknown identity modality')
  with self._transaction():
   self._check_unified_person(person_id)
   row=self.db.execute('SELECT person_id FROM unified_evidence WHERE modality=? AND evidence_id=?',(modality,evidence_id)).fetchone()
   if not row or row[0]!=person_id:raise ValueError('Evidence no longer belongs to this person')
   _,roots=self._legacy_roots();root=roots[modality,evidence_id]
   if root!=evidence_id:raise ValueError('Separate the active evidence group')
   before=self._unified_snapshot();new=_id('human')
   self.db.execute('INSERT INTO unified_people VALUES(?,NULL,?,0)',(new,time.time()))
   for kind,key in roots:
    if kind==modality and roots[kind,key]==root:self.db.execute('UPDATE unified_evidence SET person_id=?,provenance=?,assigned_at=? WHERE modality=? AND evidence_id=?',(new,'operator_detached',time.time(),kind,key))
   return {'person_id':new,**self._person_event('unified_evidence_detached',reason,before)}
 def undo_person_edit(self,event_id):
  from .project_store import _json
  self.ensure_unified_people()
  with self._transaction():
   event=self.db.execute('SELECT * FROM unified_person_events WHERE id=?',(event_id,)).fetchone()
   if not event:raise ValueError('Unknown person edit')
   if event['undone_by']:raise ValueError('This edit was already undone')
   current=self._unified_snapshot()
   if _json(current)!=event['after_state']:raise ValueError('People changed after this edit. Review before undoing.')
   before=json.loads(event['before_state'])
   self.db.execute('DELETE FROM unified_evidence')
   # Retain newly created people as empty historical rows, never delete evidence.
   for row in before['unified_people']:self.db.execute('UPDATE unified_people SET name=?,created_at=?,name_conflict=? WHERE id=?',(row['name'],row['created_at'],row['name_conflict'],row['id']))
   for row in before['unified_evidence']:self.db.execute('INSERT INTO unified_evidence VALUES(?,?,?,?,?)',tuple(row[k] for k in ('modality','evidence_id','person_id','provenance','assigned_at')))
   for table in ('unified_person_rejections','unified_person_notes'):
    self.db.execute('DELETE FROM '+table)
    for row in before.get(table,[]):
     columns=list(row);self.db.execute('INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',tuple(row[k] for k in columns))
   result=self._person_event('unified_person_edit_undone','Operator undid '+event_id,current)
   self.db.execute('UPDATE unified_person_events SET undone_by=? WHERE id=?',(result['event_id'],event_id))
   return result
