#!/usr/bin/env python3
"""A native Backup shortcut with a stable current CCC task binding.

Only bind changes the private pointer. Install does not start CCC or copy data.
The action uses published CCC --uuids/--start/--watch, never task configuration.
"""
import argparse
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


def bind(home,task_uuid,mode,runner=subprocess.run):
    if mode != 'start':raise ValueError('Backup starts the current task')
    task=str(uuid.UUID(task_uuid)).upper();rows=inventory(runner)
    if task not in rows:raise ValueError('CCC task is absent; pointer unchanged')
    root=private_dir(home);path=root/'current.plist'
    if path.is_symlink():raise ValueError('Current binding is a symlink')
    value={'schema':1,'task_uuid':task,'task_name':rows[task],'mode':mode}
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
    set taskID to (binding's objectForKey:"task_uuid") as text
    set taskName to (binding's objectForKey:"task_name") as text
    set actionMode to (binding's objectForKey:"mode") as text
    set validTask to do shell script CLI & " --uuids | /usr/bin/awk -F ': ' -v wanted=" & quoted form of taskID & " 'toupper($1)==wanted {print $1}'"
    if validTask is not taskID then error "The current CCC backup task is missing. No backup was started."
    if actionMode is "start" then
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
'''.replace('POINTER',json.dumps(pointer)).replace('CLI',json.dumps("'"+cli.replace("'","'\\''")+"'"))


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
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['install','bind','status']);p.add_argument('--home',type=Path,default=Path.home()/'Library/Application Support/Field Lab');p.add_argument('--desktop',type=Path,default=Path.home()/'Desktop');p.add_argument('--task-uuid');p.add_argument('--mode',choices=['start'])
    a=p.parse_args();a.home=a.home.expanduser().absolute()
    try:
        if a.action=='install':result=install(a.home,a.desktop)
        elif a.action=='bind':
            if not a.task_uuid or not a.mode:raise ValueError('bind requires --task-uuid and --mode')
            result=bind(a.home,a.task_uuid,a.mode)
        else:
            path=a.home/'shared/runtime/backup-shortcut/current.plist'
            result={'bound':False} if not path.exists() else plistlib.loads(path.read_bytes())
        print(json.dumps(result))
    except (OSError,ValueError,subprocess.SubprocessError) as e:p.exit(1,str(e)+'\n')

if __name__=='__main__':main()
