"""Initialization/registration recovery tests; fake CCC, temporary mounts only."""
import fcntl
from pathlib import Path
import shutil
import tempfile
import unittest

from ditnotes.backup_rotation import Manager,publish_json
from tests.test_backup_rotation_disaster import Backend,TASK

class PowerLoss(BaseException):pass

class BackupInitialization(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory(prefix='backup-init-');self.addCleanup(tmp.cleanup)
        base=Path(tmp.name).resolve();project=base/'primary/project';project.mkdir(parents=True);(base/'backup').mkdir()
        card=project/'20261111/A001';card.mkdir(parents=True);(card/'clip.mp4').write_bytes(b'own footage')
        b=Backend(base)
        return base,project,card,b
    def test_matching_initialize_rerun_preserves_assignment_and_refuses_reset(self):
        base,project,card,b=self.fixture();state=base/'state';spool=base/'primary/spool'
        m=Manager.initialize(state,project,spool,backend=b,clone=shutil.copy2)
        m.add_set('B001',base/'backup','copy',TASK,dedicated_task_reviewed=True);m.register_card(card,'fixture-receipt')
        def pause(stage):
            if stage=='assigned':raise PowerLoss()
        m.checkpoint=pause
        with self.assertRaises(PowerLoss):m.run_pending()
        before=(state/'state.json').read_bytes()
        resumed=Manager.initialize(state,project,spool,backend=b,clone=shutil.copy2,reserve_fraction=.2)
        self.assertEqual((state/'state.json').read_bytes(),before)
        self.assertEqual(resumed.state['jobs'][0]['set'],'B001');self.assertEqual(resumed.state['reserve_fraction'],.1)
        other=base/'primary/other-project';other.mkdir()
        with self.assertRaisesRegex(ValueError,'differs'):Manager.initialize(state,other,spool,backend=b,clone=shutil.copy2)
        self.assertEqual((state/'state.json').read_bytes(),before)
        resumed.run_pending();self.assertEqual(resumed.status()['jobs'][0]['state'],'verified')
    def test_concurrent_initializer_lock_blocks_before_state_or_spool_creation(self):
        base,project,card,b=self.fixture();state=base/'state';state.mkdir();spool=base/'primary/spool'
        with (state/'rotation.lock').open('a+') as locked:
            fcntl.flock(locked,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'initialization is already running'):
                Manager.initialize(state,project,spool,backend=b,clone=shutil.copy2)
            self.assertFalse((state/'state.json').exists());self.assertFalse(spool.exists())
        initialized=Manager.initialize(state,project,spool,backend=b,clone=shutil.copy2)
        self.assertEqual(initialized.state['jobs'],[])
    def test_committed_set_without_payload_directory_recovers_without_reregistering(self):
        base,project,card,b=self.fixture();m=Manager.initialize(base/'state',project,base/'primary/spool',backend=b,clone=shutil.copy2)
        def pause(stage):
            if stage=='committed':raise PowerLoss()
        m.checkpoint=pause
        with self.assertRaises(PowerLoss):m.add_set('B001',base/'backup','copy',TASK,dedicated_task_reviewed=True)
        self.assertFalse((base/'primary/spool/B001/payload').exists())
        resumed=Manager(base/'state',backend=b,clone=shutil.copy2)
        self.assertEqual([entry['label'] for entry in resumed.state['sets']],['B001'])
        resumed.register_card(card,'fixture-receipt');resumed.run_pending()
        self.assertEqual(resumed.status()['jobs'][0]['state'],'verified');self.assertEqual(len(b.calls),1)
        self.assertEqual((base/'backup/copy/20261111/A001/clip.mp4').read_bytes(),b'own footage')
    def test_publish_missing_parent_never_creates_replacement_mount(self):
        base,project,card,b=self.fixture();missing=base/'missing-volume'/'backup-state'/'state.json'
        with self.assertRaises((ValueError,FileNotFoundError)):publish_json(missing,{'fixture':'data'})
        self.assertFalse((base/'missing-volume').exists())

if __name__=='__main__':unittest.main()
