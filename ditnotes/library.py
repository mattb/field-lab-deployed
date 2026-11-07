"""Content-addressed local footage library. Originals remain untouched."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
import uuid

from .engine import Engine
from .scratch import scratch_directory

NOTES='_DIT_NOTES'
MEDIA_EXTENSIONS={'.mp4','.mov','.m4v','.mxf','.mts','.m2ts','.mkv','.avi','.webm','.3gp','.wav','.wave','.flac','.aif','.aiff','.mp3','.m4a','.ogg','.opus','.aac','.caf','.wma','.mka'}
ROOT=Path(__file__).resolve().parents[1]


from .atomic import atomic_json as _save, fsync_dir, fsync_tree


def _read(path,default):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def _sha(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def _stat(path):
    info=Path(path).stat()
    return {'size':info.st_size,'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns,'inode':info.st_ino,'device':info.st_dev}


def infer_project(input_path):
    source=Path(input_path).absolute()
    initial=source if source.is_dir() else source.parent
    for folder in (initial,*initial.parents):
        if (folder/NOTES/'project.json').is_file():return folder.resolve()
    return initial.resolve()


def _safe_source(path,project):
    path=Path(path)
    try:relative=path.relative_to(project)
    except ValueError:return False
    if NOTES in relative.parts:return False
    current=project
    for part in relative.parts:
        current=current/part
        if current.is_symlink():return False
    return path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS


def discover(input_path,project):
    source=Path(input_path).absolute()
    if source.is_symlink():return []
    if source.is_file():return [source] if _safe_source(source,project) else []
    if not source.is_dir():raise ValueError('Input does not exist')
    files=[]
    for folder,dirs,names in os.walk(source,followlinks=False):
        dirs[:]=sorted(d for d in dirs if d!=NOTES and not (Path(folder)/d).is_symlink())
        for name in sorted(names):
            path=Path(folder)/name
            if _safe_source(path,project):files.append(path)
    return sorted(files)


def profile_id(models_root=None,options=None):
    models_root=Path(models_root or ROOT/'models')
    files=[ROOT/'ditnotes/audio_context.py',ROOT/'ditnotes/diarization.py',ROOT/'ditnotes/models.py',ROOT/'ditnotes/faces.py',ROOT/'ditnotes/face_models.py',ROOT/'ditnotes/engine.py',ROOT/'ditnotes/prompts/field-caption.txt',ROOT/'ditnotes/native/feature_select',ROOT/'ditnotes/native/feature_select.swift']
    evidence={str(p.relative_to(ROOT)):_sha(p) if p.is_file() else 'missing' for p in files}
    lock_path=ROOT/'deploy/assets.lock.json'
    if lock_path.is_file():
        # Managed generations are fully SHA-verified by bootstrap/doctor before
        # use. Pin the inference assets, not download timestamps, release paths,
        # browser runtime or search-only models. A UI update can reuse AI output.
        lock=_read(lock_path,{})
        # Python libraries and decoding can alter the same inference recipe even
        # when model weights and prompts are unchanged. Keep this conservative;
        # Node/browser/search asset versions remain outside the analysis key.
        requirements=ROOT/'requirements.lock.txt'
        evidence['runtime/requirements.lock.txt']=_sha(requirements) if requirements.is_file() else 'missing'
        for runtime in ('python','ffmpeg'):
            evidence['runtime/'+runtime]=lock.get(runtime,{}).get('sha256','missing')
        analysis_models={'parakeet','Qwen3.5-4B-4bit','diarization','speaker','faces'}
        for row in lock['model_files']:
            relative=Path(row['path'])
            if relative.parts[0] in analysis_models and 'README' not in relative.name.upper() and 'LICENSE' not in relative.name.upper():
                evidence['model/'+relative.as_posix()]={'sha256':row['sha256'],'bytes':row.get('bytes')}
        for name,value in lock.get('model_json',{}).items():
            if name=='model-lock.json' or Path(name).parts[0] in analysis_models:
                evidence['model-manifest/'+name]=value
    else:
        # Legacy standalone prototypes have no release asset lock. Preserve their
        # previous conservative invalidation rule; field installs use the branch
        # above and never depend on model mtimes.
        for name in ['parakeet','Qwen3.5-4B-4bit']:
            for p in sorted((models_root/name).glob('*')):
                if p.name in ['config.json','provenance.json','tokenizer_config.json','processor_config.json','preprocessor_config.json']:
                    evidence[name+'/'+p.name]=_sha(p)
                elif p.suffix=='.safetensors':
                    evidence[name+'/'+p.name]={'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
    # Numerically identical options and omitted defaults must share the same cache key.
    normalized={'interval':2,'max_frames':12,'timeout':300}
    normalized.update(options or {})
    if normalized.get('vision_width')==960:normalized.pop('vision_width')
    normalized={key:int(value) if isinstance(value,float) and value.is_integer() else value for key,value in normalized.items()}
    return hashlib.sha256(json.dumps({'schema':2 if lock_path.is_file() else 1,'files':evidence,'options':normalized},sort_keys=True).encode()).hexdigest()[:20]


@contextmanager
def _lock(notes):
    notes.mkdir(parents=True,exist_ok=True)
    with (notes/'.process.lock').open('a+') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another process is updating this project')
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def _valid_object(folder,sha,profile):
    folder=Path(folder)
    try:
        if folder.is_symlink():return False
        complete=_read(folder/'complete.json',{})
        if complete.get('content_id')!=sha or complete.get('profile_id')!=profile:return False
        manifest=complete.get('files')
        if not isinstance(manifest,dict) or 'result.json' not in manifest:return False
        for relative,spec in manifest.items():
            path=Path(relative)
            if path.is_absolute() or '..' in path.parts:return False
            # Finder may create/change this directory-view cache while a user
            # browses work/results. It is not an AI artifact or operator data.
            if path.name=='.DS_Store':continue
            target=folder/path
            if any((folder/Path(*path.parts[:i])).is_symlink() for i in range(1,len(path.parts)+1)):return False
            if not target.is_file() or target.stat().st_size!=spec['size'] or _sha(target)!=spec['sha256']:return False
        result=_read(folder/'result.json',{})
        if result.get('status')!='complete':return False
        referenced=[h['frame'] for h in result.get('highlights',[])]+[t[key] for t in result.get('tracks',[]) for key in ['json','text_file','srt'] if key in t]
        return all(p in manifest for p in referenced)
    except (ValueError,KeyError,OSError,TypeError):return False


def _seal(folder,sha,profile,relative,record):
    files={}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():raise ValueError('Generated output contains a symlink')
        if path.is_file() and path.name not in ('complete.json','.DS_Store'):
            files[path.relative_to(folder).as_posix()]={'size':path.stat().st_size,'sha256':_sha(path)}
    fsync_tree(folder)
    _save(folder/'complete.json',{'schema_version':2,'content_id':sha,'profile_id':profile,
          'files':files,'source_relative':relative,'source_record':record})
    fsync_dir(folder)


def _recover_read(path,default):
    try:
        value=_read(path,default)
        if not isinstance(value,dict):raise ValueError('Expected JSON object')
        if Path(path).name=='state.json' and not isinstance(value.get('paths'),dict):raise ValueError('Invalid paths')
        return value
    except (ValueError,OSError,TypeError):
        path=Path(path)
        if path.exists():
            quarantine=path.parent/'.quarantine';quarantine.mkdir(exist_ok=True)
            os.replace(path,quarantine/(path.name+'.'+uuid.uuid4().hex+'.corrupt'))
            fsync_dir(quarantine);fsync_dir(path.parent)
        return default


def _catalog(notes,identity,records):
    catalog={'schema_version':1,'project':dict(identity,updated_at=datetime.now(timezone.utc).isoformat()),
             'assets':[_asset(rel,r,notes) for rel,r in sorted(records.items())]}
    _save(notes/'catalog.json',catalog)
    return catalog


def _failure_text(value,project):
    text=str(value).replace(str(project),'<project>').replace(str(ROOT),'<tool>')
    text=re.sub(r"(['\"])(/[^'\"]*)\1",'<external-path>',text)
    return re.sub(r"(?<![\w>])/(?:[^\s'\"<>]+)",'<external-path>',text)


def _remember_failure(state,record,relative,error,stage,project):
    failure={'id':uuid.uuid4().hex,'path':relative,'source_sha256':record.get('sha256'),
             'profile_id':record['profile_id'],'stage':_failure_text(getattr(error,'stage',stage),project),
             'error':_failure_text(error,project),'error_type':type(error).__name__,
             'time':datetime.now(timezone.utc).isoformat()}
    state.setdefault('failures',[]).append(failure);record['last_failure']=failure
    return failure


def _reconcile_locked(project,notes,identity,state,profile,scopes):
    records=state['paths'];history=dict(records)
    for relative,old in records.items():
        if old.get('error') and not old.get('last_failure'):
            failure=_remember_failure(state,old,relative,RuntimeError(old['error']),'previous_run',project)
            failure['migrated_legacy_error']=True
    for manifest in (notes/'.objects').glob('*/*/complete.json'):
        try:
            data=_read(manifest,{})
            if data.get('source_relative') and data.get('source_record'):
                history.setdefault(data['source_relative'],data['source_record'])
        except (ValueError,OSError):pass
    files=sorted({path for scope in scopes if (project/scope).exists() for path in discover(project/scope,project)})
    updated={};pending=[]
    for path in files:
        relative=path.relative_to(project).as_posix();old=history.get(relative,{})
        record={'asset_id':old.get('asset_id',uuid.uuid4().hex),'aliases':old.get('aliases',[]),'profile_id':profile,'status':'pending'}
        if old.get('last_failure'):record['last_failure']=old['last_failure']
        try:
            before=_stat(path);sha=old.get('sha256') if old.get('stat')==before else _sha(path)
            if _stat(path)!=before:raise RuntimeError('Source changed during reconciliation')
            if not old:
                moved=[(rel,r) for rel,r in history.items() if r.get('sha256')==sha and r.get('stat',{}).get('inode')==before['inode'] and r.get('stat',{}).get('device')==before['device'] and not (project/rel).exists()]
                if len(moved)==1:
                    rel,r=moved[0];record.update(asset_id=r['asset_id'],aliases=sorted(set(r.get('aliases',[])+[rel])))
                    if r.get('last_failure'):record['last_failure']=r['last_failure']
            record.update(sha256=sha,stat=before)
            if _valid_object(notes/'.objects'/sha/profile,sha,profile):
                from .enrichment import register_result
                register_result(project,identity,sha,_read(notes/'.objects'/sha/profile/'result.json',{}),profile)
                record.update(status='ready',error=None)
            else:pending.append(relative)
        except Exception as error:
            failure=_remember_failure(state,record,relative,error,'reconciliation',project)
            record.update(status='error',error=failure['error']);pending.append(relative)
        updated[relative]=record
    state['paths']=updated;_save(notes/'state.json',state);_catalog(notes,identity,updated)
    return {'pending':pending,'catalog':str(notes/'catalog.json'),'counts':{'files':len(files),'ready':len(files)-len(pending),'pending':len(pending)}}


def reconcile(project,*,input_paths=None,models_root=None,options=None):
    """Rebuild from current configured ingests and sealed artifacts without inference."""
    project=Path(project).resolve();notes=project/NOTES
    if notes.is_symlink():raise ValueError('Notes directory must not be a symlink')
    with _lock(notes):
        identity=_recover_read(notes/'project.json',{'id':uuid.uuid4().hex,'name':project.name,'schema_version':1})
        scopes=input_paths if input_paths is not None else identity.get('ingest_paths')
        if not scopes:raise ValueError('Explicit ingest paths required; refusing unrestricted project scan')
        normalized=[]
        for scope in scopes:
            path=Path(scope);path=path if path.is_absolute() else project/path
            if not path.absolute().is_relative_to(project) or '..' in path.parts:raise ValueError('Ingest path escapes project')
            normalized.append(path.relative_to(project).as_posix())
        identity['ingest_paths']=normalized;_save(notes/'project.json',identity)
        state=_recover_read(notes/'state.json',{'schema_version':1,'paths':{}})
        return _reconcile_locked(project,notes,identity,state,profile_id(models_root,options),normalized)


def _asset(relative,record,notes):
    original='/media/'+quote(relative,safe='/')
    asset={'id':record['asset_id'],'content_id':record.get('sha256'),'name':Path(relative).name,'relative_path':relative,
           'aliases':record.get('aliases',[]),'media_url':original,'original_url':original,
           'kind':'audio' if Path(relative).suffix.lower() in {'.wav','.wave','.flac','.aif','.aiff','.mp3','.m4a','.ogg','.opus','.aac','.caf','.wma','.mka'} else 'video',
           'duration':0,'highlights':[],'transcript':[],'tracks':[],'metadata':{},'status':record['status'],'error':record.get('error'),'last_failure':record.get('last_failure')}
    if record['status']!='ready':return asset
    folder=notes/'.objects'/record['sha256']/record['profile_id'];result=_read(folder/'result.json',{})
    prefix='/notes/'+quote(str(folder.relative_to(notes)),safe='/')+'/'
    asset['highlights']=[{'seconds':h['seconds'],'note':h['label'],'raw':h.get('raw'),'needs_review':h.get('needs_review',False),
                          'image_url':prefix+quote(h['frame'],safe='/'),'audio_context':h.get('audio_context',[])} for h in result.get('highlights',[])]
    asset['transcript']=[{'start':s['start'],'end':s['end'],'text':s['text'].strip(),'stream':t['stream_index']}
                          for t in result.get('tracks',[]) for s in t.get('sentences',[])]
    asset['transcript'].sort(key=lambda item:(item['start'],item['stream']))
    asset['tracks']=[{'stream':t['stream_index'],'channels':t.get('channels'),'channel_layout':t.get('channel_layout'),
                      'json_url':prefix+quote(t['json'],safe='/'),'text_url':prefix+quote(t['text_file'],safe='/'),
                      'srt_url':prefix+quote(t['srt'],safe='/')} for t in result.get('tracks',[])]
    asset['duration']=result.get('duration',0)
    videos=[s for s in result.get('probe',{}).get('streams',[]) if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')]
    asset['kind']='video' if videos else 'audio'
    if videos:asset.update(width=videos[0].get('width'),height=videos[0].get('height'))
    if asset['highlights']:asset['poster_url']=asset['highlights'][0]['image_url']
    asset['metadata']={'analysis_wall_seconds':result.get('timing',{}).get('total_seconds'),'profile_id':record['profile_id'],
                       'visual_method':'Sparse sampled highlights; not exhaustive shot coverage','probe':result.get('probe',{}),'models':result.get('models',{})}
    asset['result_url']=prefix+'result.json'
    return asset


def process(input_path,project=None,*,models_root=None,engine=None,options=None,progress=None,progress_event=None):
    """Process file/folder, return {project,catalog,counts,failures}; failure history remains in state.json.

    Inject an Engine-compatible object for tests. A caller-supplied engine remains open.
    Existing SHA/profile results survive file moves and are shared by duplicate copies.
    """
    source=Path(input_path).absolute()
    if source.is_symlink():raise ValueError('Symlink inputs are excluded')
    project=Path(project).resolve() if project else infer_project(source)
    source=source.resolve()
    if not source.is_relative_to(project):raise ValueError('Input must be inside the project')
    if source.is_symlink():raise ValueError('Symlink inputs are excluded')
    if NOTES in source.relative_to(project).parts:raise ValueError('Generated notes are not input footage')
    notes=project/NOTES
    if notes.is_symlink():raise ValueError('Notes directory must not be a symlink')
    options=options or {};profile=profile_id(models_root,options)
    counts={'processed':0,'reused':0,'failed':0,'files':0}
    owned=engine is None
    with _lock(notes):
        identity=_recover_read(notes/'project.json',{'id':uuid.uuid4().hex,'name':project.name,'schema_version':1})
        scope=source.relative_to(project).as_posix()
        identity['ingest_paths']=sorted(set(identity.get('ingest_paths',[])+[scope]))
        _save(notes/'project.json',identity)
        state=_recover_read(notes/'state.json',{'schema_version':1,'paths':{}})
        failure_offset=len(state.get('failures',[]))
        _reconcile_locked(project,notes,identity,state,profile,identity['ingest_paths'])
        records=state['paths'];previous=dict(records)
        files=discover(source,project);counts['files']=len(files)
        current_stage='scanning'
        def emit(stage,**values):
            nonlocal current_stage
            current_stage=stage
            if progress_event:progress_event(dict(stage=stage,**values))
        emit('scanning',total=len(files),completed=0,failed=0)
        if engine is None:engine=Engine(models_root=models_root,**options)
        completed_files=0
        try:
            for path in files:
                relative=path.relative_to(project).as_posix();old=records.get(relative,{})
                emit('hashing',current=relative)
                record={'asset_id':old.get('asset_id',uuid.uuid4().hex),'aliases':old.get('aliases',[]),'status':'error','profile_id':profile}
                if old.get('last_failure'):record['last_failure']=old['last_failure']
                try:
                    before=_stat(path)
                    sha=old.get('sha256') if old.get('stat')==before else None
                    if not sha:sha=_sha(path)
                    if _stat(path)!=before:raise RuntimeError('Source changed while hashing; retry after recording/copying finishes')
                    record.update(sha256=sha,stat=before)
                    if not old:
                        # Preserve an alias identity for a real rename, but keep independent duplicate copies distinct.
                        moved=[(rel,r) for rel,r in previous.items() if r.get('sha256')==sha and r.get('stat',{}).get('inode')==before['inode']
                               and r.get('stat',{}).get('device')==before['device'] and not (project/rel).exists()]
                        if len(moved)==1:
                            rel,r=moved[0];record['asset_id']=r['asset_id'];record['aliases']=sorted(set(r.get('aliases',[])+[rel]))
                            if r.get('last_failure'):record['last_failure']=r['last_failure']
                    target=notes/'.objects'/sha/profile
                    if _valid_object(target,sha,profile):counts['reused']+=1
                    else:
                        work_root=notes/'.work';work_root.mkdir(exist_ok=True)
                        with scratch_directory(prefix='analyze-',dir=work_root) as scratch:
                            temporary=Path(scratch)
                            emit('analysis',current=relative)
                            if hasattr(engine,'progress_callback'):engine.progress_callback=lambda stage:emit(stage,current=relative)
                            engine.analyze(path,temporary)
                            if _stat(path)!=before:raise RuntimeError('Source changed during analysis; result was not published')
                            emit('publication',current=relative)
                            _seal(temporary,sha,profile,relative,record)
                            if not _valid_object(temporary,sha,profile):raise RuntimeError('Analysis output failed publication validation')
                            if _stat(path)!=before:raise RuntimeError('Source changed before publication')
                            target.parent.mkdir(parents=True,exist_ok=True)
                            fsync_dir(target.parent.parent)
                            if target.exists():
                                quarantine=notes/'.quarantine';quarantine.mkdir(exist_ok=True)
                                target.replace(quarantine/(sha+'-'+profile+'-'+uuid.uuid4().hex))
                                fsync_dir(quarantine);fsync_dir(target.parent)
                            temporary.replace(target);fsync_dir(target.parent);counts['processed']+=1
                    if _stat(path)!=before:raise RuntimeError('Source changed before catalog update; retry')
                    current_stage='registration'
                    from .enrichment import register_result
                    register_result(project,identity,sha,_read(target/'result.json',{}),profile)
                    record.update(status='ready',error=None)
                    if progress:progress(relative,'ready')
                except Exception as error:
                    failure=_remember_failure(state,record,relative,error,current_stage,project)
                    message=failure['error'];record.update(status='error',error=message);counts['failed']+=1
                    # Persist the cause before a UI callback can interrupt the batch.
                    records[relative]=record;_save(notes/'state.json',state)
                    if progress:progress(relative,'error: '+message)
                records[relative]=record
                # Checkpoint per file so an interrupted batch resumes completed items.
                _save(notes/'state.json',state)
                _catalog(notes,identity,records)
                if record['status']=='ready':completed_files+=1
                emit('file_complete',current=relative,completed=completed_files,failed=counts['failed'])
            # Missing files and symlinks cannot remain live browser media aliases.
            records={rel:r for rel,r in records.items() if _safe_source(project/rel,project)}
            state['paths']=records;_save(notes/'state.json',state)
            assets=[_asset(rel,r,notes) for rel,r in sorted(records.items())]
            catalog={'schema_version':1,'project':dict(identity,updated_at=datetime.now(timezone.utc).isoformat()),'assets':assets}
            _save(notes/'catalog.json',catalog)
        finally:
            if owned:engine.close()
    return {'project':str(project),'catalog':str(notes/'catalog.json'),'counts':counts,'failures':state.get('failures',[])[failure_offset:]}
