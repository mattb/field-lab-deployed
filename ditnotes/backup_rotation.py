"""Opt-in CCC rotation. No CCC task creation/configuration or source-media writes.

APFS clone projections give each preconfigured task a fixed source. JSON commits
are durable, operations serialized, and unknown CCC attempts reconcile by hashes.
"""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from .atomic import atomic_json, fsync_dir, fsync_tree

SCHEMA=1

def state_digest(value):
    return hashlib.sha256(json.dumps({k:v for k,v in value.items() if k!='state_sha256'},sort_keys=True,separators=(',',':')).encode()).hexdigest()

def publish_json(path,value):
    """Replace through an existing directory FD; never recreate a lost mount."""
    path=safe(path,exists=False);parent=safe(path.parent)
    fd=os.open(parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0))
    temporary='.'+path.name+'.'+uuid.uuid4().hex+'.tmp'
    try:
        out=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=fd)
        with os.fdopen(out,'w') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path.name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try:os.unlink(temporary,dir_fd=fd)
        except FileNotFoundError:pass
        os.close(fd)

def mkdir_chain(path,anchor):
    """Create descendants through an already-open directory, never a mount path."""
    path=safe(path,exists=False);anchor=safe(anchor)
    relative=path.relative_to(anchor)
    if anchor==Path('/Volumes'):raise ValueError('Expected volume is not mounted')
    fd=os.open(anchor,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0))
    try:
        for name in relative.parts:
            try:os.mkdir(name,mode=0o700,dir_fd=fd);os.fsync(fd)
            except FileExistsError:pass
            child=os.open(name,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0),dir_fd=fd)
            os.close(fd);fd=child
    finally:os.close(fd)


def safe(path, *, exists=True):
    p=Path(path).expanduser().absolute()
    if '..' in p.parts:raise ValueError('Path traversal refused')
    for parent in (p,*p.parents):
        if parent.is_symlink():raise ValueError('Symlink path refused: '+str(parent))
    if exists and not p.exists():raise ValueError('Path unavailable: '+str(p))
    return p


def digest(path):
    p=safe(path)
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def inventory(root):
    root=safe(root);result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            p=safe(Path(base)/name)
            if not p.is_dir() and not p.is_file():raise ValueError('Non-regular source entry')
        for name in files:
            p=Path(base)/name;s=p.stat()
            result[p.relative_to(root).as_posix()]={'bytes':s.st_size,'sha256':digest(p)}
    return result


def check_inventory(root, expected, *, exact=False):
    root=safe(root)
    if exact and set(inventory(root))!=set(expected):raise ValueError('Source inventory changed')
    for relative,item in expected.items():
        p=safe(root/relative)
        if not p.is_relative_to(root) or not p.is_file():raise ValueError('Unsafe manifest entry')
        if p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']:
            raise ValueError('Checksum mismatch: '+relative)


def clonefile(source,destination):
    """APFS COW clone, never a hardlink or a silent full-copy fallback."""
    if sys.platform!='darwin':raise RuntimeError('APFS cloning requires macOS')
    lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
    fn=lib.clonefile;fn.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int];fn.restype=ctypes.c_int
    if fn(os.fsencode(source),os.fsencode(destination),0):
        err=ctypes.get_errno();raise OSError(err,os.strerror(err),str(source))


class MacBackend:
    cli=Path('/Applications/Carbon Copy Cloner.app/Contents/MacOS/ccc')
    def identity(self,path):
        from .backup_volumes import mac_identity
        return mac_identity(path)
    def free(self,path):return shutil.disk_usage(path).free
    def capacity(self,path):return shutil.disk_usage(path).total
    def idle(self):
        p=subprocess.run([str(self.cli),'--no-running-tasks'],capture_output=True,text=True,timeout=15)
        return p.returncode==0
    def run(self,task_uuid):
        # Do not stop any task on timeout/interruption; CCC owns the copy.
        p=subprocess.run([str(self.cli),'--start='+task_uuid,'--watch'],stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,timeout=24*3600)
        if p.returncode:raise RuntimeError('CCC reported an unsuccessful or unknown task result')


class Manager:
    def __init__(self,state_dir,backend=None,clone=None,checkpoint=None):
        self.root=safe(state_dir);self.path=self.root/'state.json'
        self.backend=backend or MacBackend();self.clone=clone or clonefile
        self.checkpoint=checkpoint or (lambda stage:None)
        self._load()

    @classmethod
    def initialize(cls,state_dir,project,spool,backend=None,clone=None,reserve_fraction=.10):
        backend=backend or MacBackend();project=safe(project);spool=safe(spool,exists=False)
        state_dir=safe(state_dir,exists=False)
        if not 0<=reserve_fraction<.5:raise ValueError('Reserve must be between zero and 50%')
        if spool.is_relative_to(project) or project.is_relative_to(spool):raise ValueError('Spool must be outside project')
        if state_dir.is_relative_to(spool):raise ValueError('State must be outside spool')
        if state_dir.is_relative_to(project) and state_dir!=project/'_DIT_NOTES/backup-rotation':
            raise ValueError('Use the default backup-rotation state folder or a location outside the project')
        parent=spool
        while not parent.exists():parent=parent.parent
        primary=backend.identity(project);other=backend.identity(parent)
        if primary['uuid']!=other['uuid'] or primary.get('filesystem','').lower()!='apfs':
            raise ValueError('Spool and project require the same APFS volume')
        state_anchor=project if state_dir.is_relative_to(project) else state_dir
        while not state_anchor.exists():state_anchor=state_anchor.parent
        mkdir_chain(state_dir,state_anchor)
        with safe(state_dir/'rotation.lock',exists=False).open('a+') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Backup initialization is already running') from None
            if (state_dir/'state.json').exists():
                existing=cls(state_dir,backend=backend,clone=clone)
                if existing.state['project']!=str(project) or existing.state['spool']!=str(spool):
                    raise ValueError('Existing backup configuration differs; refusing reset')
                return existing
            if any(p.name!='rotation.lock' for p in state_dir.iterdir()):raise ValueError('State folder contains incomplete or foreign data; inspect before initialization')
            if spool.exists() and any(spool.iterdir()):raise ValueError('Spool folder must be empty')
            mkdir_chain(spool,parent)
            value={'schema':SCHEMA,'project':str(project),'spool':str(spool),'primary':primary,
                   'reserve_fraction':reserve_fraction,'sets':[],'jobs':[],'message':'Register backup drives before copying',
                   'project_id':uuid.uuid4().hex,'created_at':time.time(),'revision':0}
            value['state_sha256']=state_digest(value)
            publish_json(state_dir/'state.json',value)
            return cls(state_dir,backend=backend,clone=clone)

    def _load(self,assign=True):
        safe(self.path);state=json.loads(self.path.read_text())
        if state.get('state_sha256')!=state_digest(state):raise ValueError('Backup state checksum failed; preserve it for recovery')
        if not isinstance(state.get('revision'),int) or state['revision']<0:raise ValueError('Damaged backup revision')
        if state.get('schema')!=SCHEMA or not isinstance(state.get('sets'),list) or not isinstance(state.get('jobs'),list):
            raise ValueError('Unsupported or damaged backup state; preserve it for recovery')
        for field in ('project','spool'):
            if not isinstance(state.get(field),str) or not Path(state[field]).is_absolute():raise ValueError('Damaged backup path')
            safe(state[field],exists=False)
        labels=set()
        for entry in state['sets']:
            if not re.fullmatch(r'B[0-9]{3,}',entry.get('label','')) or entry['label'] in labels:raise ValueError('Damaged backup set')
            labels.add(entry['label']);uuid.UUID(entry['task_uuid'])
            rel=Path(entry['destination_relative'])
            if rel.is_absolute() or '..' in rel.parts or not rel.parts:raise ValueError('Damaged destination path')
        for job in state['jobs']:
            rel=Path(job['relative'])
            if rel.is_absolute() or '..' in rel.parts or not rel.parts:raise ValueError('Damaged card path')
            if job['set'] is not None and job['set'] not in labels:raise ValueError('Unknown assigned set')
            if job['state'] not in ('pending','assigned','copying','verified'):raise ValueError('Damaged backup phase')
            for name,item in job['manifest'].items():
                r=Path(name)
                if r.is_absolute() or '..' in r.parts or not r.parts or not isinstance(item['bytes'],int) or item['bytes']<0 or not re.fullmatch('[a-f0-9]{64}',item['sha256']):
                    raise ValueError('Damaged file manifest')
            if job['bytes']!=sum(v['bytes'] for v in job['manifest'].values()):raise ValueError('Damaged reservation')
        if assign:self.state=state
        return state

    def _save(self):
        self.state['revision']+=1;self.state['updated_at']=time.time()
        self.state['state_sha256']=state_digest(self.state)
        publish_json(self.path,self.state);self.checkpoint('committed')

    @contextmanager
    def locked(self):
        path=safe(self.root/'rotation.lock',exists=False)
        with path.open('a+') as stream:
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Backup manager is already working') from None
            try:self._load();yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

    def _primary(self):
        p=safe(self.state['project']);expected=self.state['primary'];got=self.backend.identity(p)
        if got['uuid']!=expected['uuid'] or got['mount']!=expected['mount']:raise RuntimeError('Primary volume identity changed')
        spool=safe(self.state['spool']);info=self.backend.identity(spool)
        if info['uuid']!=got['uuid']:raise RuntimeError('Spool volume identity changed')
        return p,spool,got

    def _disk(self,entry):
        mount=safe(entry['mount']);got=self.backend.identity(mount)
        if got['uuid']!=entry['uuid'] or got['mount']!=entry['mount']:raise RuntimeError('Wrong or disconnected backup drive: '+entry['label'])
        _,_,primary=self._primary()
        if not got.get('physical') or not primary.get('physical') or set(got['physical'])&set(primary['physical']):
            raise RuntimeError('Backup must be on an independent physical device')
        dest=safe(mount/entry['destination_relative'],exists=False)
        if not dest.is_relative_to(mount) or dest==mount:raise ValueError('Dedicated destination subfolder required')
        if dest.exists() and self.backend.identity(dest)['uuid']!=entry['uuid']:raise RuntimeError('Nested destination volume refused')
        return dest

    def prepare_set(self,label):
        """Create a durable-owned empty CCC source before task UI setup."""
        if not re.fullmatch(r'B[0-9]{3,}',label):raise ValueError('Use a label such as B001')
        with self.locked():
            _,spool,_=self._primary();payload=safe(spool/label/'payload',exists=False)
            prepared=self.state.setdefault('prepared_sources',[])
            if any(e['label']==label for e in self.state['sets']):raise ValueError('Set already registered')
            if label not in prepared:
                if payload.exists():raise ValueError('Unmanaged source folder already exists')
                prepared.append(label);self._save()
            payload.mkdir(parents=True,exist_ok=True);fsync_dir(payload.parent)
            if any(payload.iterdir()):raise ValueError('Prepared source is not empty')
            return {'label':label,'source':str(payload),'ccc_settings_changed':False}

    def add_set(self,label,mount,destination_relative,task_uuid,dedicated_task_reviewed=False,guard_path=None):
        if not dedicated_task_reviewed:raise ValueError('A new dedicated CCC task must be reviewed explicitly')
        if not re.fullmatch(r'B[0-9]{3,}',label):raise ValueError('Use a label such as B001')
        task_uuid=str(uuid.UUID(task_uuid));relative=Path(destination_relative)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:raise ValueError('Unsafe destination path')
        with self.locked():
            _,spool,_=self._primary();mount=safe(mount);info=self.backend.identity(mount)
            if str(mount)!=info['mount']:raise ValueError('Register the actual volume mountpoint')
            if any(e['label']==label or e['uuid']==info['uuid'] or e['task_uuid']==task_uuid for e in self.state['sets']):
                raise ValueError('Drive, label or task already registered')
            entry={'label':label,'mount':str(mount),'uuid':info['uuid'],'physical':info['physical'],
                   'destination_relative':str(relative),'task_uuid':task_uuid,'closed':False,'reviewed_at':time.time(),
                   'guard_path':str(Path(guard_path).absolute()) if guard_path else None}
            dest=self._disk(entry)
            if dest.exists() and any(dest.iterdir()):raise ValueError('New dedicated destination must be empty')
            payload=safe(spool/label/'payload',exists=False)
            if payload.exists() and (label not in self.state.get('prepared_sources',[]) or any(payload.iterdir())):
                raise ValueError('Unregistered spool already exists; inspect interrupted registration')
            # Commit ownership before creating source directories. A crash can
            # then rebuild an absent empty payload, without adopting foreign data.
            self.state['sets'].append(entry);self.state['message']='Ready to back up pending footage';self._save()
            payload.mkdir(parents=True,exist_ok=True);fsync_dir(payload.parent)
            return {'source':str(payload),'destination':str(dest),'task_uuid':task_uuid}

    def register_card(self,card_path,evidence_id):
        """Internal discovery API: caller must supply trusted accepted-receipt ID."""
        if not isinstance(evidence_id,str) or not evidence_id:raise ValueError('Verified ingest evidence required')
        with self.locked():
            project,_,_=self._primary();card=safe(card_path)
            if card==project or not card.is_relative_to(project) or '_DIT_NOTES' in card.relative_to(project).parts:
                raise ValueError('Expected a dedicated card folder inside project')
            relative=card.relative_to(project).as_posix()
            existing=next((j for j in self.state['jobs'] if j['evidence_id']==evidence_id),None)
            if existing:
                if existing['relative']!=relative:raise ValueError('Receipt destination changed')
                return existing
            if any(relative==j['relative'] or relative.startswith(j['relative']+'/') or j['relative'].startswith(relative+'/') for j in self.state['jobs']):
                raise ValueError('Overlapping card scopes refused')
            manifest=inventory(card)
            if not manifest:raise ValueError('Empty card folder')
            job={'id':uuid.uuid4().hex,'evidence_id':evidence_id,'relative':relative,'manifest':manifest,
                 'bytes':sum(v['bytes'] for v in manifest.values()),'state':'pending','set':None,'error':None}
            self.state['jobs'].append(job);self._save();return job

    def discover_queue(self,queue_db):
        """Only accepted, receipt-backed queue entries; never scan unverified folders."""
        queue=safe(queue_db)
        with sqlite3.connect(queue.as_uri()+'?mode=ro',uri=True) as conn:
            rows=conn.execute('SELECT payload FROM jobs ORDER BY enqueued_at,job_id').fetchall()
        count=0
        for row in rows:
            p=json.loads(row[0])
            # New receipt adapter marks recovered verified receipt evidence explicitly.
            event=p.get('event',{})
            if p.get('ingest_root')!=self.state['project'] or p.get('volume_uuid','').upper()!=self.state['primary']['uuid'].upper():continue
            if p.get('verification_mode')!='Source & Destination' or not p.get('job_id'):continue
            if event.get('FileCopyCompleted_state')!='Success':continue
            self.register_card(p['destination'],p['job_id']);count+=1
        return count

    def _allocate(self,job,metadata_bytes=0):
        if job['set']:return next(e for e in self.state['sets'] if e['label']==job['set'])
        for entry in self.state['sets']:
            if entry['closed']:continue
            try:dest=self._disk(entry)
            except (OSError,ValueError,RuntimeError):
                self.state['message']='Connect backup drive '+entry['label'];self._save();return None
            outstanding=sum(j['bytes'] for j in self.state['jobs'] if j['set']==entry['label'] and j['state']!='verified')
            capacity=self.backend.capacity(entry['mount'])
            reserve=math.ceil(capacity*self.state['reserve_fraction'])
            # A card too large for this drive is not evidence that an otherwise
            # empty/useful drive is full. Preserve it for smaller future cards.
            if job['bytes']+metadata_bytes>capacity-reserve:continue
            # No subtraction for partial destination data: conservative retry accounting.
            if self.backend.free(entry['mount'])-reserve>=outstanding+job['bytes']+metadata_bytes:
                job['set']=entry['label'];job['state']='assigned';self._save();self.checkpoint('assigned');return entry
            entry['closed']=True;self._save()
        self.state['message']='Connect and register the next backup drive';self._save();return None

    def _guard(self,entry):
        # Injected test backends cannot accidentally invoke installed CCC.
        if not isinstance(self.backend,MacBackend):return
        from .backup_guard import generate_guard, validate_installed
        _,spool,_=self._primary()
        expected=generate_guard(str(spool/entry['label']/'payload'),str(Path(entry['mount'])/entry['destination_relative']),
                                self.state['primary']['uuid'],entry['uuid'])
        if not entry.get('guard_path'):raise RuntimeError('Install and register the secured CCC preflight guard before running')
        validate_installed(entry['guard_path'],expected)

    def _metadata(self):
        from .backup_metadata import snapshot
        project,spool,_=self._primary()
        if not (project/'_DIT_NOTES').is_dir():return None
        target=spool/('metadata-'+uuid.uuid4().hex)
        snapshot(project,target,self.clone)
        manifest=inventory(target)
        fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
        stable=safe(spool/('metadata-sealed-'+fingerprint),exists=False)
        if stable.exists():
            check_inventory(stable,manifest,exact=True)
            # This randomly named target was created by this invocation only.
            shutil.rmtree(target);fsync_dir(spool)
        else:os.rename(target,stable);fsync_dir(spool)
        return {'source':str(stable),'manifest':manifest,'revision':fingerprint,
                'bytes':sum(v['bytes'] for v in manifest.values())}

    def _stage_metadata(self,entry,metadata):
        if not metadata:return
        _,spool,_=self._primary();payload=spool/entry['label']/'payload'
        relative='FieldLabMetadata/'+metadata['revision']+'/_DIT_NOTES'
        target=safe(payload/relative,exists=False)
        if not target.exists():
            # Snapshot is already sealed/immutable. Clone into a private staging
            # directory; publish only after complete independent hash validation.
            staging=spool/entry['label']/('metadata-stage-'+uuid.uuid4().hex)
            staging.mkdir()
            for name in metadata['manifest']:
                dst=safe(staging/name,exists=False);dst.parent.mkdir(parents=True,exist_ok=True)
                self.clone(safe(Path(metadata['source'])/name),dst)
            check_inventory(staging,metadata['manifest'],exact=True);fsync_tree(staging)
            target.parent.mkdir(parents=True,exist_ok=True);os.rename(staging,target);fsync_dir(target.parent)
        check_inventory(target,metadata['manifest'],exact=True)
        entry['metadata_pending']={'relative':relative,'revision':metadata['revision'],'manifest':metadata['manifest']}
        self._save()

    def _verify_metadata(self,entry):
        item=entry.get('metadata_pending')
        if item:
            dest=self._disk(entry);check_inventory(dest/item['relative'],item['manifest'],exact=True)
            entry['metadata_revision']=item['revision'];entry.pop('metadata_pending');self._save()

    def _stage(self,job,entry):
        project,spool,_=self._primary();source=safe(project/job['relative'])
        check_inventory(source,job['manifest'],exact=True)
        payload=safe(spool/entry['label']/'payload',exists=False)
        payload.mkdir(parents=True,exist_ok=True)
        target=safe(payload/job['relative'],exists=False)
        if target.exists():check_inventory(target,job['manifest'],exact=True);return
        staging=safe(spool/entry['label']/('stage-'+job['id']),exists=False)
        if staging.exists():
            # Resume a partial clone without deleting or trusting old data.
            for base,dirs,files in os.walk(staging):
                for n in dirs+files:safe(Path(base)/n)
        else:staging.mkdir()
        for relative,item in job['manifest'].items():
            dst=safe(staging/relative,exists=False);dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists():
                if dst.stat().st_size!=item['bytes'] or digest(dst)!=item['sha256']:raise ValueError('Damaged staged clone; preserve for diagnosis')
            else:self.clone(safe(source/relative),dst)
        check_inventory(staging,job['manifest'],exact=True);check_inventory(source,job['manifest'],exact=True)
        fsync_tree(staging);target.parent.mkdir(parents=True,exist_ok=True)
        os.rename(staging,target);fsync_dir(target.parent);self.checkpoint('staged')

    def _verify(self,job,entry):
        dest=self._disk(entry);check_inventory(dest/job['relative'],job['manifest'],exact=True)
        # Verify original too: new/changed originals cannot inherit an old success.
        project,_,_=self._primary();check_inventory(project/job['relative'],job['manifest'],exact=True)
        self._disk(entry)

    def _space_before_copy(self,entry,job=None):
        dest=self._disk(entry);needed=0
        items=[]
        if job:items.append((job['relative'],job['manifest']))
        if entry.get('metadata_pending'):
            item=entry['metadata_pending'];items.append((item['relative'],item['manifest']))
        for root,manifest in items:
            for relative,item in manifest.items():
                p=safe(dest/root/relative,exists=False)
                if not p.is_file() or p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']:needed+=item['bytes']
        reserve=math.ceil(self.backend.capacity(entry['mount'])*self.state['reserve_fraction'])
        if self.backend.free(entry['mount'])-reserve<needed:
            raise RuntimeError('Backup space changed; keep this assignment pending and free space or reconnect its drive')

    def _mirror_ledger(self,entry):
        self._disk(entry)
        ledger=safe(Path(entry['mount'])/('.FieldLab-'+self.state['project_id']+'-'+entry['label']),exists=False)
        ledger.mkdir(exist_ok=True)
        publish_json(safe(ledger/'ledger.json',exists=False),self.state)

    def status(self):
        value=self._load(assign=False)
        return {'message':value['message'],'sets':[{'id':e['label'],'state':'Closed' if e['closed'] else 'Available',
                    'bytes':sum(j['bytes'] for j in value['jobs'] if j['set']==e['label']),
                    'message':('Metadata pending' if e.get('metadata_pending') else ('Metadata '+e['metadata_revision'][:10] if e.get('metadata_revision') else 'No metadata snapshot'))} for e in value['sets']],
                'jobs':[{'id':j['id'],'label':j['relative'],'state':j['state'],'bytes':j['bytes'],
                         'message':j.get('set') or 'Awaiting allocation','error':j.get('error')} for j in value['jobs']]}

    def run_pending(self):
        with self.locked():
            self._primary()
            if not self.backend.idle():raise RuntimeError('CCC is busy or its status is unavailable; wait and retry')
            self.state['message']='Backing up pending footage';self._save()
            metadata=self._metadata()
            for job in self.state['jobs']:
                if job['state']=='verified':continue
                entry=self._allocate(job,metadata['bytes'] if metadata else 0)
                if not entry:return self.status()
                try:
                    self._disk(entry)
                    if job['state']=='copying':
                        # CCC may have survived the old manager. Never relaunch before idle.
                        if not self.backend.idle():raise RuntimeError('CCC is still running')
                        try:self._verify(job,entry)
                        except (ValueError,OSError):pass
                        else:
                            job['state']='verified';job['error']=None;job['verified_at']=time.time();self._save();self._mirror_ledger(entry);continue
                    self._stage(job,entry);self._stage_metadata(entry,metadata)
                    if not self.backend.idle():raise RuntimeError('CCC is busy; retry later')
                    self._disk(entry);self._guard(entry);self._space_before_copy(entry,job)
                    job['state']='copying';job['error']=None;job['attempt']=uuid.uuid4().hex;self._save();self.checkpoint('before_ccc')
                    self.backend.run(entry['task_uuid'])
                    if not self.backend.idle():raise RuntimeError('CCC completion is unknown; retry later')
                    self._verify(job,entry);self._verify_metadata(entry);self.checkpoint('verified_bytes')
                    job['state']='verified';job['error']=None;job['verified_at']=time.time();self._save();self._mirror_ledger(entry)
                except Exception as exc:
                    job['error']=str(exc);self.state['message']=str(exc);self._save();raise
            for entry in self.state['sets']:
                try:self._disk(entry)
                except (OSError,ValueError,RuntimeError):continue
                if metadata and any(j['set']==entry['label'] for j in self.state['jobs']) and entry.get('metadata_revision')!=metadata['revision']:
                    reserve=math.ceil(self.backend.capacity(entry['mount'])*self.state['reserve_fraction'])
                    if self.backend.free(entry['mount'])-reserve<metadata['bytes']:
                        self.state['message']='Metadata awaiting space on '+entry['label'];self._save();return self.status()
                    self._stage_metadata(entry,metadata);self._guard(entry)
                    if not self.backend.idle():raise RuntimeError('CCC is busy; retry later')
                    self._space_before_copy(entry)
                    self.backend.run(entry['task_uuid'])
                    if not self.backend.idle():raise RuntimeError('CCC completion unknown')
                    self._verify_metadata(entry)
                self._mirror_ledger(entry)
            self.state['message']='All registered footage backed up';self._save()
            return self.status()
