import fcntl
import os
from pathlib import Path
import shutil
import signal
import select
import sqlite3
import tempfile
import unittest

from ditnotes.backup_metadata import snapshot


class MetadataSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root/'project'
        self.notes = self.project/'_DIT_NOTES'
        self.notes.mkdir(parents=True)
        self.target = self.root/'snapshots'/'generation1'

    def tearDown(self):
        self.temp.cleanup()

    def test_committed_wal_data_is_included_and_source_unchanged(self):
        source = self.notes/'project.sqlite3'
        db = sqlite3.connect(source)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute('CREATE TABLE notes(value TEXT)')
            db.commit()
            db.execute("INSERT INTO notes VALUES ('operator edit only in WAL')")
            db.commit()
            before = source.read_bytes()
            wal_before = (self.notes/'project.sqlite3-wal').read_bytes()
            (self.notes/'objects').mkdir()
            (self.notes/'objects'/'caption.json').write_text('{"caption":"Ice"}')
            (self.notes/'backup-rotation').mkdir()
            (self.notes/'backup-rotation'/'ledger.sqlite3').write_text('not copied')
            (self.notes/'unfinished.tmp').write_text('not copied')
            snapshot(self.project, self.target, shutil.copyfile)
            with sqlite3.connect(self.target/'project.sqlite3') as copied:
                self.assertEqual(copied.execute('SELECT value FROM notes').fetchone()[0], 'operator edit only in WAL')
                self.assertEqual(copied.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(before, source.read_bytes())
            self.assertEqual(wal_before, (self.notes/'project.sqlite3-wal').read_bytes())
            self.assertEqual((self.target/'objects'/'caption.json').read_text(), '{"caption":"Ice"}')
            self.assertFalse((self.target/'project.sqlite3-wal').exists())
            self.assertFalse((self.target/'backup-rotation').exists())
            self.assertFalse((self.target/'.process.lock').exists())
            self.assertFalse((self.target/'unfinished.tmp').exists())
        finally:
            db.close()

    def test_busy_project_has_no_published_target(self):
        with (self.notes/'.process.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                snapshot(self.project, self.target, shutil.copyfile)
            self.assertFalse(self.target.exists())

    def test_symlink_source_target_or_content_rejected(self):
        (self.notes/'bad').symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):
            snapshot(self.project, self.target, shutil.copyfile)
        self.assertFalse(self.target.exists())
        (self.notes/'bad').unlink()
        alias = self.root/'alias'; alias.symlink_to(self.project)
        with self.assertRaises(ValueError):
            snapshot(alias, self.target, shutil.copyfile)
        self.root.joinpath('target-alias').symlink_to(self.root/'snapshots')
        with self.assertRaises(ValueError):
            snapshot(self.project, self.root/'target-alias'/'gen', shutil.copyfile)

    def test_no_nested_target_or_overwrite(self):
        with self.assertRaises(ValueError):
            snapshot(self.project, self.notes/'snapshots'/'bad', shutil.copyfile)
        self.target.mkdir(parents=True)
        (self.target/'keep').write_text('old')
        with self.assertRaises(FileExistsError):
            snapshot(self.project, self.target, shutil.copyfile)
        self.assertEqual((self.target/'keep').read_text(), 'old')

    def test_clone_failure_does_not_publish(self):
        (self.notes/'a').write_text('original')
        def broken(src, dst):
            dst.write_text('partial')
            raise OSError('disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
            snapshot(self.project, self.target, broken)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.iterdir()), [])
        self.assertEqual((self.notes/'a').read_text(), 'original')

    def test_sigkill_during_snapshot_never_publishes_and_retry_succeeds(self):
        (self.notes/'a').write_text('original')
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            def interrupted(src, dst):
                dst.write_text('partial')
                os.write(write_fd, b'ready')
                signal.pause()
            try:
                snapshot(self.project, self.target, interrupted)
            finally:
                os._exit(1)
        os.close(write_fd)
        try:
            self.assertTrue(select.select([read_fd], [], [], 3)[0])
            self.assertEqual(os.read(read_fd, 5), b'ready')
            self.assertFalse(self.target.exists())
        finally:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(read_fd)
        snapshot(self.project, self.target, shutil.copyfile)
        self.assertEqual((self.target/'a').read_text(), 'original')
        self.assertTrue(any(p.name.startswith('.generation1.staging-') for p in self.target.parent.iterdir()))

    def test_special_file_rejected(self):
        os.mkfifo(self.notes/'fifo')
        with self.assertRaises(ValueError):
            snapshot(self.project, self.target, shutil.copyfile)
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
