#!/usr/bin/env python3
"""Recoverable primary cutover through supported OffShoot and CCC interfaces.

Never copies media or edits CCC task settings. Setup/acceptance of target's CCC
pair is separate, through supported UI. No service restarts until completion.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy import manager
from deploy.backup_shortcut import CCC,bind,private_dir
from ditnotes.atomic import atomic_json
from ditnotes.shoot_registry import ShootRegistry,RESERVED_CONFIG


class Cutover:
    def __init__(self,home,*,registry=None,runner=subprocess.run,checkpoint=lambda _:None):
        self.home=Path(home).expanduser().resolve(strict=True)
        self.registry=registry or ShootRegistry(self.home);self.runner=runner;self.checkpoint=checkpoint
        self.config_path=self.home/'shared/config.json'

    @contextmanager
    def operation(self):
        with manager.locked(self.home) as (_,_,shared):
            with manager.stopped_service(shared):
                root=private_dir(self.home)/'attempts';root.mkdir(exist_ok=True)
                if root.is_symlink():raise ValueError('Backup invocation directory is a symlink')
                descriptor=os.open(root/'run.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(descriptor,'a+') as lock:
                    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:raise RuntimeError('A backup invocation is active; wait for CCC') from None
                    self.runner([str(CCC/'Contents/MacOS/ccc'),'--no-running-tasks'],capture_output=True,text=True,check=True,timeout=15)
                    yield

    def _config(self):
        if self.config_path.is_symlink():raise ValueError('Shared configuration is a symlink')
        return json.loads(self.config_path.read_text())

    def _record(self,stage,**extra):
        pending=self.registry.read()['pending_cutover']
        return self.registry.record_cutover(stage,{'kind':'readback','observed_at':time.time(),'primary_id':pending['to_primary'],**extra})

    def prepare(self,target,*,offshoot_idle=False,cache_factory=None):
        if not offshoot_idle:raise ValueError('Observe OffShoot idle before preparing cutover')
        with self.operation():
            state=self.registry.read()
            if state['active_primary']==target and not state.get('pending_cutover'):return {'already_active':True}
            # Cache the old library before disconnecting it. Failure leaves it active.
            if not state.get('pending_cutover') and cache_factory:
                cache_factory(self.home/'shared').refresh(state['active_primary'])
            elif not state.get('pending_cutover') and (Path(state['profiles'][state['active_primary']]['project_root'])/'_DIT_NOTES/catalog.json').is_file():
                from ditnotes.multivolume import MultiVolumeLibrary
                library=MultiVolumeLibrary(self.home/'shared',probe=self.registry.probe)
                try:library.refresh(state['active_primary'])
                finally:library.close()
            pending=self.registry.begin_cutover(target,self._config())
            if 'quiescent' not in pending['steps']:self._record('quiescent',offshoot_idle_observed=True,service_lock_observed_stopped=True)
            profile=self.registry.read()['profiles'][target];self.registry.profile_identity(target)
            root=Path(profile['project_root']);mount=Path(profile['volume_mount'])
            # The profile already validated its relative root. Recheck each ancestor.
            current=mount
            for part in Path(profile['ingest_folder']).parts:
                current=current/part
                if current.is_symlink():raise ValueError('Target ingest path is a symlink')
                current.mkdir(exist_ok=True)
            self.registry.profile_identity(target)
            if 'destination_prepared' not in self.registry.read()['pending_cutover']['steps']:self._record('destination_prepared',volume_uuid=profile['volume_uuid'],project_root=str(root))
            self.checkpoint('prepared')
            return self.registry.read()

    def apply(self,*,offshoot_idle=False):
        if not offshoot_idle:raise ValueError('Observe OffShoot idle before applying cutover')
        with self.operation():
            state=self.registry.read();pending=state.get('pending_cutover')
            if not pending:raise ValueError('No pending cutover')
            if 'accepted' in pending['steps']:raise ValueError('Cutover already accepted; finish instead of reconfiguring')
            if 'destination_prepared' not in pending['steps']:raise ValueError('Prepare cutover first')
            self.registry.profile_identity(pending['to_primary'])
            actual=self._config();target=self.registry.target_config(actual)
            before=pending['before_config']
            if any(actual.get(key) not in (before.get(key),target.get(key)) for key in RESERVED_CONFIG):raise ValueError('Unexpected configuration; preserve it and reconcile cutover')
            atomic_json(self.config_path,target);self.checkpoint('field_config_written')
            release=manager.pointer(self.home,'current')
            if release is None:raise ValueError('Activate a managed application release first')
            python=release/'.venv/bin/python'
            setup_started=time.time()
            self.runner([str(python),str(release/'setup_workflow.py'),'--home',str(self.home),'--offshoot-idle'],check=True,capture_output=True,text=True,timeout=180)
            self.checkpoint('offshoot_api_returned')
            receipt=json.loads((self.home/'shared/runtime/setup-receipt.json').read_text())
            stamp=receipt.get('configured_at')
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or not setup_started<=stamp<=time.time()+5:
                raise ValueError('Fresh OffShoot setup receipt required')
            if any(receipt.get(key)!=target.get(key) for key in ('volume_uuid','ingest_root')):raise ValueError('OffShoot setup receipt does not match target')
            result=self.runner([str(python),str(release/'deploy/offshoot_status.py')],check=True,capture_output=True,text=True,timeout=30)
            readback=json.loads(result.stdout)
            if not readback.get('settings_verified'):raise ValueError('OffShoot settings/preset readback failed')
            if 'offshoot_configured' not in self.registry.read()['pending_cutover']['steps']:
                self._record('offshoot_configured',volume_uuid=target['volume_uuid'],project_root=target['ingest_root'],api_acknowledged=True,settings_readback=readback,dropzone_acceptance_pending=True)
            if 'field_configured' not in self.registry.read()['pending_cutover']['steps']:
                self._record('field_configured',volume_uuid=target['volume_uuid'],project_root=target['ingest_root'])
            profile=self.registry.read()['profiles'][pending['to_primary']]
            label=profile['backup']['current_set'];entry=profile['backup']['sets'].get(label)
            if not entry or entry.get('needs_review'):raise ValueError('Configure/register target CCC backup set through supported UI before binding')
            bind(self.home,entry['task_uuid'],'start',self.runner,primary_id=profile['id'],set_label=label,registry=self.registry)
            self.checkpoint('backup_pointer_written')
            if 'backup_bound' not in self.registry.read()['pending_cutover']['steps']:
                self._record('backup_bound',task_uuid=entry['task_uuid'],set_label=label)
            return {'pending_cutover':self.registry.read()['pending_cutover'],'acceptance_required':True,'service_started':False}

    def validate(self):
        state=self.registry.read();pending=state.get('pending_cutover')
        if not pending or 'backup_bound' not in pending['steps']:raise ValueError('Apply cutover before its validation run')
        release=manager.pointer(self.home,'current')
        if release is None:raise ValueError('No active application release')
        self.runner([str(release/'.venv/bin/python'),str(release/'ingest_service.py'),'start','--cutover-intent',pending['id']],check=True,capture_output=True,text=True,timeout=45)
        return {'validation_intent':pending['id'],'primary_id':pending['to_primary'],'active_primary_unchanged':True,'acceptance_required':True}

    def accept(self,evidence):
        with self.operation():
            state=self.registry.read();pending=state.get('pending_cutover')
            if not pending:raise ValueError('No pending cutover')
            profile=state['profiles'][pending['to_primary']]
            current=profile['backup']['sets'].get(profile['backup']['current_set'])
            required=('offshoot_destination_observed','verified_ingest','ai_index_checked','backup_copy_verified','restore_checked')
            if evidence.get('primary_id')!=profile['id'] or evidence.get('volume_uuid')!=profile['volume_uuid'] or evidence.get('task_uuid')!=(current or {}).get('task_uuid') or evidence.get('observed_at',0)<pending['created_at'] or evidence.get('method')!='operator-observed-acceptance' or any(evidence.get(key) is not True for key in required) or not isinstance(evidence.get('artifacts'),list) or not evidence['artifacts']:
                raise ValueError('Fresh target-specific ingest/index/CCC/restore acceptance evidence is required')
            self.registry.record_cutover('accepted',{**evidence,'kind':'acceptance'})
            self.checkpoint('acceptance_recorded')
            return self.registry.read()

    def finish(self):
        with self.operation():
            state=self.registry.read();pending=state.get('pending_cutover')
            if pending:
                proof=pending['steps'].get('accepted')
                if not proof:raise ValueError('Acceptance is pending; active primary remains unchanged')
            pointer=self.home/'shared/runtime/backup-shortcut/current.plist'
            if pointer.is_symlink():raise ValueError('Backup pointer is a symlink')
            value=self.registry.complete_cutover(self._config(),plistlib.loads(pointer.read_bytes()))
            self.checkpoint('active_primary_committed')
            return {'active_primary':value['active_primary'],'pending_cutover':value['pending_cutover'],'service_started':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--home',type=Path,required=True);p.add_argument('action',choices=['prepare','apply','accept','finish','status','validate']);p.add_argument('--primary');p.add_argument('--offshoot-idle',action='store_true');p.add_argument('--evidence',type=Path);a=p.parse_args()
    try:
        coordinator=Cutover(a.home)
        if a.action=='prepare':
            if not a.primary:raise ValueError('Target primary ID is required')
            value=coordinator.prepare(a.primary,offshoot_idle=a.offshoot_idle)
        elif a.action=='apply':value=coordinator.apply(offshoot_idle=a.offshoot_idle)
        elif a.action=='validate':value=coordinator.validate()
        elif a.action=='accept':
            if not a.evidence:raise ValueError('Acceptance evidence file required')
            value=coordinator.accept(json.loads(a.evidence.read_text()))
        elif a.action=='finish':value=coordinator.finish()
        else:value=coordinator.registry.read()
        print(json.dumps(value,indent=2))
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as exc:p.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
