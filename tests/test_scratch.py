"""Owned scratch cleanup tolerates metadata races without masking inference errors."""
import errno
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from ditnotes.scratch import scratch_directory

class ScratchCleanup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.parent=Path(self.temp.name);self.original=self.parent/'original.mp4';self.original.write_bytes(b'untouched')
    def race_then_remove(self, failures, code=errno.ENOTEMPTY):
        original=shutil.rmtree;calls=[]
        def remove(path):
            calls.append(Path(path))
            if len(calls)<=failures:
                # Reproduce a file appearing in the generated directory while
                # recursive removal is enumerating it.
                (Path(path)/'.DS_Store').write_bytes(b'metadata writer')
                raise OSError(code,'Directory not empty')
            return original(path)
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=remove),patch('ditnotes.scratch.time.sleep') as sleep:
            with scratch_directory(dir=self.parent) as directory:
                scratch=Path(directory);(scratch/'generated.wav').write_bytes(b'scratch')
            self.assertEqual(len(calls),failures+1);self.assertEqual(sleep.call_count,failures)
        self.assertFalse(scratch.exists());self.assertEqual(self.original.read_bytes(),b'untouched');self.assertTrue(self.parent.is_dir())
    def test_one_enotempty_race_recovers(self):self.race_then_remove(1)
    def test_three_enotempty_races_recover(self):self.race_then_remove(3)
    def test_eexist_race_recovers(self):self.race_then_remove(1,errno.EEXIST)
    def test_persistent_cleanup_failure_is_bounded_and_blocks_success(self):
        error=OSError(errno.ENOTEMPTY,'Directory not empty');calls=[]
        def remove(path):calls.append(path);raise error
        reached=False
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=remove),patch('ditnotes.scratch.time.sleep') as sleep:
            with self.assertRaises(OSError) as caught:
                with scratch_directory(dir=self.parent) as directory:pass
                reached=True
            self.assertIs(caught.exception,error);self.assertEqual(len(calls),6);self.assertEqual(sleep.call_count,5)
        self.assertFalse(reached);self.assertTrue(Path(directory).is_dir());self.assertEqual(self.original.read_bytes(),b'untouched')
    def test_analysis_exception_is_retained_when_cleanup_also_fails(self):
        original=RuntimeError('actual inference failure')
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=OSError(errno.ENOTEMPTY,'racing metadata')),patch('ditnotes.scratch.time.sleep'):
            with self.assertRaises(RuntimeError) as caught:
                with scratch_directory(dir=self.parent):raise original
        self.assertIs(caught.exception,original);self.assertIn('cleanup also failed',original.__notes__[0])
    def test_analysis_exception_is_retained_when_retry_recovers(self):
        original=ValueError('actual analysis failure');remove=shutil.rmtree;calls=[]
        def race(path):
            calls.append(path)
            if len(calls)==1:raise OSError(errno.ENOTEMPTY,'racing metadata')
            remove(path)
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=race),patch('ditnotes.scratch.time.sleep'):
            with self.assertRaises(ValueError) as caught:
                with scratch_directory(dir=self.parent) as directory:raise original
        self.assertIs(caught.exception,original);self.assertEqual(len(calls),2);self.assertFalse(Path(directory).exists())
    def test_permission_error_is_not_retried(self):
        error=PermissionError(errno.EACCES,'denied')
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=error) as remove,patch('ditnotes.scratch.time.sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:
                with scratch_directory(dir=self.parent):pass
        self.assertIs(caught.exception,error);remove.assert_called_once();sleep.assert_not_called()
    def test_interrupt_remains_original_when_cleanup_fails(self):
        original=KeyboardInterrupt('operator stopped')
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=PermissionError(errno.EACCES,'denied')):
            with self.assertRaises(KeyboardInterrupt) as caught:
                with scratch_directory(dir=self.parent):raise original
        self.assertIs(caught.exception,original)
    def test_invalid_policy_creates_no_scratch(self):
        for kwargs in [{'attempts':0},{'attempts':True},{'delay':float('inf')},{'delay':-1}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                with scratch_directory(dir=self.parent,**kwargs):pass
        self.assertEqual(list(self.parent.iterdir()),[self.original])

if __name__=='__main__':unittest.main()
