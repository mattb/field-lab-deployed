#!/usr/bin/env python3
"""Read-only update guards; private receipts never configure OffShoot or CCC.

Capture before deploying and check after activation/Desktop refresh, before
starting the service. Existing single-primary installs need no registry migration.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.manager import atomic_json
from deploy.backup_shortcut import inventory

FILES=('config.json','shoot.json','runtime/backup-shortcut/current.plist')

def _shared(home):
    home=Path(home).expanduser().absolute()
    if any(p.is_symlink() for p in (home,*home.parents)):raise ValueError('Application home cannot use symlinks')
    shared=home/'shared'
    if not shared.is_dir() or shared.is_symlink():raise ValueError('Existing managed shared directory required')
    return shared

def snapshot(home,runner=None):
    shared=_shared(home);files={}
    for relative in FILES:
        path=shared/relative
        if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Protected update state uses a symlink')
        if path.exists():
            if not path.is_file():raise ValueError('Protected update state is not a regular file')
            files[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
        else:files[relative]=None
    if files['config.json'] is None:raise ValueError('Existing installation configuration required')
    config=json.loads((shared/'config.json').read_text())
    if not all(config.get(k) for k in ('volume_uuid','volume_mount','ingest_root','ingest_folder')):
        raise ValueError('Existing ingest identity is incomplete; preserve it for reconciliation')
    tasks=inventory(runner) if runner is not None else inventory()
    return {'files':files,'ccc_tasks':tasks,'ingest':{k:config[k] for k in ('volume_uuid','volume_mount','ingest_root','ingest_folder')}}

def _receipt_root(home):
    shared=_shared(home);root=shared/'runtime/update-preservation'
    for path in (shared/'runtime',root):
        if path.is_symlink():raise ValueError('Receipt storage is a symlink')
        path.mkdir(exist_ok=True,mode=0o700)
    return root

def capture(home,runner=None):
    before=snapshot(home,runner);identity=uuid.uuid4().hex
    root=_receipt_root(home)
    record={'schema':1,'id':identity,'home':str(_shared(home).parent),'created_at':time.time(),'before':before,'status':'captured'}
    atomic_json(root/(identity+'.json'),record)
    return {'id':identity,'receipt':str(root/(identity+'.json')),'status':'captured','ingest':before['ingest'],'task_count':len(before['ccc_tasks'])}

def check(home,identity,runner=None):
    if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):raise ValueError('Invalid preservation receipt ID')
    path=_receipt_root(home)/(identity+'.json')
    if path.is_symlink():raise ValueError('Receipt is a symlink')
    record=json.loads(path.read_text())
    if record.get('schema')!=1 or record.get('id')!=identity or record.get('home')!=str(_shared(home).parent):raise ValueError('Preservation receipt belongs to another installation')
    observed=snapshot(home,runner)
    if observed!=record['before']:
        # Keep original evidence and report drift; never repair settings silently.
        record.setdefault('history',[]).append({'checked_at':time.time(),'status':'drift','observed':observed})
        atomic_json(path,record)
        raise ValueError('Ingest identity, backup binding, primary registry or CCC inventory changed; stop and reconcile without overwriting settings')
    record.update(status='preserved',checked_at=time.time())
    atomic_json(path,record)
    return {'status':'preserved','id':identity,'ingest':observed['ingest'],'task_count':len(observed['ccc_tasks']),'ccc_settings_changed':False,'cutover_performed':False,'registry_initialized':False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,required=True)
    p.add_argument('action',choices=('capture','check'));p.add_argument('--id');a=p.parse_args()
    try:
        if a.action=='check' and not a.id:raise ValueError('Capture receipt ID required')
        result=capture(a.home) if a.action=='capture' else check(a.home,a.id)
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as exc:
        p.exit(1,str(exc)+'\n')
if __name__=='__main__':main()
