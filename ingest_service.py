#!/usr/bin/env python3
"""Persistent OffShoot post-processing coordinator. No card or media mutations."""
from __future__ import annotations
import argparse,fcntl,hashlib,json,os,signal,sqlite3,subprocess,sys,threading,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
os.environ.update(PATH=str(ROOT/'bin')+':/usr/bin:/bin:/usr/sbin:/sbin',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',PYTHONNOUSERSITE='1')
from ditnotes.atomic import atomic_json,fsync_dir
from ditnotes.project_retry import retry_project,is_project_busy
from ditnotes.native_progress import ProgressState,open_progress
from ditnotes.ingest_events import enqueue,validate_event,mac_volume_info,RejectedEvent

def config():
    c=json.loads((ROOT/'config.json').read_text())
    mount=Path(c.get('volume_mount',ROOT.parent)).expanduser().absolute()
    project=Path(c.get('ingest_root',mount/c['ingest_folder'])).expanduser().absolute()
    if project==mount or not project.is_relative_to(mount):raise ValueError('Ingest folder must be inside the configured volume')
    c.update(volume_mount=str(mount),ingest_root=str(project),queue_db=str(ROOT/'runtime/queue.sqlite3'))
    return c

def verify_volume(c):
    mount=Path(c['volume_mount'])
    if not mount.is_dir():raise RuntimeError('Configured ingest volume is not mounted')
    info=mac_volume_info(mount)
    if str(info.get('VolumeUUID','')).upper()!=c['volume_uuid'].upper() or Path(info.get('MountPoint','')).resolve()!=mount.resolve():raise RuntimeError('Configured ingest volume is not mounted')
    return info

def connect(c):
    from ditnotes.sqlite_safety import require_safe_sqlite
    require_safe_sqlite()
    db=sqlite3.connect(c['queue_db'],timeout=15);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT \'pending\',payload TEXT NOT NULL,enqueued_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,claimed_at REAL,finished_at REAL)')
    return db

def ingest_event(event):
    c=config();verify_volume(c)
    digest=hashlib.sha256(json.dumps(event,sort_keys=True).encode()).hexdigest()
    atomic_json(ROOT/'runtime/events'/f'{digest}.json',event)
    try:
        job,added=enqueue(event,c);return {'status':'queued' if added else 'duplicate','job_id':job['job_id']}
    except RejectedEvent as e:return {'status':'ignored','reason':str(e)}

def start():
    c=config();verify_volume(c);(ROOT/'runtime').mkdir(exist_ok=True)
    open_progress(ROOT,c['port'],ingest_root=c['ingest_root'])
    with (ROOT/'runtime/service.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'running','url':f'http://127.0.0.1:{c["port"]}/'}
        fcntl.flock(lock,fcntl.LOCK_UN)
    log=(ROOT/'runtime/service.log').open('ab')
    child=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(Path(__file__).resolve()),'run'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    # Callback launcher holds its deployment read lock until a daemon holds the service lock.
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        with (ROOT/'runtime/service.lock').open('a+') as guard:
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:break
        if child.poll() is not None:raise RuntimeError('AI service failed to start; inspect runtime/service.log')
        time.sleep(.05)
    else:
        child.terminate()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.kill();child.wait()
        raise RuntimeError('AI service did not acquire its lock; no release switch is safe until inspected')
    return {'status':'starting','url':f'http://127.0.0.1:{c["port"]}/'}

from ditnotes.catalog_index import Snapshots

class Controller:
    def __init__(self,c,server,stop):
        self.c=c;self.server=server;self.stop=stop;self.project=Path(c['ingest_root']);self.lock=threading.RLock();self.phase='starting';self.active=None;self.error=None;self.unverified=[];self.engine=None;self.native_progress=ProgressState()
    def status(self):
        with connect(self.c) as db:
            pending=db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('pending','processing')").fetchone()[0]
            errors=db.execute("SELECT COUNT(*) FROM jobs WHERE state='error'").fetchone()[0]
            latest=db.execute("SELECT last_error FROM jobs WHERE state='error' ORDER BY claimed_at DESC LIMIT 1").fetchone()
        with self.lock:return {**self.native_progress.snapshot(),'phase':self.phase,'pending':pending,'active':self.active,'last_error':self.error or (latest[0] if latest else None),'errors':errors,'needs_verification':self.unverified}
    def publish(self,phase=None,active=None,error=None):
        with self.lock:
            if phase:self.phase=phase
            self.active=active;self.error=error
            atomic_json(ROOT/'runtime/status.json',dict(self.status(),updated_at=time.time()))
    def snapshot(self):
        from ditnotes.enrichment import publish_index
        path=self.project/'_DIT_NOTES/catalog.json'
        if path.exists():return publish_index(self.project,self.server.search_backend)
    def reconcile(self):
        from ditnotes.library import reconcile
        verify_volume(self.c)
        from ditnotes.offshoot_receipts import recover
        recover(self.c,ROOT)
        for event in sorted((ROOT/'runtime/events').glob('*.json')):
            try:enqueue(json.loads(event.read_text()),self.c)
            except (RejectedEvent,ValueError,OSError):pass
        with connect(self.c) as db:
            rows=list(db.execute('SELECT * FROM jobs'))
            db.execute("UPDATE jobs SET state='pending',claimed_at=NULL WHERE state='processing'")
        scopes=[];remaining={};valid_ids=set()
        for row in rows:
            try:
                job=validate_event(json.loads(row['payload'])['event'],self.c)
                scopes.append(Path(job['destination']));valid_ids.add(row['job_id'])
            except Exception as e:
                with connect(self.c) as db:db.execute("UPDATE jobs SET state='error',last_error=? WHERE job_id=?",(str(e),row['job_id']))
        if scopes:
            result=self.project_call(lambda:reconcile(self.project,input_paths=scopes,models_root=ROOT/'models'))
            for relative in result['pending']:
                for row in rows:
                    if row['job_id'] not in valid_ids:continue
                    destination=Path(json.loads(row['payload'])['destination'])
                    if (self.project/relative).is_relative_to(destination):remaining[row['job_id']]=True
            self.snapshot()
            with connect(self.c) as db:
                for row in rows:
                    if row['job_id'] not in valid_ids:continue
                    if row['job_id'] in remaining:db.execute("UPDATE jobs SET state='pending',last_error=NULL WHERE job_id=?",(row['job_id'],))
                    else:db.execute("UPDATE jobs SET state='done',last_error=NULL,finished_at=? WHERE job_id=?",(time.time(),row['job_id']))
        # Unknown wrappers are never silently treated as verified or lost from view.
        known=set(scopes);unknown=[]
        for day in self.project.iterdir():
            if day.is_dir() and not day.is_symlink() and len(day.name)==8 and day.name.isdigit():
                unknown.extend(str(p.relative_to(self.project)) for p in day.iterdir() if p.is_dir() and not p.is_symlink() and p not in known)
        self.unverified=sorted(unknown)
        if unknown:self.error=f'{len(unknown)} ingest folder(s) need OffShoot verification before AI processing'
    def waiting_for_project(self):
        self.native_progress.update(stage='waiting_for_project')
        self.publish('waiting_for_project',self.native_progress.snapshot().get('current'))
    def project_call(self,call):
        return retry_project(call,stop_event=self.stop,on_wait=self.waiting_for_project)
    def reconcile_until_ready(self):
        while not self.stop.is_set():
            try:self.reconcile();return
            except Exception as error:
                if not is_project_busy(error):raise
                self.waiting_for_project();self.stop.wait(1)
        raise KeyboardInterrupt('Processing interrupted; resume next session')
    def requeue(self,job_id):
        with connect(self.c) as db:
            db.execute("UPDATE jobs SET state='pending',last_error=NULL,claimed_at=NULL,finished_at=NULL WHERE job_id=?",(job_id,))
    def progress_event(self,event):
        if self.stop.is_set():raise KeyboardInterrupt('Processing interrupted; resume next session')
        self.native_progress.update(**event)
        self.publish('processing',self.native_progress.snapshot().get('current'))
    def progress(self,path,status):
        if self.stop.is_set():raise KeyboardInterrupt('Processing interrupted; resume next session')
        self.publish('processing',path)
    def loop(self):
        from ditnotes.engine import Engine
        from ditnotes.library import process,_failure_text
        self.engine=Engine(models_root=ROOT/'models')
        from concurrent.futures import ThreadPoolExecutor
        warmer=ThreadPoolExecutor(max_workers=1,thread_name_prefix='analysis-startup')
        warming=warmer.submit(self.engine.prewarm)
        try:
            self.native_progress.begin();self.native_progress.update(stage='reconciling')
            self.reconcile_until_ready();self.native_progress.update(stage='idle',current=None);self.publish('ready',error=self.error)
            # Spawn model loaders without waiting; preparation/copying can proceed.
            # Representative model forwards run in background while reconciliation proceeds.
            last_receipt_scan=0.0
            while not self.stop.is_set():
                if time.monotonic()-last_receipt_scan>5:
                    from ditnotes.offshoot_receipts import recover
                    recover(self.c,ROOT);last_receipt_scan=time.monotonic()
                request=ROOT/'runtime/reconcile.request'
                if request.exists():
                    self.publish('starting');self.reconcile_until_ready();request.unlink();fsync_dir(request.parent);self.publish('ready',error=self.error)
                with connect(self.c) as db:
                    db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY enqueued_at LIMIT 1").fetchone()
                    if row:db.execute("UPDATE jobs SET state='processing',attempts=attempts+1,claimed_at=? WHERE job_id=?",(time.time(),row['job_id']))
                if not row:self.stop.wait(1);continue
                awake=None
                try:
                    job=validate_event(json.loads(row['payload'])['event'],self.c)
                    self.native_progress.update(stage='warming',current=None)
                    self.publish('warming')
                    warmup=warming.result(timeout=300)
                    atomic_json(ROOT/'runtime/analysis-warmup.json',warmup)
                    self.native_progress.begin(str(Path(job['destination']).relative_to(self.project)))
                    self.publish('processing',str(Path(job['destination']).relative_to(self.project)))
                    awake=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    result=self.project_call(lambda:process(job['destination'],self.project,models_root=ROOT/'models',engine=self.engine,progress=self.progress,progress_event=self.progress_event))
                    if result['counts']['failed']:
                        message=f'{result["counts"]["failed"]} media file(s) failed; completed files are retained'
                        scope=Path(job['destination']).relative_to(self.project).as_posix()
                        for failure in result.get('failures',[]):
                            if not isinstance(failure,dict) or failure.get('migrated_legacy_error'):continue
                            path=failure.get('path')
                            if not isinstance(path,str) or Path(path).is_absolute() or '..' in Path(path).parts:continue
                            if not (scope=='.' or path==scope or path.startswith(scope+'/')):continue
                            if not failure.get('error'):continue
                            message+='; '+_failure_text(f"{path} [{failure.get('stage','processing')}]: {failure['error']}",self.project)
                            break
                        raise RuntimeError(message)
                    self.progress_event({'stage':'indexing','current':None})
                    self.snapshot()
                    self.native_progress.update(stage='complete',current=None)
                    with connect(self.c) as db:db.execute("UPDATE jobs SET state='done',last_error=NULL,finished_at=? WHERE job_id=?",(time.time(),row['job_id']))
                    self.unverified=[p for p in self.unverified if p!=str(Path(job['destination']).relative_to(self.project))]
                    self.publish('ready')
                except KeyboardInterrupt:
                    self.requeue(row['job_id'])
                    raise
                except Exception as e:
                    if is_project_busy(e):
                        self.requeue(row['job_id']);self.waiting_for_project();self.stop.wait(1)
                        continue
                    with connect(self.c) as db:db.execute("UPDATE jobs SET state='error',last_error=? WHERE job_id=?",(str(e),row['job_id']))
                    self.native_progress.update(stage='failed',current=self.native_progress.snapshot().get('current'))
                    self.publish('error',error=str(e))
                finally:
                    if awake:awake.terminate();awake.wait(timeout=2)
        except BaseException as e:
            if not self.stop.is_set():self.publish('error',error=str(e))
        finally:
            warmer.shutdown(wait=True,cancel_futures=True)
            self.engine.close()

def run():
    from ditnotes.server import create_server
    c=config();verify_volume(c);project=Path(c['ingest_root']);project.mkdir(parents=True,exist_ok=True)
    lock=(ROOT/'runtime/service.lock').open('a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    try:
        with connect(c) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise sqlite3.DatabaseError('Queue integrity check failed')
    except sqlite3.DatabaseError:
        quarantine=ROOT/'runtime/quarantine'/str(uuid.uuid4());quarantine.mkdir(parents=True)
        for suffix in ('','-wal','-shm'):
            path=Path(c['queue_db']+suffix)
            if path.exists():path.replace(quarantine/path.name)
        fsync_dir(quarantine);fsync_dir(ROOT/'runtime')
        with connect(c):pass
    atomic_json(ROOT/'runtime/service.json',{'pid':os.getpid(),'root':str(ROOT),'port':c['port'],'started_at':time.time()})
    stop=threading.Event();server=create_server(project,c['port'],ROOT);snapshot=Snapshots(project);server.catalog=snapshot;server.search_vectors=snapshot.vectors
    ctl=Controller(c,server,stop);server.get_status=ctl.status
    def shutdown(*_):stop.set();threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    worker=threading.Thread(target=ctl.loop,name='ingest-worker',daemon=True);worker.start()
    try:server.serve_forever(poll_interval=.25)
    finally:
        stop.set()
        # Keep service.lock until every AI writer and its executors have stopped.
        # The stop command bounds this wait and kills this process group if needed.
        try:
            worker.join();server.server_close()
            with connect(c) as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            atomic_json(ROOT/'runtime/stopped.json',{'stopped_at':time.time()})
        finally:lock.close()

def _stopped_or_unknown():
    """Missing/stale PID evidence must not override a live service lock."""
    try:
        with (ROOT/'runtime/service.lock').open('a+') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('AI service lock is held but its PID cannot be verified; status is unknown, not stopped') from None
    except FileNotFoundError:pass
    return {'status':'stopped'}

def stop_service():
    try:state=json.loads((ROOT/'runtime/service.json').read_text());pid=int(state['pid'])
    except (OSError,ValueError):return _stopped_or_unknown()
    check=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
    if str(ROOT) not in check.stdout or 'ingest_service.py run' not in check.stdout:return _stopped_or_unknown()
    os.killpg(pid,signal.SIGTERM)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        with (ROOT/'runtime/service.lock').open('a+') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return {'status':'stopped'}
            except BlockingIOError:pass
        time.sleep(.1)
    try:os.killpg(pid,signal.SIGKILL)
    except ProcessLookupError:pass
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with (ROOT/'runtime/service.lock').open('a+') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);return {'status':'stopped','incomplete_work':'will resume at next start'}
            except BlockingIOError:pass
        time.sleep(.05)
    raise RuntimeError('AI service still owns its lock after termination; refusing to report stopped')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['start','run','stop','event','reconcile','recover']);parser.add_argument('payload',nargs='?');a=parser.parse_args()
    if a.command=='run':run();return
    if a.command=='event':result=ingest_event(json.loads(a.payload));start()
    elif a.command=='recover':
        from ditnotes.offshoot_receipts import recover
        c=config();verify_volume(c)
        with connect(c):pass
        result=recover(c,ROOT);start()
    elif a.command=='start':result=start()
    elif a.command=='stop':result=stop_service()
    else:atomic_json(ROOT/'runtime/reconcile.request',{'requested_at':time.time()});result=start()
    print(json.dumps(result))
if __name__=='__main__':main()
