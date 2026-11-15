"""Explicit-prior interview reconciliation; no co-occurrence-only identity claims.

Reuse sealed local voice embeddings and project-global People. A recurring voice
is cross-validated against OTHER recordings, never itself. An operator-supplied
sole-on-camera prior may identify the other participant, but is not lip-sync.
"""
from __future__ import annotations
import hashlib,json,math,os,sqlite3,time,uuid
from pathlib import Path
from .atomic import atomic_json,fsync_dir
from .project_store import ProjectStore

def unit(vector):
 if not isinstance(vector,list) or not vector or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector):raise ValueError('Invalid voice embedding')
 n=math.sqrt(sum(x*x for x in vector))
 if n<1e-8:raise ValueError('Empty voice embedding')
 return [x/n for x in vector]
def cosine(a,b):return sum(x*y for x,y in zip(a,b))
def mean(rows):return unit([sum(values)/len(rows) for values in zip(*rows)])
def common_voice(rows,threshold=.72,margin=.25,min_recordings=3):
 if type(min_recordings) is not int or min_recordings<3 or not math.isfinite(threshold) or not math.isfinite(margin) or not -1<=threshold<=1 or not 0<=margin<=2:raise ValueError('Invalid cross-validation policy')
 rows=[dict(r,embedding=unit(r['embedding'])) for r in rows]
 if len({(r['content_id'],r['local_speaker']) for r in rows})!=len(rows):raise ValueError('Duplicate local speaker enrollment')
 if len({(r['model_revision'],len(r['embedding'])) for r in rows})>1:raise ValueError('Incompatible embedding revisions or dimensions')
 by_clip={}
 for r in rows:by_clip.setdefault(r['content_id'],[]).append(r)
 if len(by_clip)<min_recordings:return {'matches':{},'rejected':list(by_clip),'basis':'Insufficient independent recordings'}
 def support(a):
  return sum(max(cosine(a['embedding'],b['embedding']) for b in group)>=threshold for cid,group in by_clip.items() if cid!=a['content_id'])
 anchor=max(rows,key=lambda a:(support(a),a['content_id'],a['local_speaker']))['embedding']
 selected={}
 for _ in range(5):
  selected={}
  for cid,group in by_clip.items():
   ranked=sorted(((cosine(anchor,r['embedding']),r) for r in group),key=lambda x:x[0],reverse=True)
   gap=ranked[0][0]-(ranked[1][0] if len(ranked)>1 else 0)
   if ranked[0][0]>=threshold and gap>=margin:selected[cid]=ranked[0][1]
  if len(selected)<min_recordings:return {'matches':{},'rejected':list(by_clip),'basis':'No separated recurring voice'}
  anchor=mean([r['embedding'] for r in selected.values()])
 matches={}
 for cid,row in selected.items():
  others=[r['embedding'] for other,r in selected.items() if other!=cid]
  if len(others)<min_recordings-1:continue
  reference=mean(others);score=cosine(reference,row['embedding'])
  rival=max([cosine(reference,r['embedding']) for r in by_clip[cid] if r['local_speaker']!=row['local_speaker']] or [0])
  if score>=threshold and score-rival>=margin:matches[cid]={'local_speaker':row['local_speaker'],'cosine':score,'margin':score-rival,'independent_recordings':len(others)}
 if len(matches)<min_recordings:return {'matches':{},'rejected':sorted(by_clip),'basis':'Insufficient cross-validated independent recordings'}
 return {'matches':matches,'rejected':sorted(set(by_clip)-set(matches)),'threshold':threshold,'minimum_margin':margin,'basis':'Sealed voice embeddings; leave-one-recording-out validation, not speaker-number inference'}

def apply_plan(project,plan,*,checkpoint=None):
 """One additive transaction, stable run identity, backup before mutation.

 All assignments and the applied receipt commit together; rerunning after a lost
 acknowledgement returns the original receipt instead of duplicating merges.
 """
 project=Path(project).resolve(strict=True);notes=project/'_DIT_NOTES';db=notes/'project.sqlite3';checkpoint=checkpoint or (lambda phase:None)
 if db.is_symlink() or notes.is_symlink() or not db.is_file():raise ValueError('Unsafe or missing project registry')
 if type(plan.get('base_revision')) is not int or not isinstance(plan.get('links'),list):raise ValueError('Invalid validation plan')
 job=hashlib.sha256(json.dumps(plan,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()
 folder=notes/'av-validation';folder.mkdir(exist_ok=True)
 if folder.is_symlink():raise ValueError('Unsafe validation folder')
 receipt=folder/(job+'.receipt.json')
 with ProjectStore(db) as store:
  prior=next((json.loads(r[0]) for r in store.db.execute("SELECT payload FROM audit WHERE operation='av_validation_applied'") if json.loads(r[0]).get('job_id')==job),None)
  if prior:atomic_json(receipt,prior);return prior
 backup=folder/(job+'-'+uuid.uuid4().hex+'.sqlite3');pending=backup.with_suffix('.pending')
 with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(pending) as target:
  source.backup(target)
  if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Validation backup failed')
 with pending.open('rb') as f:os.fsync(f.fileno())
 os.replace(pending,backup);fsync_dir(folder);checkpoint('backup_ready')
 with ProjectStore(db) as store:
  with store._transaction():
   prior=next((json.loads(r[0]) for r in store.db.execute("SELECT payload FROM audit WHERE operation='av_validation_applied'") if json.loads(r[0]).get('job_id')==job),None)
   if prior:atomic_json(receipt,prior);return prior
   if store.project_id!=plan['project_id'] or store.edit_revision!=plan['base_revision']:raise ValueError('Project changed; review a fresh plan')
   # Review decisions are bound to exact input files and their sealed analyses.
   for source in plan.get('sources',[]):
    path=notes/source['path']
    if path.is_symlink() or not path.resolve().is_relative_to(notes.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('Validation source changed')
   store.ensure_unified_people();applied=[];named_voices=[]
   for link in plan['links']:
    target=store.person_for_evidence('face',link['face_id']);source=store.person_for_evidence('voice',link['voice_id'])
    target_row=next(p for p in store.unified_people() if p['id']==target)
    source_row=next(p for p in store.unified_people() if p['id']==source)
    if source_row['name_conflict'] or target_row['name_conflict']:raise ValueError('Resolve existing name conflicts before applying an association')
    if source_row['name'] and source_row['name']!=link['name']:raise ValueError('Existing voice name conflicts with the reviewed plan')
    if target_row['name']!=link['name']:raise ValueError('Face name changed')
    if source!=target:
     rejected=tuple(sorted((source,target)))
     if store.db.execute('SELECT 1 FROM unified_person_rejections WHERE person_a=? AND person_b=?',rejected).fetchone():raise ValueError('Existing rejection blocks this association')
     for left in source_row['members']:
      if left['modality']!='voice':continue
      for right in target_row['members']:
       if right['modality']!='voice':continue
       contradiction=store.db.execute('''SELECT 1 FROM observations a JOIN observations b ON a.content_id=b.content_id
        JOIN voice_domains da ON a.content_id=da.content_id AND a.local_speaker=da.local_speaker
        JOIN voice_domains db ON b.content_id=db.content_id AND b.local_speaker=db.local_speaker
        WHERE a.person_id=? AND b.person_id=? AND a.local_speaker<>b.local_speaker AND da.domain=db.domain
        AND MIN(a.end,b.end)>MAX(a.start,b.start)+0.000001 LIMIT 1''',(left['evidence_id'],right['evidence_id'])).fetchone()
       if contradiction:raise ValueError('Overlapping distinct speakers block this association')
     event=store.merge_unified_people(source,target,'Cross-validated interview association: '+link['basis'],chosen_name=link['name'])
     applied.append(dict(link,event_id=event['event_id']))
   for label in plan.get('voice_labels',[]):
    person=store.person_for_evidence('voice',label['voice_id'])
    current=next(p for p in store.unified_people() if p['id']==person)
    if current['name_conflict'] or current['name'] and current['name']!=label['name']:raise ValueError('Existing voice name conflicts with the reviewed label')
    if any(m['modality']=='face' for m in current['members']) and current['name']!=label['name']:raise ValueError('Voice-only labeling cannot rename a visible person')
    if current['name']!=label['name']:
     event=store.rename_unified_person(person,label['name']);named_voices.append(dict(label,person_id=person,event_id=event['event_id']))
     store._audit('av_voice_labeled',dict(label,person_id=person))
   report={'job_id':job,'project_id':store.project_id,'backup':str(backup),'applied':applied,'named_voices':named_voices,'plan':plan,'at':time.time()}
   store._audit('av_validation_applied',report);checkpoint('before_commit')
 checkpoint('database_committed');atomic_json(receipt,report);return report

def main():
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 commands=parser.add_subparsers(dest='command',required=True)
 discover=commands.add_parser('common-voice');discover.add_argument('--rows',type=Path,required=True);discover.add_argument('--output',type=Path,required=True)
 apply=commands.add_parser('apply');apply.add_argument('--project',type=Path,required=True);apply.add_argument('--plan',type=Path,required=True)
 args=parser.parse_args()
 if args.command=='common-voice':
  result=common_voice(json.loads(args.rows.read_text()));atomic_json(args.output,result);print(json.dumps({'validated_recordings':len(result['matches']),'rejected_recordings':len(result['rejected'])}))
 else:
  result=apply_plan(args.project,json.loads(args.plan.read_text()));print(json.dumps({'job_id':result['job_id'],'associations_applied':len(result['applied']),'voice_names_applied':len(result['named_voices']),'backup':result['backup']}))
if __name__=='__main__':main()
