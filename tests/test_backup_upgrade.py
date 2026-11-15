"""Source-only recipient upgrade simulation; no models, apps, or real CCC task starts."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('backup_upgrade_manager',ROOT/'deploy/manager.py')
manager=importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)

class BackupUpgrade(unittest.TestCase):
    def test_fresh_source_candidate_preserves_old_state_and_has_runnable_backup_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();home=base/'Field Lab';old_source=base/'older source';(old_source/'deploy').mkdir(parents=True)
            (old_source/'deploy/assets.lock.json').write_text('{}');(old_source/'old-version.txt').write_text('previous managed version')
            old=manager.stage(old_source,home);manager.set_pointer(home,'current',Path(old['release']))
            shared=home/'shared';(shared/'runtime').mkdir(exist_ok=True)
            keep=shared/'runtime/legacy-backup-records.json';keep.write_text('{"preserve":"existing copy evidence"}')
            (shared/'config.json').write_text('{"operator":"preserve"}')
            before={str(p.relative_to(shared)):p.read_bytes() for p in shared.rglob('*') if p.is_file()}
            # Prepare a minimal portable source package using only checked-in dependencies.
            source=base/'new source';source.mkdir()
            required=['deploy/assets.lock.json','deploy/backup_shortcut.py','deploy/desktop.py','deploy/manager.py','.agents/skills/field-lab-backup/SKILL.md','.agents/skills/field-lab-backup/scripts/plan_rotation.py','docs/BACKUP-ADOPTION.md','docs/prompts/UPDATE-AND-ADOPT-BACKUPS.md']
            for relative in required:
                dest=source/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,dest)
            candidate=manager.stage(source,home);release=Path(candidate['release'])
            self.assertEqual((home/'current').resolve(),Path(old['release']))
            self.assertEqual({str(p.relative_to(shared)):p.read_bytes() for p in shared.rglob('*') if p.is_file()},before)
            self.assertTrue((release/'.agents/skills/field-lab-backup/scripts/plan_rotation.py').is_file())
            desktop=base/'Recipient Desktop';desktop.mkdir()
            # Run with isolated import path from the sealed candidate, not the checkout.
            code='''import json,sys,plistlib
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,sys.argv[1])
from deploy.backup_shortcut import install,bind
home,desktop=Path(sys.argv[2]),Path(sys.argv[3])
def compiler(src,dest,*args):
 dest.mkdir();(dest/'fixture').write_text(src.read_text())
install(home,desktop,compiler)
assert not (home/'shared/runtime/backup-shortcut/current.plist').exists()
task='11111111-1111-4111-8111-111111111111'
runner=Mock();runner.return_value.stdout=task+': Recipient test task\\n'
bind(home,task,'start',runner)
before=(home/'shared/runtime/backup-shortcut/current.plist').read_bytes()
install(home,desktop,compiler)
assert (home/'shared/runtime/backup-shortcut/current.plist').read_bytes()==before
assert all(c.args[0][-1]=='--uuids' for c in runner.call_args_list)
print(json.dumps({'installed':True,'binding_preserved':True,'task_started':False}))
'''
            done=subprocess.run([sys.executable,'-I','-c',code,str(release),str(home),str(desktop)],cwd=base,capture_output=True,text=True,timeout=30)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertFalse(json.loads(done.stdout)['task_started'])
            self.assertEqual(keep.read_text(),'{"preserve":"existing copy evidence"}')
            self.assertEqual((shared/'config.json').read_text(),'{"operator":"preserve"}')
            self.assertEqual((home/'current').resolve(),Path(old['release']))
            self.assertTrue((desktop/'Backup.app').is_symlink())

if __name__=='__main__':unittest.main()
