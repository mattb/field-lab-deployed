"""Release adoption guards operate only on disposable state/fake CCC inventory."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from deploy import update_preservation as preservation

TASK='11111111-1111-4111-8111-111111111111'
class UpdatePreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name).resolve()/'application';self.shared=self.home/'shared';self.shared.mkdir(parents=True)
        self.config={'volume_uuid':'REAL-OPERATOR-UUID','volume_mount':'/Volumes/Operator Primary','ingest_root':'/Volumes/Operator Primary/Shoot','ingest_folder':'Shoot','custom':{'keep':True}}
        (self.shared/'config.json').write_text(json.dumps(self.config))
        pointer=self.shared/'runtime/backup-shortcut/current.plist';pointer.parent.mkdir(parents=True);pointer.write_bytes(b'existing private binding')
        self.runner=Mock(return_value=subprocess.CompletedProcess([],0,TASK+': Existing operator task\n',''))
    def snapshot_files(self):return {str(p.relative_to(self.shared)):p.read_bytes() for p in self.shared.rglob('*') if p.is_file() and 'update-preservation' not in str(p)}
    def test_legacy_adoption_preserves_bytes_and_never_creates_registry(self):
        before=self.snapshot_files();result=preservation.capture(self.home,self.runner)
        self.assertEqual(preservation.check(self.home,result['id'],self.runner)['status'],'preserved')
        self.assertEqual(self.snapshot_files(),before);self.assertFalse((self.shared/'shoot.json').exists())
        self.assertTrue(all(call.args[0][-1]=='--uuids' for call in self.runner.call_args_list))
    def test_existing_registry_including_pending_cutover_is_retained(self):
        (self.shared/'shoot.json').write_text(json.dumps({'active_primary':'existing','pending_cutover':{'id':'unfinished'},'private':'retain'}))
        before=self.snapshot_files();result=preservation.capture(self.home,self.runner)
        preservation.check(self.home,result['id'],self.runner);self.assertEqual(self.snapshot_files(),before)
    def test_drift_is_reported_and_never_repaired(self):
        for relative in preservation.FILES:
            with self.subTest(relative=relative):
                result=preservation.capture(self.home,self.runner);path=self.shared/relative;path.parent.mkdir(parents=True,exist_ok=True)
                original=path.read_bytes() if path.exists() else None
                changed=json.dumps({**self.config,'ingest_root':'/different'}).encode() if relative=='config.json' else b'changed state'
                path.write_bytes(changed)
                with self.assertRaisesRegex(ValueError,'changed'):preservation.check(self.home,result['id'],self.runner)
                self.assertEqual(path.read_bytes(),changed)
                if original is None:path.unlink()
                else:path.write_bytes(original)
    def test_task_inventory_drift_and_unknown_receipts_fail_closed(self):
        result=preservation.capture(self.home,self.runner);self.runner.return_value.stdout=''
        with self.assertRaisesRegex(ValueError,'changed'):preservation.check(self.home,result['id'],self.runner)
        for value in ('../escape','f'*32):
            with self.assertRaises((ValueError,OSError)):preservation.check(self.home,value,self.runner)
    def test_missing_config_or_symlink_does_not_publish_success(self):
        path=self.shared/'config.json';path.unlink()
        with self.assertRaises(ValueError):preservation.capture(self.home,self.runner)
        target=self.home/'outside';target.write_text('{}');path.symlink_to(target)
        with self.assertRaises(ValueError):preservation.capture(self.home,self.runner)

if __name__=='__main__':unittest.main()
