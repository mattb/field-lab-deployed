#!/usr/bin/env python3
"""Install/update orchestration. Code preparation never changes active ingest settings."""
from __future__ import annotations
import argparse,json,os,shlex,sqlite3,subprocess,sys,time,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import manager
from configure import atomic_json,machine_config,save_config
SOURCE=Path(__file__).resolve().parents[1]

def run(command,**kwargs):
    print('+ '+shlex.join(map(str,command)),file=sys.stderr,flush=True)
    return subprocess.run(list(map(str,command)),check=True,**kwargs)

def current(home):
    state=manager.status(home)
    if not state['current']:raise ValueError('No active release; prepare and activate one first')
    return Path(state['current'])

def service(release,command):
    return run([release/'.venv/bin/python',release/'ingest_service.py',command])

def save_launcher(home):
    # Hooks resolve the current interpreter at invocation, never a resolved release path.
    path=home/'run'
    if path.is_symlink():raise ValueError('Refusing symlink launcher')
    text='#!/bin/zsh\nset -eu\nFIELD_HOME='+shlex.quote(str(home))+'\nexec "$FIELD_HOME/current/.venv/bin/python" "$FIELD_HOME/current/deploy/launcher.py" --home "$FIELD_HOME" "$@"\n'
    tmp=home/('.run-'+uuid.uuid4().hex);tmp.write_text(text);tmp.chmod(0o755);os.replace(tmp,path);manager.sync_dir(home)

def backup_state(home,release):
    """SQLite backup API includes committed WAL data; no raw copying of live DBs."""
    target=home/'shared/runtime/update-backups'/str(time.time_ns());target.mkdir(parents=True)
    config=home/'shared/config.json'
    if config.is_file():atomic_json(target/'config.json',json.loads(config.read_text()))
    paths=[home/'shared/runtime/queue.sqlite3']
    if config.is_file():
        project=Path(json.loads(config.read_text())['ingest_root'])
        if not project.is_dir():raise ValueError('Configured project is unavailable; cannot take pre-update metadata backup')
        paths+=[project/'_DIT_NOTES/project.sqlite3']
        for name in ['project.json','state.json','catalog.json']:
            p=project/'_DIT_NOTES'/name
            if p.is_file():atomic_json(target/name,json.loads(p.read_text()))
    inventory=[]
    for i,p in enumerate(paths):
        if not p.exists():continue
        dest=target/(str(i)+'-'+p.name)
        with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(dest) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Metadata backup integrity check failed')
        with dest.open('rb') as stream:os.fsync(stream.fileno())
        inventory.append({'source':str(p),'backup':str(dest),'sha256':manager.digest(dest)})
    atomic_json(target/'manifest.json',{'created_at':time.time(),'release':str(release),'databases':inventory})
    return target

def prepare(args):
    source=Path(args.source).expanduser().resolve() if args.source else SOURCE
    candidate=manager.stage(source,args.home);release=Path(candidate['release'])
    state=manager.status(args.home)
    if state['current']==str(release):
        run([release/'.venv/bin/python',release/'deploy/bootstrap.py','--release',release,'--check'])
    else:run(['/bin/bash',release/'deploy/bootstrap.sh','--release',release]+(['--offline'] if args.offline else []))
    manager.mark_ready(args.home,candidate['release_id'])
    return candidate

def switch(args,release_id=None,rollback=False):
    if not args.offshoot_idle:raise ValueError('Switching requires --offshoot-idle after confirming OffShoot has no active or queued transfers')
    home=Path(args.home).expanduser().absolute();state=manager.status(home)
    release=Path(state['previous']) if rollback and state['previous'] else home/'releases'/str(release_id)
    if not release.is_dir():raise ValueError('Requested release is unavailable')
    # Full fresh integrity/readiness check before stopping any working service.
    run([release/'.venv/bin/python',release/'deploy/bootstrap.py','--release',release,'--check'])
    manager.mark_ready(home,release.name)
    old=Path(state['current']) if state['current'] else None
    # Refuse busy AI work rather than surprising a field operator.
    status=home/'shared/runtime/status.json'
    if old and status.is_file():
        value=json.loads(status.read_text())
        if value.get('phase') not in ('ready','stopped','failed','error') and not args.interrupt_ai:
            raise ValueError('AI is busy; wait or use --interrupt-ai to stop safely and reconcile later')
    if old:service(old,'stop')
    backup=backup_state(home,old) if old else None
    result=manager.activate(home,release.name,rollback=rollback)
    save_launcher(home)
    # Starting is a separate explicit step: deployment does not mutate app settings or project schemas.
    return {**result,'backup':str(backup) if backup else None,'service':'stopped; run start after checking configuration'}

def doctor(home):
    state=manager.status(home);release=Path(state['current']) if state['current'] else None
    result={'deployment':state,'configured':(home/'shared/config.json').is_file(),'ccc_configured_by_installer':False}
    if release:
        manager.validate_shared(release,home/'shared');manager.verify_bootstrap(release)
        run([release/'.venv/bin/python',release/'deploy/bootstrap.py','--release',release,'--check'])
        result['offline']=manager.offline_check(release)
        if result['configured']:
            run([release/'.venv/bin/python','-c','from ingest_service import config,verify_volume; verify_volume(config())'],cwd=release)
    result['ready']=bool(release and result.get('offline',{}).get('ready'))
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for name in ['install','update','activate','rollback','status','doctor','configure','configure-offshoot','start','stop','reconcile','ccc-status','reindex','verify-offshoot','desktop','backup']:
        q=sub.add_parser(name);q.add_argument('--home',type=Path,default=manager.DEFAULT_HOME)
        if name in ['install','update']:q.add_argument('--source',type=Path);q.add_argument('--activate',action='store_true');q.add_argument('--offline',action='store_true')
        if name in ['install','update','activate','rollback']:q.add_argument('--offshoot-idle',action='store_true');q.add_argument('--interrupt-ai',action='store_true')
        if name=='activate':q.add_argument('--release',required=True)
        if name=='desktop':q.add_argument('--desktop',type=Path)
        if name=='reindex':q.add_argument('--project',type=Path,required=True);q.add_argument('--run',action='store_true');q.add_argument('--offshoot-idle',action='store_true')
        if name=='configure':q.add_argument('--volume',required=True);q.add_argument('--ingest-folder',default='Ingests');q.add_argument('--port',type=int,default=8766)
        if name=='configure-offshoot':q.add_argument('--offshoot-idle',action='store_true')
        if name=='backup':q.add_argument('backup_args',nargs=argparse.REMAINDER)
    a=p.parse_args();a.home=a.home.expanduser().resolve()
    try:
        if a.command in ['install','update']:
            result=prepare(a)
            if a.activate:result=switch(a,result['release_id'])
        elif a.command in ['activate','rollback']:result=switch(a,getattr(a,'release',None),a.command=='rollback')
        elif a.command=='status':result=manager.status(a.home)
        elif a.command=='doctor':result=doctor(a.home)
        elif a.command=='configure':
            with manager.locked(a.home):result=save_config(a.home,machine_config(a.volume,a.ingest_folder,a.port))
        elif a.command=='configure-offshoot':
            if not a.offshoot_idle:raise ValueError('Confirm OffShoot has no active or queued transfers using --offshoot-idle')
            release=current(a.home);save_launcher(a.home)
            run([release/'.venv/bin/python',release/'setup_workflow.py','--home',a.home,'--offshoot-idle']);result={'configured':True,'ccc':False}
        elif a.command=='reindex':
            release=current(a.home)
            if a.run and not a.offshoot_idle:raise ValueError('Reindex requires OffShoot idle and AI service stopped')
            command=[release/'.venv/bin/python',release/'deploy/reindex.py','--project',a.project]
            if a.run:command.append('--run')
            with manager.locked(a.home) as (_,_,shared):
                if a.run:
                    with manager.stopped_service(shared):run(command)
                else:run(command)
            result={'reindex':'complete' if a.run else 'plan','service':'stopped'}
        elif a.command=='desktop':
            from desktop import install
            current(a.home)
            with manager.locked(a.home):save_launcher(a.home)
            result=install(a.home,a.desktop)
        elif a.command=='verify-offshoot':
            release=current(a.home);run([release/'.venv/bin/python',release/'deploy/offshoot_status.py']);result={'changes_made':False}
        elif a.command=='ccc-status':
            release=current(a.home);run([release/'.venv/bin/python',release/'deploy/ccc.py']);result={'changed':False}
        elif a.command=='backup':
            release=current(a.home)
            # Optional rotation starts no task until run/button. Task definitions
            # are never changed; explicit per-project registration is separate.
            run([release/'.venv/bin/python',release/'deploy/backup.py',*a.backup_args])
            result={'command':'backup'}
        else:
            current(a.home);save_launcher(a.home)
            run([a.home/'run',a.command]);result={'command':a.command}
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
