"""Cutover orchestration with disposable volumes and intercepted app commands."""
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest

from deploy.cutover import Cutover
from ditnotes.shoot_registry import ShootRegistry

TASK='22222222-2222-4222-8222-222222222222'

class CutoverWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.home=self.base/'app';self.home.mkdir()
        self.identities={}
        for name in ('old','annex','backup'):
            path=self.base/name;path.mkdir()
            self.identities[str(path)]={'uuid':name.upper()+'-UUID','mount':str(path),'physical':[name]}
        (self.base/'old/Ingests').mkdir()
        self.registry=ShootRegistry(self.home,probe=self.probe)
        self.before={'volume_uuid':'OLD-UUID','volume_mount':str(self.base/'old'),'ingest_folder':'Ingests','ingest_root':str(self.base/'old/Ingests'),'operator_extension':{'preserve':True}}
        self.config_path=self.home/'shared/config.json';self.config_path.write_text(json.dumps(self.before))
        self.old=self.registry.initialize(self.before)['active_primary'];self.target=self.registry.register_primary(self.base/'annex')['id']
        source=str(self.base/'annex/Ingests');destination=str(self.base/'backup/Shoot')
        self.registry.register_backup(self.target,'B001',{'task_uuid':TASK,'source':source,'destination_mount':str(self.base/'backup'),'destination_uuid':'BACKUP-UUID','destination_relative':'Shoot','configuration_evidence':{'method':'supported-ui','observed_at':time.time(),'task_uuid':TASK,'source':source,'destination':destination}},reviewed=True)
        self.registry.set_current_backup(self.target,'B001')
        self.release=self.home/'releases'/('a'*64);self.release.mkdir(parents=True);(self.home/'current').symlink_to(self.release)
        self.calls=[];self.busy=False;self.fail_setup=False;self.settings_verified=True;self.task_present=True;self.stale_receipt=False
        self.coordinator=Cutover(self.home,registry=self.registry,runner=self.runner)
    def probe(self,path):return copy.deepcopy(self.identities[str(path)])
    def runner(self,args,**kwargs):
        args=list(map(str,args));self.calls.append(args)
        if '--no-running-tasks' in args:
            if self.busy:raise subprocess.CalledProcessError(1,args)
            return SimpleNamespace(stdout='',returncode=0)
        if '--uuids' in args:return SimpleNamespace(stdout=TASK+': Annex backup\n' if self.task_present else '',returncode=0)
        if str(self.release/'setup_workflow.py') in args:
            if self.fail_setup:raise subprocess.CalledProcessError(1,args)
            config=json.loads(self.config_path.read_text())
            (self.home/'shared/runtime/setup-receipt.json').write_text(json.dumps({'volume_uuid':config['volume_uuid'],'ingest_root':config['ingest_root'],'configured_at':1 if self.stale_receipt else time.time()}))
            return SimpleNamespace(stdout='{}',returncode=0)
        if str(self.release/'deploy/offshoot_status.py') in args:return SimpleNamespace(stdout=json.dumps({'settings_verified':self.settings_verified}),returncode=0)
        if str(self.release/'ingest_service.py') in args:return SimpleNamespace(stdout='validation fixture',returncode=0)
        self.fail('Unexpected external command: '+repr(args))
    def prepare_apply(self):
        self.coordinator.prepare(self.target,offshoot_idle=True);self.coordinator.apply(offshoot_idle=True)
    def evidence(self):
        path=self.base/'synthetic-acceptance.json';path.write_text('{"fixture_only":true}')
        return {'primary_id':self.target,'volume_uuid':'ANNEX-UUID','task_uuid':TASK,'observed_at':time.time(),'method':'operator-observed-acceptance','offshoot_destination_observed':True,'verified_ingest':True,'ai_index_checked':True,'backup_copy_verified':True,'restore_checked':True,'artifacts':[str(path)]}
    def assert_pending_old(self):
        state=self.registry.read();self.assertEqual(state['active_primary'],self.old);self.assertIsNotNone(state['pending_cutover'])
    def test_idle_confirmation_and_busy_ccc_block_before_changes(self):
        with self.assertRaises(ValueError):self.coordinator.prepare(self.target)
        self.busy=True
        with self.assertRaises(subprocess.CalledProcessError):self.coordinator.prepare(self.target,offshoot_idle=True)
        self.assertIsNone(self.registry.read()['pending_cutover']);self.assertFalse((self.base/'annex/Ingests').exists());self.assertEqual(json.loads(self.config_path.read_text()),self.before)
    def test_running_service_blocks_before_intent(self):
        lock=self.home/'shared/runtime/service.lock';lock.parent.mkdir(parents=True)
        with lock.open('a+') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'service is running'):self.coordinator.prepare(self.target,offshoot_idle=True)
        self.assertIsNone(self.registry.read()['pending_cutover']);self.assertEqual(self.calls,[])
    def test_prepare_replays_without_switching_or_changing_old_files(self):
        original=self.base/'old/Ingests/original.mov';original.write_bytes(b'untouched original')
        self.coordinator.prepare(self.target,offshoot_idle=True);intent=self.registry.read()['pending_cutover']['id']
        self.coordinator.prepare(self.target,offshoot_idle=True)
        self.assertEqual(self.registry.read()['pending_cutover']['id'],intent);self.assert_pending_old();self.assertEqual(original.read_bytes(),b'untouched original');self.assertEqual(json.loads(self.config_path.read_text()),self.before)
    def test_apply_interruption_and_replay_from_each_external_boundary(self):
        self.coordinator.prepare(self.target,offshoot_idle=True)
        for boundary in ('field_config_written','offshoot_api_returned','backup_pointer_written'):
            with self.subTest(boundary=boundary):
                def crash(phase):
                    if phase==boundary:raise OSError('simulated power loss')
                self.coordinator=Cutover(self.home,registry=self.registry,runner=self.runner,checkpoint=crash)
                with self.assertRaises(OSError):self.coordinator.apply(offshoot_idle=True)
                self.assert_pending_old()
                self.registry=ShootRegistry(self.home,probe=self.probe);self.coordinator=Cutover(self.home,registry=self.registry,runner=self.runner)
                self.coordinator.apply(offshoot_idle=True);self.assert_pending_old()
        self.assertEqual(json.loads(self.config_path.read_text())['operator_extension'],self.before['operator_extension'])
    def test_setup_failure_and_bad_readback_keep_journal_recoverable(self):
        self.coordinator.prepare(self.target,offshoot_idle=True);self.fail_setup=True
        with self.assertRaises(subprocess.CalledProcessError):self.coordinator.apply(offshoot_idle=True)
        self.assert_pending_old();self.fail_setup=False;self.settings_verified=False
        with self.assertRaisesRegex(ValueError,'readback'):self.coordinator.apply(offshoot_idle=True)
        self.assertNotIn('offshoot_configured',self.registry.read()['pending_cutover']['steps']);self.settings_verified=True
        self.coordinator.apply(offshoot_idle=True);self.assert_pending_old()
    def test_stale_matching_target_setup_receipt_does_not_count_as_current_readback(self):
        self.coordinator.prepare(self.target,offshoot_idle=True);self.stale_receipt=True
        with self.assertRaises(ValueError):self.coordinator.apply(offshoot_idle=True)
        self.assertNotIn('offshoot_configured',self.registry.read()['pending_cutover']['steps']);self.assert_pending_old()
        self.stale_receipt=False;self.coordinator.apply(offshoot_idle=True);self.assert_pending_old()
    def test_missing_ccc_task_cannot_bind_or_finish(self):
        self.coordinator.prepare(self.target,offshoot_idle=True);self.task_present=False
        with self.assertRaisesRegex(ValueError,'absent'):self.coordinator.apply(offshoot_idle=True)
        self.assert_pending_old();self.assertNotIn('backup_bound',self.registry.read()['pending_cutover']['steps'])
        with self.assertRaises(ValueError):self.coordinator.finish()
    def test_replaced_target_uuid_blocks_apply_without_writing_config(self):
        self.coordinator.prepare(self.target,offshoot_idle=True)
        self.identities[str(self.base/'annex')]['uuid']='REPLACEMENT-UUID'
        with self.assertRaisesRegex(ValueError,'UUID'):self.coordinator.apply(offshoot_idle=True)
        self.assertEqual(json.loads(self.config_path.read_text()),self.before);self.assert_pending_old()
    def test_validation_is_explicit_target_intent_and_does_not_commit(self):
        with self.assertRaises(ValueError):self.coordinator.validate()
        self.prepare_apply();pending=self.registry.read()['pending_cutover'];result=self.coordinator.validate()
        self.assertEqual(result['primary_id'],self.target);self.assertEqual(self.calls[-1][-2:],['--cutover-intent',pending['id']]);self.assert_pending_old()
    def test_acceptance_rejects_wrong_scope_and_incomplete_or_stale_proof(self):
        self.prepare_apply()
        for field,value in [('primary_id',self.old),('volume_uuid','WRONG'),('task_uuid','11111111-1111-4111-8111-111111111111'),('observed_at',1),('method','assumed'),('artifacts',[])]+[(key,False) for key in ('offshoot_destination_observed','verified_ingest','ai_index_checked','backup_copy_verified','restore_checked')]:
            with self.subTest(field=field):
                proof=self.evidence();proof[field]=value
                with self.assertRaises(ValueError):self.coordinator.accept(proof)
                self.assertNotIn('accepted',self.registry.read()['pending_cutover']['steps']);self.assert_pending_old()
    def test_completion_requires_acceptance_and_is_replayable_after_atomic_commit(self):
        self.prepare_apply()
        with self.assertRaises(ValueError):self.coordinator.finish()
        self.coordinator.accept(self.evidence());self.assert_pending_old()
        def crash(phase):
            if phase=='active_primary_committed':raise OSError('lost caller after durable commit')
        self.coordinator.checkpoint=crash
        with self.assertRaises(OSError):self.coordinator.finish()
        self.registry=ShootRegistry(self.home,probe=self.probe);self.coordinator=Cutover(self.home,registry=self.registry,runner=self.runner);self.coordinator.finish()
        state=self.registry.read();self.assertEqual(state['active_primary'],self.target);self.assertIsNone(state['pending_cutover']);self.assertEqual(len(state['history']),1)
        self.registry.refresh_mount(self.old,self.base/'old');self.assertEqual(self.registry.read()['active_primary'],self.target)

if __name__=='__main__':unittest.main()
