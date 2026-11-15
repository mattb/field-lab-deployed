#!/usr/bin/env python3
"""A native Backup shortcut with a stable current CCC task binding.

Only bind changes the private pointer. Install does not start CCC or copy data.
The action uses published CCC --uuids/--start/--watch, never task configuration.
"""
import argparse
import math
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import uuid
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from deploy.desktop import compile_app

CCC=Path('/Applications/Carbon Copy Cloner.app')

def inventory(runner=subprocess.run):
    result=runner([str(CCC/'Contents/MacOS/ccc'),'--uuids'],capture_output=True,text=True,check=True,timeout=15)
    rows={}
    for line in result.stdout.splitlines():
        match=re.fullmatch(r'([A-Fa-f0-9-]{36}): (.+)',line)
        if match:rows[str(uuid.UUID(match[1])).upper()]=match[2]
    return rows


def private_dir(home):
    home=Path(home).expanduser().absolute()
    if home.is_symlink() or any(p.is_symlink() for p in home.parents):raise ValueError('Application home must not use symlinks')
    root=home/'shared/runtime/backup-shortcut'
    for p in [home,home/'shared',home/'shared/runtime',root]:
        if p.is_symlink():raise ValueError('Managed directory is a symlink')
        p.mkdir(exist_ok=True,parents=True)
    return root


def bind(home,task_uuid,mode,runner=subprocess.run,*,primary_id=None,set_label=None,registry=None):
    if mode != 'start':raise ValueError('Backup starts the current task')
    task=str(uuid.UUID(task_uuid)).upper();rows=inventory(runner)
    if task not in rows:raise ValueError('CCC task is absent; pointer unchanged')
    root=private_dir(home);path=root/'current.plist'
    profile=None;entry=None
    if (Path(home)/'shared/shoot.json').is_file():
        from ditnotes.shoot_registry import ShootRegistry
        registry=registry or ShootRegistry(home);state=registry.read()
        primary_id=primary_id or state['active_primary'];profile=state['profiles'][primary_id]
        label=set_label or profile['backup']['current_set']
        entry=profile['backup']['sets'].get(label)
        if not entry or entry.get('needs_review') or entry['task_uuid']!=task:raise ValueError('Task is not the reviewed current backup set for this primary')
        registry.set_current_backup(primary_id,label)
        permitted={state['active_primary']}
        if state.get('pending_cutover'):permitted.add(state['pending_cutover']['to_primary'])
        if primary_id not in permitted:
            return {'bound':True,'primary_id':primary_id,'task_uuid':task,'default_changed':False,'ccc_settings_changed':False}
    if path.is_symlink():raise ValueError('Current binding is a symlink')
    value={'schema':1,'task_uuid':task,'task_name':rows[task],'mode':mode}
    if profile:value.update(primary_id=primary_id,source_mount=profile['volume_mount'],source_uuid=profile['volume_uuid'],destination_mount=entry['destination_mount'],destination_uuid=entry['destination_uuid'])
    fd,name=tempfile.mkstemp(prefix='.current-',dir=root)
    try:
        with os.fdopen(fd,'wb') as stream:plistlib.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        descriptor=os.open(root,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        if os.path.exists(name):os.unlink(name)
    return {'bound':True,'task_uuid':task,'task_name':rows[task],'mode':mode,'ccc_settings_changed':False}


def script(root):
    pointer=str(Path(root)/'current.plist');cli=str(CCC/'Contents/MacOS/ccc')
    # Read the binding once into memory; a later rotation affects the next click.
    return '''use framework "Foundation"
use scripting additions
try
    set pointerPath to POINTER
    set binding to current application's NSDictionary's dictionaryWithContentsOfFile:pointerPath
    if binding is missing value then error "No current backup task is configured. Use the Field Lab backup skill to select it."
    set registryPath to REGISTRY
    set registryState to do shell script "if [ -f " & quoted form of registryPath & " ]; then /usr/bin/plutil -extract pending_cutover json -o - " & quoted form of registryPath & "; else printf null; fi"
    if registryState is not "null" then error "Primary cutover is pending. Reconcile it before starting a backup."
    set taskID to (binding's objectForKey:"task_uuid") as text
    set taskName to (binding's objectForKey:"task_name") as text
    set actionMode to (binding's objectForKey:"mode") as text
    set validTask to do shell script CLI & " --uuids | /usr/bin/awk -F ': ' -v wanted=" & quoted form of taskID & " 'toupper($1)==wanted {print $1}'"
    if validTask is not taskID then error "The current CCC backup task is missing. No backup was started."
    set hasRegistry to (current application's NSFileManager's defaultManager()'s fileExistsAtPath:registryPath) as boolean
    if hasRegistry then
        set primaryID to binding's objectForKey:"primary_id"
        if primaryID is missing value then error "Backup needs a primary-specific task binding. Run the cutover/backup skill."
        do shell script RUNNER & " backup-current"
        display notification "CCC finished. Review its result before releasing source cards." with title "Backup"
    else if actionMode is "start" then
        do shell script CLI & " --no-running-tasks"
        do shell script "/usr/bin/open -a '/Applications/Carbon Copy Cloner.app'"
        do shell script CLI & " --start=" & quoted form of taskID & " --watch"
        display notification "CCC finished. Review its result before releasing source cards." with title "Backup"
    else
        error "Unknown Backup action. No backup was started."
    end if
on error errorMessage number errorNumber
    display alert "Backup" message errorMessage as critical buttons {"OK"} default button "OK"
end try
'''.replace('POINTER',json.dumps(pointer)).replace('REGISTRY',json.dumps(str(Path(root).parents[1]/'shoot.json'))).replace('RUNNER',json.dumps("'"+str(Path(root).parents[2]/'run').replace("'","'\\''")+"'")).replace('CLI',json.dumps("'"+cli.replace("'","'\\''")+"'"))


def install(home,desktop,compiler=compile_app,checkpoint=lambda _:None):
    root=private_dir(home);desktop=Path(desktop).expanduser().resolve(strict=True)
    app=desktop/'Backup.app';owned=root/'ownership.json'
    from deploy.manager import atomic_json, safe_relative, digest, sync_dir
    with (root/'install.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if owned.is_symlink():raise ValueError('Ownership record is a symlink')
        record=json.loads(owned.read_text()) if owned.exists() else {'schema':1,'desktops':{}}
        # Adopt only a previously recorded link from the first shortcut version.
        if 'shortcut' in record:
            record={'schema':1,'desktops':{record['shortcut']:[record['target']]}}
        if record.get('schema')!=1 or not isinstance(record.get('desktops'),dict):raise ValueError('Invalid ownership record')
        targets=record['desktops'].get(str(app),[])
        if not isinstance(targets,list):raise ValueError('Invalid ownership entry')
        if app.exists() or app.is_symlink():
            if not app.is_symlink() or os.readlink(app) not in targets:raise ValueError('Backup.app already exists and is not managed')
        text=script(root)
        fingerprint=hashlib.sha256((text+digest(Path(__file__))).encode()).hexdigest()
        generation=root/('app-'+fingerprint);target=generation/'Backup.app'
        if generation.exists():
            if generation.is_symlink():raise ValueError('App generation is a symlink')
            manifest=json.loads((generation/'manifest.json').read_text())
            if manifest.get('fingerprint')!=fingerprint or not manifest.get('files'):raise ValueError('Incomplete shortcut generation')
            for name,expected in manifest['files'].items():
                file=safe_relative(generation,name)
                if not file.is_file() or digest(file)!=expected:raise ValueError('Shortcut integrity check failed')
        else:
            building=Path(tempfile.mkdtemp(prefix='.building-',dir=root))
            source=building/'backup.applescript';source.write_text(text)
            compiler(source,building/'Backup.app','Backup','backup','GenericHardDiskIcon.icns')
            files={str(file.relative_to(building)):digest(file) for file in building.rglob('*') if file.is_file()}
            if not files:raise ValueError('Compiler produced no shortcut files')
            atomic_json(building/'manifest.json',{'fingerprint':fingerprint,'files':files})
            for file in building.rglob('*'):
                if file.is_file():
                    with file.open('rb') as stream:os.fsync(stream.fileno())
            for directory,_,_ in os.walk(building,topdown=False):sync_dir(directory)
            os.rename(building,generation);sync_dir(root)
        # Retain old and proposed targets before switching so interruption is resumable.
        record['desktops'][str(app)]=list(dict.fromkeys(targets+[str(target)]))
        atomic_json(owned,record);checkpoint('ownership_saved')
        if app.exists() or app.is_symlink():
            if not app.is_symlink() or os.readlink(app) not in record['desktops'][str(app)]:raise ValueError('Desktop item changed during install')
        temporary=desktop/('.backup-link-'+uuid.uuid4().hex)
        temporary.symlink_to(target,target_is_directory=True)
        try:os.replace(temporary,app);sync_dir(desktop)
        finally:
            if temporary.is_symlink():temporary.unlink()
        return {'shortcut':str(app),'installed':True,'backup_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['install','bind','status','run-current','run','reconcile-attempt']);p.add_argument('--home',type=Path,default=Path.home()/'Library/Application Support/Field Lab');p.add_argument('--desktop',type=Path,default=Path.home()/'Desktop');p.add_argument('--task-uuid');p.add_argument('--primary');p.add_argument('--set-label');p.add_argument('--readback',type=Path);p.add_argument('--validation-intent');p.add_argument('--mode',choices=['start'])
    a=p.parse_args();a.home=a.home.expanduser().absolute()
    try:
        if a.action=='install':result=install(a.home,a.desktop)
        elif a.action=='bind':
            if not a.task_uuid or not a.mode:raise ValueError('bind requires --task-uuid and --mode')
            result=bind(a.home,a.task_uuid,a.mode,primary_id=a.primary,set_label=a.set_label)
        elif a.action=='run-current':result=run_current_task(a.home)
        elif a.action in ('run','reconcile-attempt'):
            if not a.primary or not a.set_label:raise ValueError('Primary and set label are required')
            if a.action=='run':result=run_primary_task(a.home,a.primary,a.set_label,validation_intent=a.validation_intent)
            else:
                if not a.readback:raise ValueError('Fresh task history readback is required')
                result=reconcile_primary_attempt(a.home,a.primary,a.set_label,json.loads(a.readback.read_text()))
        else:
            path=a.home/'shared/runtime/backup-shortcut/current.plist'
            result={'bound':False} if not path.exists() else plistlib.loads(path.read_bytes())
        print(json.dumps(result))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:p.exit(1,str(e)+'\n')



def _attempt_path(home,primary_id,label):
    if not re.fullmatch(r'[a-f0-9]{32}',primary_id) or not re.fullmatch(r'B\d{3,}',label):raise ValueError('Invalid primary/set identity')
    root=private_dir(home)/'attempts';root.mkdir(exist_ok=True)
    if root.is_symlink():raise ValueError('Attempts directory is a symlink')
    return root/(primary_id+'-'+label+'.json')


def run_primary_task(home,primary_id,label,*,runner=subprocess.run,registry=None,checkpoint=lambda _:None,validation_intent=None):
    """Explicit user action; CCC copies. Unknown attempts never auto-relaunch."""
    from ditnotes.shoot_registry import ShootRegistry
    from deploy.manager import atomic_json
    registry=registry or ShootRegistry(home);state=registry.read()
    pending=state.get('pending_cutover')
    if pending and (validation_intent!=pending['id'] or primary_id!=pending['to_primary'] or 'backup_bound' not in pending['steps']):raise ValueError('Primary cutover pending; no backup started')
    profile=state['profiles'][primary_id];entry=profile['backup']['sets'].get(label)
    if not entry or entry.get('needs_review'):raise ValueError('Backup set needs supported-UI configuration review')
    if entry['source']!=profile['project_root']:raise ValueError('Backup source is not this primary project')
    source=registry.profile_identity(primary_id)
    destination=registry.probe(Path(entry['destination_mount']))
    if str(destination.get('uuid','')).upper()!=entry['destination_uuid'] or Path(destination.get('mount',''))!=Path(entry['destination_mount']):raise ValueError('Backup destination UUID/mount mismatch')
    if set(source['physical']).intersection(destination.get('physical',[])):raise ValueError('Backup must be physically independent')
    if entry['task_uuid'] not in inventory(runner):raise ValueError('Registered CCC task is missing')
    path=_attempt_path(home,primary_id,label)
    root=path.parent
    descriptor=os.open(root/'run.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(descriptor,'a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('A Field Lab backup invocation is already running') from None
        fresh_state=registry.read()
        fresh_pending=fresh_state.get('pending_cutover')
        fresh_profile=fresh_state['profiles'][primary_id]
        if fresh_profile['backup']['sets'].get(label)!=entry or fresh_profile['project_root']!=profile['project_root']:
            raise ValueError('Backup configuration changed; review before starting')
        source=registry.profile_identity(primary_id)
        destination=registry.probe(Path(entry['destination_mount']))
        if str(destination.get('uuid','')).upper()!=entry['destination_uuid'] or Path(destination.get('mount',''))!=Path(entry['destination_mount']):
            raise ValueError('Backup destination changed before starting')
        if not destination.get('physical') or set(source['physical']).intersection(destination['physical']):
            raise ValueError('Backup must be physically independent')
        if fresh_pending and (validation_intent!=fresh_pending['id'] or primary_id!=fresh_pending['to_primary'] or 'backup_bound' not in fresh_pending['steps']):raise ValueError('Primary cutover began; no backup started')
        runner([str(CCC/'Contents/MacOS/ccc'),'--no-running-tasks'],check=True,capture_output=True,text=True,timeout=15)
        if path.is_symlink():raise ValueError('Attempt record is a symlink')
        previous=json.loads(path.read_text()) if path.exists() else {}
        if previous.get('state') in ('launching','running','unknown'):
            raise RuntimeError('Prior backup outcome is unknown; reconcile CCC task history before another start')
        attempt={'schema':1,'primary_id':primary_id,'set_label':label,'task_uuid':entry['task_uuid'],'attempt_id':uuid.uuid4().hex,'created_at':time.time(),'state':'launching','backup_verified':False,'previous_attempt':previous or None}
        atomic_json(path,attempt);checkpoint('before_ccc_start')
        try:
            # The CLI's exit is completion evidence only; it is not a hash/restore receipt.
            result=runner([str(CCC/'Contents/MacOS/ccc'),'--start='+entry['task_uuid'],'--watch'],check=True,capture_output=True,text=True)
            checkpoint('ccc_returned')
        except BaseException as exc:
            attempt.update(state='unknown',error=str(exc),updated_at=time.time());atomic_json(path,attempt)
            raise
        attempt.update(state='ccc_finished',updated_at=time.time(),ccc_exit_code=result.returncode)
        atomic_json(path,attempt)
        return attempt


def reconcile_primary_attempt(home,primary_id,label,evidence,*,runner=subprocess.run):
    from deploy.manager import atomic_json
    path=_attempt_path(home,primary_id,label)
    descriptor=os.open(path.parent/'run.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(descriptor,'a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Backup invocation is still active') from None
        runner([str(CCC/'Contents/MacOS/ccc'),'--no-running-tasks'],check=True,capture_output=True,text=True,timeout=15)
        if path.is_symlink():raise ValueError('Attempt record is a symlink')
        attempt=json.loads(path.read_text())
        stamp=evidence.get('observed_at')
        if evidence.get('method')!='supported-ui-history' or evidence.get('task_uuid')!=attempt['task_uuid'] or evidence.get('result') not in ('completed','failed','cancelled','not-started') or type(stamp) not in (int,float) or not math.isfinite(stamp) or not attempt['created_at']<=stamp<=time.time()+5:
            raise ValueError('Fresh matching CCC history readback is required')
        attempt.update(state='reconciled',reconciliation=evidence,updated_at=time.time())
        atomic_json(path,attempt)
        return attempt


def run_current_task(home,**kwargs):
    from ditnotes.shoot_registry import ShootRegistry
    registry=kwargs.get('registry') or ShootRegistry(home);state=registry.read()
    if state.get('pending_cutover'):raise ValueError('Primary cutover pending')
    path=private_dir(home)/'current.plist'
    if path.is_symlink():raise ValueError('Backup pointer is a symlink')
    pointer=plistlib.loads(path.read_bytes());identity=state['active_primary']
    profile=state['profiles'][identity];label=profile['backup']['current_set']
    entry=profile['backup']['sets'].get(label)
    if not entry or pointer.get('primary_id')!=identity or pointer.get('task_uuid')!=entry['task_uuid'] or pointer.get('mode')!='start':raise ValueError('Backup pointer and active primary differ; reconcile cutover')
    return run_primary_task(home,identity,label,**kwargs)

if __name__=='__main__':main()
