"""Exercise actual macOS APFS COW, with generated disposable bytes only."""
from pathlib import Path
import sys
import tempfile
import unittest
from ditnotes.backup_rotation import clonefile,digest

@unittest.skipUnless(sys.platform=='darwin','macOS clonefile required')
class NativeCloneTests(unittest.TestCase):
    def test_clone_is_independent_after_original_write(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();source=root/'original';target=root/'clone'
            source.write_bytes(b'field-lab-disposable-test'*50000)
            before=digest(source);clonefile(source,target)
            self.assertEqual(digest(target),before)
            self.assertNotEqual(source.stat().st_ino,target.stat().st_ino)
            with source.open('r+b') as f:f.write(b'new data')
            self.assertEqual(digest(target),before)
            self.assertNotEqual(digest(source),before)

if __name__=='__main__':unittest.main()
