"""One shoot catalogue over independently owned primary-volume projects.

Only derived metadata, SQLite backup snapshots and referenced raster previews are
cached on the laptop. Original media and project databases remain on their own
volumes. Every live volume access revalidates its stable UUID; reconnecting never
changes the active-primary pointer.
"""
from __future__ import annotations
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import types
from urllib.parse import unquote, urlsplit
import uuid

from .atomic import atomic_json, fsync_dir, fsync_tree
from .catalog_index import Snapshots

ID=re.compile(r'^[A-Za-z0-9_-]{1,128}$')
RASTER={'.jpg','.jpeg','.png','.webp','.avif'}


def _hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _safe(root,relative,*,exists=True):
    root=Path(root)
    rel=Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in ('.','..') for p in rel.parts):raise ValueError('Unsafe relative path')
    path=root
    if root.is_symlink():raise ValueError('Symlink root refused')
    for part in rel.parts:
        path/=part
        if path.is_symlink():raise ValueError('Symlink file refused')
    if exists and not path.is_file():raise FileNotFoundError(str(path))
    return path


def _dump(path,value):atomic_json(path,value)


def _ns(primary,value):return primary+'::'+str(value)


def _rewrite(value,primary):
    """Namespace identities without merging unrelated names/evidence."""
    ids={'id','content_id','contentId','assetId','document_id','person_id','face_id','evidence_id','observation_id','subject_id','candidate_id','source_id','target_id','person_a','person_b','face_a','face_b','event_id','annotation_id','link_id'}
    lists={'contents','visible_contents','heard_contents','asset_ids','visiblePersonIds','heardPersonIds','personIds','faceIds','voiceIds','annotationIds','tagIds'}
    if isinstance(value,list):return [_rewrite(v,primary) for v in value]
    if not isinstance(value,dict):return value
    result={}
    for key,item in value.items():
        if isinstance(item,str) and item.startswith(('/media/','/notes/')):result[key]='/primaries/'+primary+item
        elif key.startswith('original_'):result[key]=copy.deepcopy(item)
        elif key in ids and isinstance(item,str):result[key]=_ns(primary,item)
        elif key in lists and isinstance(item,list):result[key]=[_ns(primary,v) for v in item]
        else:result[key]=_rewrite(item,primary)
    return result


def _image_refs(value):
    if isinstance(value,dict):
        for item in value.values():yield from _image_refs(item)
    elif isinstance(value,list):
        for item in value:yield from _image_refs(item)
    elif isinstance(value,str) and value.startswith('/notes/'):
        parsed=urlsplit(value)
        if parsed.query or parsed.fragment:return
        relative=unquote(parsed.path[len('/notes/'):])
        if Path(relative).suffix.lower() in RASTER:yield relative


def _fallback_documents(catalog):
    # Legacy raw catalogues use the same document/timestamp structure as the viewer.
    documents=[]
    for index,asset in enumerate(catalog.get('assets',[])):
        aid=str(asset.get('id',asset.get('content_id',index)));content=str(asset.get('content_id',aid))
        base={'assetId':aid,'contentId':content,'title':asset.get('name',''),'path':asset.get('relative_path',''),'assetKind':asset.get('kind','video')}
        documents.append({**base,'id':aid+':file','kind':'file','text':asset.get('name','')+' '+asset.get('relative_path',''),'seconds':0,'embed':False})
        for number,highlight in enumerate(sorted(asset.get('highlights',[]),key=lambda h:float(h.get('seconds',h.get('start',0)) or 0))):
            text=highlight.get('note') or highlight.get('text') or ''
            if text.strip():documents.append({**base,'id':aid+':visual:'+str(number),'kind':'visual','text':text,'seconds':highlight.get('seconds',highlight.get('start',0)),'image':highlight.get('image_url',highlight.get('image','')),'embed':True})
        transcript=asset.get('transcript') or asset.get('sentences') or []
        if isinstance(transcript,dict):transcript=transcript.get('sentences',[])
        if not transcript:transcript=[{**sentence,'stream':track.get('stream',track.get('index'))} for track in asset.get('transcripts',[]) for sentence in track.get('sentences',track.get('segments',[]))]
        for number,sentence in enumerate(sorted((row for row in transcript if row.get('text','').strip()),key=lambda row:float(row.get('start',row.get('seconds',0)) or 0))):
            words=sentence['text'].split()
            for offset in range(0,len(words),85):documents.append({**base,'id':aid+':speech:'+str(number)+':'+str(offset//85),'kind':'speech','text':' '.join(words[offset:offset+85]),'seconds':sentence.get('start',sentence.get('seconds',0)),'end':sentence.get('end'),'stream':sentence.get('stream'),'embed':True})
    return documents


class MultiVolumeLibrary:
    def __init__(self,shared,*,probe=None,checkpoint=None):
        self.shared=Path(shared).resolve(strict=True);self.registry_path=self.shared/'shoot.json'
        if probe is None:
            from .backup_volumes import mac_identity
            probe=mac_identity
        self.probe=probe;self.checkpoint=checkpoint or (lambda phase:None)
        self.lock=threading.RLock();self.contexts={};self.signatures={};self._cached={};self.errors={};self.last_refresh={}
        self.registry()

    @classmethod
    def discover(cls,tool_root):
        config=Path(tool_root)/'config.json'
        if not config.exists():return None
        shared=config.resolve().parent
        if not (shared/'shoot.json').is_file():return None
        return cls(shared)

    def registry(self):
        if self.registry_path.is_symlink():raise ValueError('Shoot registry must not be a symlink')
        value=json.loads(self.registry_path.read_text())
        if value.get('schema')!=1 or not ID.fullmatch(value.get('shoot_id','')) or not isinstance(value.get('profiles'),dict):raise ValueError('Invalid shoot registry')
        if value.get('active_primary') not in value['profiles']:raise ValueError('Invalid active primary')
        for pid,profile in value['profiles'].items():
            if not ID.fullmatch(pid) or profile.get('id',pid)!=pid:raise ValueError('Invalid primary identifier')
        return value

    def profile(self,primary):
        profile=self.registry()['profiles'].get(primary)
        if profile is None:raise ValueError('Unknown primary')
        return profile

    def require_profile(self,primary):
        profile=self.profile(primary);mount=Path(profile['volume_mount']);root=Path(profile['project_root'])
        if not mount.is_dir() or not root.is_dir():raise OSError('Connect '+profile.get('label',primary))
        if mount.is_symlink() or root.is_symlink() or not root.absolute().is_relative_to(mount.absolute()) or root.absolute()==mount.absolute():raise ValueError('Invalid primary project path')
        for part in root.relative_to(mount).parts:
            mount=mount/part
            if mount.is_symlink():raise ValueError('Symlink primary project path')
        if (root/'_DIT_NOTES').is_symlink():raise ValueError('Primary metadata root is a symlink')
        info=self.probe(Path(profile['volume_mount']))
        actual_mount=Path(info.get('mount') or info.get('MountPoint') or '')
        actual_uuid=str(info.get('uuid') or info.get('VolumeUUID') or '').upper()
        if actual_uuid!=str(profile['volume_uuid']).upper() or actual_mount.resolve()!=Path(profile['volume_mount']).resolve():raise OSError('Connected volume identity does not match '+profile.get('label',primary))
        return root

    def available(self,primary):
        try:self.require_profile(primary);return True
        except (OSError,ValueError,KeyError):return False

    def _root(self,primary):
        shoot=self.registry()['shoot_id'];root=self.shared/'runtime/shoot-library'/shoot/primary
        if any(p.is_symlink() for p in [root,*root.parents] if p!=self.shared and self.shared in p.parents):raise ValueError('Cache path is a symlink')
        root.mkdir(parents=True,exist_ok=True);return root

    def bootstrap_project(self):
        root=self.shared/'runtime/shoot-library'/self.registry()['shoot_id']/'bootstrap'
        root.mkdir(parents=True,exist_ok=True);return root

    def status(self):
        registry=self.registry();profiles=[]
        for pid,profile in registry['profiles'].items():
            cached=self._snapshot(pid)
            profiles.append({'id':pid,'label':profile.get('label',pid),'volume_uuid':profile['volume_uuid'],
                             'available':self.available(pid),'active':pid==registry['active_primary'],
                             'cached':cached is not None,'asset_count':len(cached['catalog'].get('assets',[])) if cached else 0,
                             'metadata_error':self.errors.get(pid),'current_backup_set':profile.get('backup',{}).get('current_set'),'backup':copy.deepcopy(profile.get('backup',{'current_set':None,'sets':{}}))})
        return {'shoot_id':registry['shoot_id'],'name':registry.get('name','Shoot'),'active_primary':registry['active_primary'],
                'profiles':profiles,'revision':registry.get('revision',0),'pending_cutover':bool(registry.get('pending_cutover'))}

    def _signature(self,root):
        values=[]
        if (root/'_DIT_NOTES/project.sqlite3-shm').is_symlink():raise ValueError('Metadata shared-memory symlink refused')
        for rel in ['_DIT_NOTES/current.json','_DIT_NOTES/catalog.json','_DIT_NOTES/project.sqlite3','_DIT_NOTES/project.sqlite3-wal']:
            path=root/rel
            if path.is_symlink():raise ValueError('Metadata symlink refused')
            try:s=path.stat();values.append((rel,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
            except FileNotFoundError:values.append((rel,None))
        return tuple(values)

    def refresh(self,primary=None):
        """Refresh connected profiles; one unreadable primary cannot hide the others."""
        with self.lock:
            for pid in ([primary] if primary else self.registry()['profiles']):
                if not self.available(pid):continue
                if primary is None and time.monotonic()-self.last_refresh.get(pid,0)<2:continue
                self.last_refresh[pid]=time.monotonic()
                try:
                    root=self.require_profile(pid);signature=self._signature(root)
                    if self.signatures.get(pid)==signature and self._snapshot(pid) is not None:continue
                    self._capture(pid,root);self.signatures[pid]=signature;self.errors.pop(pid,None)
                except Exception as error:
                    self.errors[pid]=str(error)
                    if primary is not None:raise
            return self.status()

    def _snapshot(self,primary):
        try:return self.cached(primary)
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.errors[primary]=str(error);return None

    def _capture(self,primary,root):
        cache=self._root(primary)
        with (cache/'cache.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            stage=cache/('.pending-'+uuid.uuid4().hex);stage.mkdir();project=stage/'project';notes=project/'_DIT_NOTES';notes.mkdir(parents=True)
            if (root/'_DIT_NOTES').is_symlink():raise ValueError('Notes root is a symlink')
            source=Snapshots(root);pair=source.pair()
            if pair:
                catalog={**copy.deepcopy(pair['catalog']),'catalog_revision':pair['vectors']['catalog_revision']};vectors=copy.deepcopy(pair['vectors'])
            else:
                catalog=source.read();vectors={'revision':{},'vectors':[]}
            if not isinstance(catalog.get('search_projection',{}).get('documents'),list):catalog['search_projection']={'documents':_fallback_documents(catalog)}
            _dump(notes/'catalog.json',catalog)
            db=root/'_DIT_NOTES/project.sqlite3'
            if db.exists():
                _safe(root,'_DIT_NOTES/project.sqlite3')
                with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(notes/'project.sqlite3') as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Cached metadata database failed integrity check')
            self.checkpoint('database_copied')
            state={'people':[],'observations':[],'faces':[],'face_observations':[],'annotations':[],'links':[],'asset_tags':[],'tag_vocabulary':[],'edit_revision':0,'indexed_edit_revision':0,'index_error':None}
            browse={'tags':[],'faces':[],'people':[],'asset_times':{}}
            people={'people':[],'candidates':[],'history':[],'edit_revision':0}
            if db.exists():
                from .metadata_api import MetadataAPI
                api=object.__new__(MetadataAPI);api.server=types.SimpleNamespace(project=project,catalog=types.SimpleNamespace(read=lambda:catalog));api.error=None
                api.people_migration_lock=threading.Lock();api.people_migration_ready=False;api.changed=threading.Event()
                api.ensure_people_ready();state=api.state();browse=api.browse();people=api.people_review()
            values={'catalog':catalog,'vectors':vectors,'state':state,'browse':browse,'people':people}
            images=sorted(set(_image_refs(values)))
            for relative in images:
                source_path=_safe(root/'_DIT_NOTES',relative)
                target=stage/'images'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source_path,target)
            for name,value in values.items():_dump(stage/(name+'.json'),value)
            # SQLite scratch is not needed in the published cache; only derived JSON and previews survive.
            shutil.rmtree(project)
            checksums={str(path.relative_to(stage)):_hash(path) for path in stage.rglob('*') if path.is_file()}
            _dump(stage/'seal.json',{'schema':1,'primary_id':primary,'checksums':checksums})
            fsync_tree(stage)
            if self.require_profile(primary)!=root:raise ValueError('Primary path changed during cache capture')
            self.checkpoint('before_cache_publish')
            generation='generation-'+uuid.uuid4().hex;os.rename(stage,cache/generation);fsync_dir(cache)
            atomic_json(cache/'current.json',{'generation':generation,'seal_sha256':_hash(cache/generation/'seal.json')})
            self._cached.pop(primary,None);self.checkpoint('cache_published')
            # Cache generations are derived, not operator history. Bound laptop growth.
            completed=sorted((p for p in cache.iterdir() if re.fullmatch(r'generation-[0-9a-f]{32}',p.name) and p.is_dir() and not p.is_symlink()),key=lambda p:p.stat().st_mtime_ns,reverse=True)
            for obsolete in completed[3:]:
                if obsolete.name!=generation:shutil.rmtree(obsolete)

    def cached(self,primary):
        cache=self._root(primary);pointer=cache/'current.json'
        if not pointer.exists():return None
        if pointer.is_symlink():raise ValueError('Cache pointer is a symlink')
        value=json.loads(pointer.read_text());generation=value.get('generation','')
        if not re.fullmatch(r'generation-[0-9a-f]{32}',generation):raise ValueError('Invalid cache generation')
        root=cache/generation;seal_path=_safe(root,'seal.json')
        signature=(generation,value.get('seal_sha256'))
        if self._cached.get(primary,{}).get('signature')==signature:return self._cached[primary]['value']
        if _hash(seal_path)!=value['seal_sha256']:raise ValueError('Cache seal mismatch')
        seal=json.loads(seal_path.read_text())
        if seal.get('primary_id')!=primary:raise ValueError('Cache primary mismatch')
        for relative,expected in seal['checksums'].items():
            if _hash(_safe(root,relative))!=expected:raise ValueError('Cached snapshot checksum mismatch')
        result={name:json.loads(_safe(root,name+'.json').read_text()) for name in ['catalog','vectors','state','browse','people']}
        result['root']=root;result['images']={name[len('images/'):] for name in seal['checksums'] if name.startswith('images/')}
        result['image_checksums']={name[len('images/'):]:digest for name,digest in seal['checksums'].items() if name.startswith('images/')}
        self._cached[primary]={'signature':signature,'value':result};return result

    def catalog(self):
        registry=self.registry();assets=[];documents=[];revisions=[]
        for pid,profile in registry['profiles'].items():
            cached=self._snapshot(pid)
            if not cached:continue
            available=self.available(pid);catalog=cached['catalog'];revisions.append((pid,available,catalog.get('catalog_revision'),cached['vectors'].get('catalog_revision')))
            for original in catalog.get('assets',[]):
                asset=_rewrite(original,pid);asset.update(primary_id=pid,primary_label=profile.get('label',pid),available=available,offline=not available,
                    original_asset_id=str(original.get('id',original.get('content_id',''))),original_content_id=str(original.get('content_id',original.get('id',''))),
                    original_relative_path=original.get('relative_path',''),metadata_base='/primaries/'+pid)
                asset['id']=_ns(pid,asset['original_asset_id']);asset['content_id']=_ns(pid,asset['original_content_id']);assets.append(asset)
            for original in catalog.get('search_projection',{}).get('documents',[]):
                doc=_rewrite(original,pid);doc.update(primary_id=pid,primary_label=profile.get('label',pid),available=available,
                    original_document_id=original.get('id'),original_path=original.get('path',''))
                doc['path']=pid+'/'+original.get('path','');documents.append(doc)
        revision=hashlib.sha256(json.dumps([registry.get('revision'),revisions],sort_keys=True).encode()).hexdigest()
        return {'project':{'id':registry['shoot_id'],'name':registry.get('name','Shoot')},'shoot_id':registry['shoot_id'],'catalog_revision':revision,
                'assets':assets,'search_projection':{'documents':documents},'multivolume':True}

    def vectors(self):
        revision=None;vectors=[];conflict=False
        for pid in self.registry()['profiles']:
            cached=self._snapshot(pid)
            if not cached:continue
            payload=cached['vectors']
            if not payload.get('vectors'):continue
            if revision is None:revision=payload.get('revision',{})
            if payload.get('revision',{})!=revision:conflict=True;continue
            vectors.extend([{**entry,'document_id':_ns(pid,entry['document_id'])} for entry in payload.get('vectors',[])])
        # Incompatible model revisions must be re-embedded by the same existing worker, never mixed.
        return {'catalog_revision':self.catalog()['catalog_revision'],'revision':revision or {},'vectors':vectors,'partial':conflict}

    def browse(self):
        combined={'tags':[],'faces':[],'people':[],'asset_times':{}};tags={}
        for pid,profile in self.registry()['profiles'].items():
            cached=self._snapshot(pid)
            if not cached:continue
            value=_rewrite(cached['browse'],pid)
            for row in value.get('tags',[]):
                key=' '.join(row.get('name','').split()).casefold();entry=tags.setdefault(key,{**row,'id':'shoot-tag-'+hashlib.sha256(key.encode()).hexdigest()[:20],'contents':[]})
                entry['contents']=sorted(set(entry['contents'])|set(row.get('contents',[])))
            for kind in ['faces','people']:
                for row in value.get(kind,[]):row.update(primary_id=pid,primary_label=profile.get('label',pid),available=self.available(pid));combined[kind].append(row)
            combined['asset_times'].update({_ns(pid,key):stamp for key,stamp in cached['browse'].get('asset_times',{}).items()})
        combined['tags']=list(tags.values());return combined

    def people(self):
        result={'people':[],'candidates':[],'history':[],'edit_revision':0,'readonly':True}
        for pid,profile in self.registry()['profiles'].items():
            cached=self._snapshot(pid)
            if not cached:continue
            value=_rewrite(cached['people'],pid)
            for person,original in zip(value['people'],cached['people']['people']):
                person.update(primary_id=pid,primary_label=profile.get('label',pid),original_person_id=original['id'],available=self.available(pid))
            for key in ['people','candidates','history']:result[key].extend(value.get(key,[]))
        return result

    def cached_api(self,primary,path,query=None):
        cached=self.cached(primary)
        if not cached:raise FileNotFoundError('No cached metadata for this primary')
        key={'/api/catalog':'catalog','/api/metadata':'state','/api/annotations-state':'state','/api/browse':'browse','/api/people/review':'people','/api/search-vectors':'vectors'}.get(path)
        if key:return {**copy.deepcopy(cached[key]),'primary_id':primary,'available':self.available(primary),'readonly':not self.available(primary)}
        if path=='/api/asset-tags':
            content=(query or {}).get('content_id',[])
            if len(content)!=1:raise ValueError('One original content_id is required')
            known={str(a.get('content_id',a.get('id'))) for a in cached['catalog'].get('assets',[])}
            if content[0] not in known:raise ValueError('Unknown source clip')
            state=cached['state'];rows=[{'id':r['id'],'name':r['name']} for r in state.get('asset_tags',[]) if r['content_id']==content[0]]
            return {'content_id':content[0],'tags':rows,'vocabulary':state.get('tag_vocabulary',[]),'revision':0,'readonly':not self.available(primary)}
        raise FileNotFoundError('Offline operation unavailable')

    def resolve_image(self,url):
        match=re.fullmatch(r'/primaries/([A-Za-z0-9_-]{1,128})/notes/(.+)',url)
        if not match:raise ValueError('Not a cached primary image')
        pid,relative=match.groups();relative=unquote(relative);cached=self._snapshot(pid)
        if not cached or relative not in cached['images']:raise ValueError('Image was not referenced by this catalogue')
        path=_safe(cached['root']/'images',relative)
        if _hash(path)!=cached['image_checksums'][relative]:raise ValueError('Cached image checksum mismatch')
        return path

    def close(self):
        for context in self.contexts.values():
            if hasattr(context,'chat'):context.chat.close()
            context.metadata.close()
        self.contexts.clear()


class _PrimaryContext:
    def __init__(self,library,primary,parent):
        self.library,self.primary,self.parent=library,primary,parent
        self.project=library.require_profile(primary);self.tool_root=parent.tool_root
        self.catalog=Snapshots(self.project);self.search_vectors=self.catalog.vectors
        self.search_backend=parent.search_backend;self.server_port=parent.server_port
        self.readiness=parent.readiness;self.model_asset_root=parent.model_asset_root
        self.chat_init_lock=threading.Lock()
        from .metadata_api import MetadataAPI
        class GuardedMetadata(MetadataAPI):
            def store(api,catalog=None):
                library.require_profile(primary)
                return super().store(catalog)
            def ensure_people_ready(api):
                library.require_profile(primary)
                return super().ensure_people_ready()
            def _loop(api):
                # Publish through the same backend and index architecture only while identity matches.
                while not api.stop.is_set():
                    api.changed.wait(2)
                    if api.stop.is_set():break
                    api.changed.clear()
                    try:
                        library.require_profile(primary)
                        if not (api.server.project/'_DIT_NOTES/project.sqlite3').is_file():continue
                        with api.store() as store:pending=store.edit_revision>store.indexed_edit_revision
                        if not pending:api.error=None;continue
                        if api.stop.wait(.3):break
                        library.require_profile(primary)
                        from .enrichment import publish_index
                        publish_index(api.server.project,api.server.search_backend,projection_only=True)
                        api.error=None;library.refresh(primary)
                    except Exception as error:
                        api.error=str(error)
                        if api.stop.wait(3):break
        self.metadata=GuardedMetadata(self)


def primary_context(library,primary,parent):
    library.require_profile(primary)
    with library.lock:
        context=library.contexts.get(primary)
        if context is None:
            context=_PrimaryContext(library,primary,parent);library.contexts[primary]=context
        # A correct volume re-mounted elsewhere must be deliberately refreshed in the profile registry.
        if context.project!=library.require_profile(primary):raise ValueError('Primary path changed; reopen the library')
        return context
