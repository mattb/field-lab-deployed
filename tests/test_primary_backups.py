"""CCC invocation safety with disposable homes, fake volumes and fake commands."""
import json
import subprocess
import time
import unittest
from unittest.mock import Mock
from deploy import backup_shortcut as backup
from tests import test_primary_registry as fixtures
TASK_A,TASK_B=fixtures.TASK_A,fixtures.TASK_B

class PrimaryBackupTests(unittest.TestCase):
    setUp=fixtures.PrimaryRegistryTests.setUp
    record=fixtures.PrimaryRegistryTests.record
    backup=fixtures.PrimaryRegistryTests.backup

    def initialize_pairs(self):
        self.backup(self.first,'backup_old',TASK_A)
        self.backup(self.second,'backup_annex',TASK_B)
        self.runner=Mock(return_value=subprocess.CompletedProcess([],0,TASK_A+': Old\n'+TASK_B+': Annex\n',''))
        backup.bind(self.home,TASK_A,'start',self.runner,primary_id=self.first,set_label='B001',registry=self.registry)

    def run_pair(self,primary,**kwargs):
        return backup.run_primary_task(self.home,primary,'B001',runner=self.runner,registry=self.registry,**kwargs)

    def test_explicit_other_primary_never_changes_active_or_default(self):
        self.initialize_pairs();pointer=self.home/'shared/runtime/backup-shortcut/current.plist';before=pointer.read_bytes()
        result=self.run_pair(self.second)
        self.assertEqual(result['state'],'ccc_finished');self.assertFalse(result['backup_verified'])
        self.assertEqual(pointer.read_bytes(),before);self.assertEqual(self.registry.read()['active_primary'],self.first)
        self.assertEqual(self.runner.call_args.args[0][-2:],['--start='+TASK_B,'--watch'])

    def test_wrong_volume_or_offline_never_invokes_start(self):
        self.initialize_pairs();self.fixture.uuid[str(self.base/'backup_annex')]='WRONG'
        with self.assertRaisesRegex(ValueError,'UUID'):self.run_pair(self.second)
        self.fixture.available[str(self.base/'old')]=False
        with self.assertRaises((OSError,ValueError)):self.run_pair(self.first)
        self.assertFalse(any(any(str(arg).startswith('--start=') for arg in call.args[0]) for call in self.runner.call_args_list))

    def test_interrupted_start_requires_history_before_relaunch(self):
        self.initialize_pairs()
        def crash(phase):
            if phase=='before_ccc_start':raise OSError('simulated sudden loss')
        with self.assertRaises(OSError):self.run_pair(self.second,checkpoint=crash)
        with self.assertRaisesRegex(RuntimeError,'unknown'):self.run_pair(self.second)
        path=backup._attempt_path(self.home,self.second,'B001');attempt=json.loads(path.read_text())
        self.assertEqual(attempt['state'],'launching')
        with self.assertRaises(ValueError):backup.reconcile_primary_attempt(self.home,self.second,'B001',{'method':'supported-ui-history','task_uuid':TASK_A,'result':'not-started','observed_at':time.time()},runner=self.runner)
        for stamp in (float('nan'),float('inf'),True,0,time.time()+60):
            with self.subTest(stamp=stamp),self.assertRaises(ValueError):
                backup.reconcile_primary_attempt(self.home,self.second,'B001',{'method':'supported-ui-history','task_uuid':TASK_B,'result':'not-started','observed_at':stamp},runner=self.runner)
        backup.reconcile_primary_attempt(self.home,self.second,'B001',{'method':'supported-ui-history','task_uuid':TASK_B,'result':'not-started','observed_at':time.time()},runner=self.runner)
        self.assertEqual(self.run_pair(self.second)['state'],'ccc_finished')

    def test_command_failure_is_unknown_not_verified(self):
        self.initialize_pairs()
        def fake(args,**kwargs):
            if any(arg.startswith('--start=') for arg in args):raise subprocess.CalledProcessError(1,args)
            return subprocess.CompletedProcess(args,0,TASK_A+': Old\n'+TASK_B+': Annex\n','')
        self.runner.side_effect=fake
        with self.assertRaises(subprocess.CalledProcessError):self.run_pair(self.second)
        value=json.loads(backup._attempt_path(self.home,self.second,'B001').read_text())
        self.assertEqual(value['state'],'unknown');self.assertFalse(value['backup_verified'])
        with self.assertRaisesRegex(RuntimeError,'unknown'):self.run_pair(self.second)

    def test_pending_cutover_blocks_default_and_unscoped_backup(self):
        self.initialize_pairs();self.registry.begin_cutover(self.second)
        with self.assertRaisesRegex(ValueError,'pending'):backup.run_current_task(self.home,runner=self.runner,registry=self.registry)
        with self.assertRaisesRegex(ValueError,'pending'):self.run_pair(self.second)

if __name__=='__main__':unittest.main()
