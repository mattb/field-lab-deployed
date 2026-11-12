"""Rotation + live-WAL metadata integration, fake CCC/disposable media only."""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from ditnotes.backup_rotation import Manager
from tests.test_backup_rotation_disaster import Backend,TASK

class SimulatedPowerLoss(BaseException):pass

class RotationMetadataIntegration(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory(prefix='rotation-metadata-');self.addCleanup(tmp.cleanup);base=Path(tmp.name).resolve()
        project=base/'primary/project';notes=project/'_DIT_NOTES';notes.mkdir(parents=True);(base/'backup').mkdir()
        card=project/'20261111/A001';card.mkdir(parents=True);(card/'video.mp4').write_bytes(b'footage'*100)
        db=sqlite3.connect(notes/'project.sqlite3');self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE notes(value TEXT)');db.commit();db.execute("INSERT INTO notes VALUES ('WAL operator note')");db.commit()
        (notes/'objects').mkdir();(notes/'objects/caption.json').write_text('{"caption":"Ice"}')
        b=Backend(base);m=Manager.initialize(notes/'backup-rotation',project,base/'primary/spool',backend=b,clone=shutil.copy2)
        m.add_set('B001',base/'backup','copy',TASK,dedicated_task_reviewed=True);m.register_card(card,'trusted-metadata-fixture')
        return base,b,m,db
    def snapshot_path(self,base,m):
        revision=m.state['sets'][0]['metadata_revision']
        return base/'backup/copy/FieldLabMetadata'/revision/'_DIT_NOTES'
    def test_default_state_under_notes_is_excluded_and_wal_restores(self):
        base,b,m,db=self.fixture();m.run_pending();saved=self.snapshot_path(base,m)
        self.assertFalse((saved/'backup-rotation').exists());self.assertFalse((saved/'project.sqlite3-wal').exists())
        self.assertEqual((saved/'objects/caption.json').read_text(),'{"caption":"Ice"}')
        with sqlite3.connect(saved/'project.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT value FROM notes').fetchall(),[('WAL operator note',)])
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(db.execute('SELECT value FROM notes').fetchall(),[('WAL operator note',)])
        self.assertEqual(m.status()['jobs'][0]['state'],'verified')
    def test_unchanged_metadata_does_not_duplicate_payload_or_restart_ccc(self):
        base,b,m,db=self.fixture();m.run_pending();revision=m.state['sets'][0]['metadata_revision'];calls=len(b.calls)
        m.run_pending()
        self.assertEqual(m.state['sets'][0]['metadata_revision'],revision);self.assertEqual(len(b.calls),calls)
        revisions=list((base/'primary/spool/B001/payload/FieldLabMetadata').iterdir());self.assertEqual(len(revisions),1)
        snapshots=list((base/'primary/spool').glob('metadata-*'));self.assertEqual(len(snapshots),1)
        self.assertTrue(snapshots[0].name.startswith('metadata-sealed-'))
    def test_new_note_after_all_cards_verified_is_backed_up(self):
        base,b,m,db=self.fixture();m.run_pending();before=m.state['sets'][0]['metadata_revision'];calls=len(b.calls)
        db.execute("INSERT INTO notes VALUES ('later edit')");db.commit();m.run_pending()
        self.assertEqual(m.status()['jobs'][0]['state'],'verified');self.assertNotEqual(m.state['sets'][0]['metadata_revision'],before)
        self.assertEqual(len(b.calls),calls+1)
        with sqlite3.connect(self.snapshot_path(base,m)/'project.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT value FROM notes').fetchall(),[('WAL operator note',),('later edit',)])
    def test_interruption_after_metadata_staging_leaves_pending_until_verified(self):
        base,b,m,db=self.fixture()
        def crash(stage):
            if stage=='before_ccc':raise SimulatedPowerLoss()
        m.checkpoint=crash
        with self.assertRaises(SimulatedPowerLoss):m.run_pending()
        self.assertFalse(b.calls);self.assertIn('metadata_pending',m.state['sets'][0])
        resumed=Manager(m.root,backend=b,clone=shutil.copy2)
        self.assertNotEqual(resumed.status()['jobs'][0]['state'],'verified')
        resumed.run_pending();self.assertEqual(resumed.status()['jobs'][0]['state'],'verified')
        self.assertNotIn('metadata_pending',resumed.state['sets'][0]);self.assertTrue(self.snapshot_path(base,resumed).is_dir())
    def test_metadata_reserve_prevents_card_only_fit(self):
        base,b,m,db=self.fixture();b.free_bytes=11000 # 10K reserve + 700 footage fits, metadata does not.
        m.run_pending();self.assertFalse(b.calls);self.assertIsNone(m.state['jobs'][0]['set']);self.assertNotIn('metadata_revision',m.state['sets'][0])
    def test_destination_fills_after_assignment_before_launch(self):
        base,b,m,db=self.fixture()
        def fill(stage):
            if stage=='assigned':b.free_bytes=0
        m.checkpoint=fill
        try:m.run_pending()
        except (RuntimeError,ValueError,OSError):pass
        self.assertFalse(b.calls);self.assertNotEqual(m.status()['jobs'][0]['state'],'verified')
    def test_metadata_only_update_space_failure_preserves_previous_revision(self):
        base,b,m,db=self.fixture();m.run_pending();before=m.state['sets'][0]['metadata_revision'];calls=len(b.calls)
        db.execute("INSERT INTO notes VALUES ('will wait for space')");db.commit();b.free_bytes=10000
        m.run_pending();self.assertEqual(len(b.calls),calls);self.assertEqual(m.state['sets'][0]['metadata_revision'],before)
        self.assertIn('space',m.status()['message'].lower())

if __name__=='__main__':unittest.main()
