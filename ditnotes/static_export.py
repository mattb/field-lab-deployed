"""Build an explicit read-only public-data bundle; never copy a project database.

Generated assets are immutable snapshots. Only source media, referenced stills,
public model assets and selected display/search metadata can enter the bundle.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,os,shutil,sqlite3,time,uuid,subprocess,sys,re
from pathlib import Path
from urllib.parse import unquote,urlsplit
from .atomic import atomic_json,fsync_dir
from .catalog_index import read_snapshot
from .project_store import ProjectStore
from .people_api import review_people

def _local(notes,url):
 if not isinstance(url,str) or not url.startswith('/notes/') or urlsplit(url).query or urlsplit(url).fragment:raise ValueError('Only local note image URLs may be exported')
 rel=Path(unquote(url[7:]))
 if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe note URL')
 current=notes
 for part in rel.parts:
  current=current/part
  if current.is_symlink():raise ValueError('Image symlinks are not export inputs')
 if not current.is_file() or not current.resolve().is_relative_to(notes.resolve()):raise ValueError('Missing note image')
 if current.suffix.lower() not in ('.jpg','.jpeg','.png','.webp'):raise ValueError('Only display images may enter notes export')
 return current,rel

def _copy(source,target):
 target.parent.mkdir(parents=True,exist_ok=True)
 if sys.platform=='darwin':
  result=subprocess.run(['/bin/cp','-c',str(source),str(target)],capture_output=True)
  if result.returncode==0:return
 shutil.copyfile(source,target)

def export(project,output,*,tool_root,ui_dist):
 project=Path(project).resolve(strict=True);output=Path(output).absolute();tool_root=Path(tool_root).resolve(strict=True);ui_dist=Path(ui_dist).resolve(strict=True);notes=project/'_DIT_NOTES'
 if output.exists():raise ValueError('Choose a new snapshot output; existing exports are never overwritten')
 if output.resolve().is_relative_to(project) or project.is_relative_to(output.resolve()):raise ValueError('Export must be outside the media project')
 pair=read_snapshot(project)
 if not pair:raise ValueError('Complete sealed catalog required')
 catalog=pair['catalog'];catalog['catalog_revision']=pair['vectors']['catalog_revision'];projection=catalog.get('search_projection',{})
 db=notes/'project.sqlite3'
 # The database must already be migrated and indexed. Export itself is read-only.
 with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
  base_revision=int(c.execute("SELECT value FROM meta WHERE key='edit_revision'").fetchone()[0])
  if not c.execute("SELECT 1 FROM meta WHERE key='unified_people_version'").fetchone():raise ValueError('Migrate People before exporting')
 if projection.get('edit_revision')!=base_revision:raise ValueError('Metadata has changed; finish search publication before exporting')
 # Review projection helper uses ProjectStore; work only on a disposable SQLite backup.
 output.parent.mkdir(parents=True,exist_ok=True);stage=output.with_name('.'+output.name+'-'+uuid.uuid4().hex+'.pending');stage.mkdir();scratch=stage.parent/('.registry-'+uuid.uuid4().hex+'.sqlite3')
 try:
  with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(scratch) as dst:src.backup(dst)
  with ProjectStore(scratch,project_id=catalog['project']['id']) as store:
   if store.edit_revision!=base_revision:raise ValueError('Metadata changed while exporting; retry')
   review=review_people(store,catalog)
   tags=store.tag_vocabulary();assignments=store.asset_tag_assignments()
  manifest=[];images={}
  def note(url):
   if not url:return ''
   source,rel=_local(notes,url);dest=Path('notes')/rel
   if str(dest) not in images:
    _copy(source,stage/dest);images[str(dest)]=True
   return '/'+dest.as_posix()
  docs=[]
  allowed={'id','kind','text','seconds','end','stream','image','embed','source','assetId','contentId','title','path','assetKind','personIds','personNames','visiblePersonIds','heardPersonIds','speakerIds','faceIds','faceNames','tagIds','tagNames','identityBasis','annotationId','subjectId','sentence_indices'}
  for source in projection.get('documents',[]):
   d={k:copy.deepcopy(v) for k,v in source.items() if k in allowed}
   if d.get('image'):d['image']=note(d['image'])
   docs.append(d)
  assets=[];contents={}
  for a in catalog['assets']:
   if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',str(a['id'])):raise ValueError('Unsafe asset ID')
   cid=a.get('content_id') or a['id'];contents.setdefault(cid,[]).append(a['id']);rel=Path(a['relative_path']);source=project/rel
   current=project
   for part in rel.parts:
    current=current/part
    if current.is_symlink():raise ValueError('Media symlinks are not export inputs')
   if rel.is_absolute() or '..' in rel.parts or source.is_symlink() or not source.resolve().is_relative_to(project):raise ValueError('Unsafe media source')
   dest=Path('media')/(a['id']+source.suffix.lower());_copy(source,stage/dest)
   keep={k:copy.deepcopy(a[k]) for k in ('id','content_id','name','relative_path','kind','duration','width','height') if k in a}
   keep.update(media_url='/'+dest.as_posix(),original_url='/'+dest.as_posix(),poster_url=note(a.get('poster_url','')),
    transcript=[{k:s[k] for k in ('start','end','text','stream') if k in s} for s in a.get('transcript',[])],
    highlights=[{'seconds':h['seconds'],'note':h['note'],'image_url':note(h['image_url'])} for h in a.get('highlights',[])])
   keep['tags']=[{'id':t['id'],'name':t['name']} for t in assignments if t['content_id']==cid];assets.append(keep)
   manifest.append({'path':dest.as_posix(),'bytes':source.stat().st_size,'content_id':cid})
  people=[]
  for p in review['people']:
   faces=[{k:f[k] for k in ('content_id','seconds','crop_ref','frame_ref') if k in f} for f in p['face_samples']]
   for f in faces:
    for k in ('crop_ref','frame_ref'):
     if f.get(k):f[k]=note(f[k])
   people.append({'id':p['id'],'name':p['name'],'name_conflict':p['name_conflict'],'notes':p['notes'],'face_samples':faces,'voice_samples':[{k:v[k] for k in ('content_id','start','end') if k in v} for v in p['voice_samples']],'appearances':p['appearances'],'created_at':p['created_at']})
  browse={'asset_times':{},'tags':[],'people':[]}
  for t in tags:browse['tags'].append({**t,'contents':sorted({a['content_id'] for a in assignments if a['id']==t['id']})})
  for p in people:browse['people'].append({'id':p['id'],'name':p['name'],'name_conflict':p['name_conflict'],'contents':sorted({a['content_id'] for a in p['appearances']}),'sample':p['face_samples'][0] if p['face_samples'] else None,'created_at':p['created_at']})
  for a in assets:browse['asset_times'][a['id']]=(project/a['relative_path']).stat().st_mtime
  result={'schema_version':1,'readonly':True,'catalog_revision':catalog['catalog_revision'],'project':{'id':catalog['project']['id'],'name':catalog['project']['name']},'assets':assets,'search_projection':{'schema_version':1,'edit_revision':base_revision,'documents':docs}}
  atomic_json(stage/'data/catalog.json',result);atomic_json(stage/'data/browse.json',browse);atomic_json(stage/'data/people.json',{'people':people})
  readiness=json.loads((tool_root/'models/.runtime/search-readiness.json').read_text())
  if not readiness.get('ready'):raise ValueError('Prepared pinned browser models required')
  atomic_json(stage/'data/readiness.json',{k:readiness[k] for k in ('schema_version','ready','models','runtime')})
  import prepare_search
  if not prepare_search.check(tool_root,write=False)['ready']:raise ValueError('Browser model checksums failed')
  for kind,spec in prepare_search.MODEL_LOCK.items():
   for f in spec['files']:_copy(tool_root/'models/.runtime/search-models'/kind/f['path'],stage/'models'/kind/f['path'])
  for name in prepare_search.RUNTIME_LOCK:_copy(tool_root/'models/.runtime/browser'/name,stage/'runtime'/name)
  for source in ui_dist.rglob('*'):
   if not source.is_file():continue
   rel=source.relative_to(ui_dist)
   if source.is_symlink() or not source.resolve().is_relative_to(ui_dist):raise ValueError('Unsafe UI asset')
   if (rel.as_posix() in ('index.html','static.html') or rel.parts[0]=='assets') and source.suffix in ('.html','.js','.mjs','.css','.wasm'):_copy(source,stage/rel)
  if (stage/'static.html').is_file():os.replace(stage/'static.html',stage/'index.html')
  if not (stage/'index.html').is_file():raise ValueError('Static web entry is missing')
  atomic_json(stage/'export-manifest.json',{'schema_version':1,'readonly':True,'created_at':time.time(),'catalog_revision':catalog['catalog_revision'],'edit_revision':base_revision,'assets':manifest,'image_files':len(images),'people':len(people),'no_database_or_biometric_embeddings':True})
  # Confirm live metadata did not change during the export; no half-snapshot publication.
  with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
   if int(c.execute("SELECT value FROM meta WHERE key='edit_revision'").fetchone()[0])!=base_revision:raise ValueError('Metadata changed during export; retry')
  os.replace(stage,output);fsync_dir(output.parent)
  return {'output':str(output),'assets':len(assets),'people':len(people),'documents':len(docs),'images':len(images),'revision':base_revision}
 finally:
  for suffix in ('','-wal','-shm'):Path(str(scratch)+suffix).unlink(missing_ok=True)
  # A failed stage is retained for diagnosis but never activated.

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--tool-root',required=True,type=Path);p.add_argument('--ui-dist',required=True,type=Path);a=p.parse_args();print(json.dumps(export(a.project,a.output,tool_root=a.tool_root,ui_dist=a.ui_dist)))
if __name__=='__main__':main()
