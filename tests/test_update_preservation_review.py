"""Independent software-update review: no live disks, app settings or CCC commands."""
import argparse
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from deploy import update_preservation as preservation

spec=importlib.util.spec_from_file_location('preservation_review_field',Path(__file__).resolve().parents[1]/'deploy/field.py')
field=importlib.util.module_from_spec(spec);spec.loader.exec_module(field)

class UpdatePreservationReview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.home=self.base/'Application Home'
        (self.home/'shared/runtime/backup-shortcut').mkdir(parents=True)
        self.old=self.home/'releases'/('a'*64);self.new=self.home/'releases'/('b'*64)
        self.old.mkdir(parents=True);self.new.mkdir(parents=True)
        (self.new/'deploy').mkdir();(self.new/'deploy/data-capabilities.json').write_text('{"unified_people":1,"multi_primary":1}')
        self.project=self.base/'Primary One'/'Originals';self.project.mkdir(parents=True)
        self.config=self.home/'shared/config.json';self.config.write_bytes((json.dumps({'volume_uuid':'FIXTURE-UUID','volume_mount':str(self.project.parent),'ingest_root':str(self.project),'ingest_folder':'Originals','port':8877,'private_operator_option':{'retain':True}},indent=3)+'\n').encode())
        self.pointer=self.home/'shared/runtime/backup-shortcut/current.plist'
        self.pointer.write_bytes(plistlib.dumps({'schema':1,'task_uuid':'11111111-1111-4111-8111-111111111111','task_name':'Actual operator task','mode':'start','unknown_field':'retain'}))
        self.tasks=self.base/'simulated CCC tasks';self.tasks.mkdir()
        (self.tasks/'operator-task.txt').write_text('Original source; include/exclude filters; destination; schedules; retained')
        self.args=argparse.Namespace(home=self.home,offshoot_idle=True,interrupt_ai=False)
        self.state={'current':str(self.old),'previous':str(self.new)}

    def snapshot(self):
        paths=[self.config,self.pointer,*self.tasks.iterdir()]
        registry=self.home/'shared/shoot.json'
        if registry.exists():paths.append(registry)
        return {str(p):p.read_bytes() for p in paths}

    def switch(self):
        commands=[];services=[]
        with patch.object(field.manager,'status',return_value=self.state),patch.object(field,'run',side_effect=lambda command,**kwargs:commands.append(list(map(str,command)))),patch.object(field.manager,'mark_ready'),patch.object(field,'service',side_effect=lambda release,action:services.append(action)),patch.object(field.manager,'activate',return_value={'changed':True}):
            result=field.switch(self.args,self.new.name)
        self.assertEqual(services,['stop'])
        self.assertEqual(len(commands),1);self.assertIn('bootstrap.py',commands[0][1]);self.assertEqual(commands[0][-1],'--check')
        self.assertTrue((self.home/'run').is_file());self.assertIn('stopped',result['service'])
        return result

    def test_legacy_alpha_baselines_preserve_bytes_without_enrolling_a_primary(self):
        for baseline in ('alpha.8','alpha.9','alpha.10','unknown'):
            with self.subTest(baseline=baseline):
                if baseline!='unknown':
                    (self.old/'deploy').mkdir(exist_ok=True);(self.old/'deploy/data-capabilities.json').write_text('{"unified_people":1}')
                elif (self.old/'deploy/data-capabilities.json').exists():(self.old/'deploy/data-capabilities.json').unlink()
                before=self.snapshot();self.switch();self.assertEqual(self.snapshot(),before)
                self.assertFalse((self.home/'shared/shoot.json').exists())

    def test_existing_registry_and_pending_cutover_are_not_completed_by_software_switch(self):
        registry=self.home/'shared/shoot.json'
        for pending in (None,{'id':'fixture-intent','from_primary':'old','to_primary':'annex','steps':{'quiescent':{'observed':True}}}):
            with self.subTest(pending=bool(pending)):
                registry.write_text(json.dumps({'schema':1,'active_primary':'old','pending_cutover':pending,'profiles':{'old':{'volume_uuid':'FIXTURE-UUID','backup':{'current_set':'B002','sets':{'B001':{'task_uuid':'old-closed'},'B002':{'task_uuid':'old-current'}}}},'annex':{'volume_uuid':'OTHER-UUID','backup':{'current_set':'B001','sets':{'B001':{'task_uuid':'annex'}}}}}}))
                before=self.snapshot();self.switch();self.assertEqual(self.snapshot(),before)

    def test_offline_source_never_creates_fake_mount_or_rewrites_destination(self):
        self.project.rmdir();self.project.parent.rmdir();before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'unavailable'):field.backup_state(self.home,self.old)
        self.assertFalse(self.project.parent.exists());self.assertEqual(self.snapshot(),before)

class GuardReview(unittest.TestCase):
    setUp=UpdatePreservationReview.setUp
    snapshot=UpdatePreservationReview.snapshot

    def test_failed_ccc_inventory_never_publishes_a_success_receipt(self):
        runner=Mock(side_effect=subprocess.CalledProcessError(1,['fixture-ccc','--uuids']))
        before=self.snapshot()
        with self.assertRaises(subprocess.CalledProcessError):preservation.capture(self.home,runner)
        self.assertEqual(self.snapshot(),before)
        self.assertFalse((self.home/'shared/runtime/update-preservation').exists())

    def test_unknown_private_receipt_fields_survive_idempotent_check(self):
        runner=Mock(return_value=subprocess.CompletedProcess([],0,'11111111-1111-4111-8111-111111111111: Operator task\n',''))
        result=preservation.capture(self.home,runner);path=Path(result['receipt'])
        value=json.loads(path.read_text());value['operator_private_context']={'retain':True};value['history']=[{'previous':'retain'}];path.write_text(json.dumps(value))
        before=self.snapshot()
        for _ in range(2):self.assertEqual(preservation.check(self.home,result['id'],runner)['status'],'preserved')
        self.assertEqual(self.snapshot(),before)
        current=json.loads(path.read_text());self.assertEqual(current['operator_private_context'],{'retain':True});self.assertEqual(current['history'],[{'previous':'retain'}])
        self.assertTrue(all(call.args[0][-1]=='--uuids' for call in runner.call_args_list))

    def test_receipt_cannot_be_reused_for_another_home(self):
        runner=Mock(return_value=subprocess.CompletedProcess([],0,'',''))
        result=preservation.capture(self.home,runner)
        other=self.base/'Other Home';(other/'shared/runtime/update-preservation').mkdir(parents=True)
        (other/'shared/config.json').write_bytes(self.config.read_bytes())
        (other/'shared/runtime/update-preservation'/(result['id']+'.json')).write_bytes(Path(result['receipt']).read_bytes())
        with self.assertRaisesRegex(ValueError,'another installation'):preservation.check(other,result['id'],runner)

    def test_selected_release_does_not_reapply_old_ccc_configuration_migration(self):
        root=Path(__file__).resolve().parents[1]
        manifest=json.loads((root/'FIELD-LAB-RELEASE.json').read_text())
        self.assertEqual(manifest['tag'],'v0.1.0-alpha.11')
        migrations=manifest['workflow_migrations'];self.assertTrue(migrations)
        self.assertNotIn('ccc-date-filter-v1',[item['id'] for item in migrations])
        for item in migrations:self.assertTrue((root/item['procedure']).is_file())

if __name__=='__main__':unittest.main()
