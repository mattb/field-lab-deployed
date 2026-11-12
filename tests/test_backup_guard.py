from pathlib import Path
import tempfile
import unittest
from ditnotes.backup_guard import preflight


class BackupGuard(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'primary'/'spool'/'payload';self.dest=self.root/'secondary'/'backup'
        self.source.mkdir(parents=True);self.dest.mkdir(parents=True)
    def probe(self,path):
        primary=Path(path).is_relative_to(self.root/'primary')
        return {'uuid':'PRIMARY' if primary else 'SECONDARY','mount':str(self.root/('primary' if primary else 'secondary')),'physical':['disk:disk1' if primary else 'disk:disk2']}
    def check(self,**kwargs):
        return preflight(self.source,self.dest,self.source,self.dest,'SECONDARY',volume_probe=self.probe,**kwargs)
    def test_reviewed_endpoints_pass(self):
        self.assertEqual(self.check(expected_source_uuid='PRIMARY')['destination_uuid'],'SECONDARY')
    def test_retargeted_task_rejected(self):
        other=self.root/'elsewhere';other.mkdir()
        with self.assertRaises(ValueError):preflight(other,self.dest,self.source,self.dest,'SECONDARY',volume_probe=self.probe)
    def test_wrong_source_and_destination_uuid_rejected(self):
        with self.assertRaises(ValueError):self.check(expected_source_uuid='WRONG')
        with self.assertRaises(ValueError):preflight(self.source,self.dest,self.source,self.dest,'WRONG',volume_probe=self.probe)
    def test_same_backing_disk_rejected(self):
        def shared(path):return {**self.probe(path),'physical':['disk:disk1']}
        with self.assertRaises(ValueError):preflight(self.source,self.dest,self.source,self.dest,'SECONDARY',volume_probe=shared)
    def test_symlink_parent_and_missing_path_rejected(self):
        link=self.root/'alias';link.symlink_to(self.dest.parent,target_is_directory=True)
        with self.assertRaises(ValueError):preflight(self.source,link/'backup',self.source,self.dest,'SECONDARY',volume_probe=self.probe)
        self.dest.rmdir()
        with self.assertRaises(FileNotFoundError):self.check()
    def test_volume_root_refused(self):
        root=self.dest.parent
        with self.assertRaises(ValueError):preflight(self.source,root,self.source,root,'SECONDARY',volume_probe=self.probe)
    def test_parent_traversal_refused(self):
        with self.assertRaises(ValueError):preflight(self.source,self.dest/'..'/'backup',self.source,self.dest,'SECONDARY',volume_probe=self.probe)

if __name__=='__main__':unittest.main()


class StandaloneGuard(unittest.TestCase):
    def test_generated_guard_is_standalone_shell_and_quotes_data(self):
        import subprocess
        from ditnotes.backup_guard import generate_guard
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();source=root/'source $(touch PWNED)';dest=root/'destination space'
            source.mkdir();dest.mkdir()
            text=generate_guard(source,dest,'source-uuid','dest-uuid')
            self.assertNotIn('python',text.lower());self.assertNotIn('eval ',text)
            result=subprocess.run(['/bin/bash','-n'],input=text,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            script=root/'guard.sh';script.write_text(text)
            result=subprocess.run(['/bin/bash',str(script),'wrong',str(dest)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse((root/'PWNED').exists())
    def test_guard_rejects_same_volume_and_symlink_paths(self):
        from ditnotes.backup_guard import generate_guard
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();source=root/'source';dest=root/'dest';source.mkdir();dest.mkdir()
            with self.assertRaises(ValueError):generate_guard(source,dest,'same','same')
            alias=root/'alias';alias.symlink_to(source,target_is_directory=True)
            with self.assertRaises(ValueError):generate_guard(alias,dest,'source','dest')
    def test_user_writable_or_mismatching_installed_guard_rejected(self):
        from ditnotes.backup_guard import validate_installed
        with tempfile.TemporaryDirectory() as td:
            p=Path(td).resolve()/'guard';p.write_text('hello');p.chmod(0o755)
            with self.assertRaises(ValueError):validate_installed(p,'hello')


@unittest.skipUnless(__import__('sys').platform=='darwin','Apple plutil/stat shell acceptance')
class NativeStandaloneGuard(unittest.TestCase):
    def test_valid_source_destination_uuid_and_wrong_uuid(self):
        import plistlib
        import shlex
        import subprocess
        from ditnotes.backup_guard import generate_guard
        with tempfile.TemporaryDirectory() as td:
            root=Path(td).resolve();source=root/'source space';dest=root/'dest space';source.mkdir();dest.mkdir()
            a=root/'source.plist';b=root/'dest.plist'
            a.write_bytes(plistlib.dumps({'VolumeUUID':'SOURCE','MountPoint':str(root)}))
            b.write_bytes(plistlib.dumps({'VolumeUUID':'DEST','MountPoint':str(root)}))
            shim=root/'diskutil'
            shim.write_text('#!/bin/bash\ncase "$3" in '+shlex.quote(str(source))+') /bin/cat '+shlex.quote(str(a))+';; '+shlex.quote(str(dest))+') /bin/cat '+shlex.quote(str(b))+';; *) exit 1;; esac\n');shim.chmod(0o755)
            script=root/'guard.sh'
            for expected,code in [('DEST',0),('WRONG',1)]:
                with self.subTest(expected=expected):
                    text=generate_guard(source,dest,'SOURCE',expected).replace('/usr/sbin/diskutil',shlex.quote(str(shim)))
                    script.write_text(text)
                    result=subprocess.run(['/bin/bash',str(script),str(source)+'/',str(dest)+'/'],capture_output=True,text=True)
                    self.assertEqual(result.returncode,code,result.stderr)
