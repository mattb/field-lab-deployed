"""Independent rotation fault tests; disposable files and fake CCC only."""
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ditnotes.backup_rotation import Manager

TASK='11111111-1111-4111-8111-111111111111'

class Backend:
    def __init__(self,base):
        self.base=Path(base);self.running=False;self.calls=[];self.uuid='backup-uuid';self.free_bytes=100000;self.capacity_bytes=100000
        self.after_copy=None;self.copy=True
    def identity(self,path):
        path=Path(path).absolute()
        for folder,uid,physical in [('primary','primary-uuid',['disk1']),('backup',self.uuid,['disk2'])]:
            mount=self.base/folder
            if path==mount or path.is_relative_to(mount):return {'uuid':uid,'mount':str(mount),'physical':physical,'filesystem':'apfs'}
        raise ValueError('unknown fixture mount')
    def free(self,path):return self.free_bytes
    def capacity(self,path):return self.capacity_bytes
    def idle(self):return not self.running
    def run(self,task):
        self.calls.append(task)
        if self.copy:shutil.copytree(self.base/'primary/spool/B001/payload',self.base/'backup/copy',dirs_exist_ok=True)
        if self.after_copy:self.after_copy()

class RotationDisasters(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory(prefix='rotation-disaster-');self.addCleanup(tmp.cleanup);base=Path(tmp.name).resolve()
        project=base/'primary/project';project.mkdir(parents=True);(base/'backup').mkdir()
        card=project/'20261111/A001';card.mkdir(parents=True);(card/'video.mp4').write_bytes(b'footage'*100)
        b=Backend(base);m=Manager.initialize(base/'state',project,base/'primary/spool',backend=b,clone=shutil.copy2)
        m.add_set('B001',base/'backup','copy',TASK,dedicated_task_reviewed=True);m.register_card(card,'trusted-fixture-receipt')
        return base,b,m
    def test_zero_exit_without_bytes_never_marks_verified(self):
        base,b,m=self.fixture();b.copy=False
        with self.assertRaises((ValueError,FileNotFoundError)):m.run_pending()
        self.assertNotEqual(m.status()['jobs'][0]['state'],'verified')
        b.copy=True;m.run_pending();self.assertEqual(m.status()['jobs'][0]['state'],'verified')
    def test_same_mount_label_replaced_uuid_fails_closed(self):
        base,b,m=self.fixture();b.uuid='replacement-uuid'
        m.run_pending();self.assertFalse(b.calls);self.assertNotEqual(m.status()['jobs'][0]['state'],'verified')
        self.assertIn('B001',m.status()['message'])
    def test_uuid_changed_during_copy_never_marks_verified(self):
        base,b,m=self.fixture();b.after_copy=lambda:setattr(b,'uuid','replacement-uuid')
        with self.assertRaises(RuntimeError):m.run_pending()
        self.assertNotEqual(m.status()['jobs'][0]['state'],'verified')
    def test_original_changes_during_copy_never_marks_verified(self):
        base,b,m=self.fixture();b.after_copy=lambda:(base/'primary/project/20261111/A001/video.mp4').write_bytes(b'changed')
        with self.assertRaises(ValueError):m.run_pending()
        self.assertNotEqual(m.status()['jobs'][0]['state'],'verified')
    def test_partial_clone_exception_is_recoverable_without_source_write(self):
        base,b,m=self.fixture();source=base/'primary/project/20261111/A001/video.mp4';before=source.read_bytes();calls=[]
        def fail_after_clone(src,dst):
            shutil.copy2(src,dst);calls.append(str(dst));raise OSError('simulated interrupted clone')
        m.clone=fail_after_clone
        with self.assertRaises(OSError):m.run_pending()
        self.assertEqual(source.read_bytes(),before);self.assertFalse(b.calls)
        resumed=Manager(base/'state',backend=b,clone=shutil.copy2);resumed.run_pending()
        self.assertEqual(resumed.status()['jobs'][0]['state'],'verified');self.assertEqual(len(b.calls),1)
    def test_destination_corruption_is_detected(self):
        base,b,m=self.fixture()
        b.after_copy=lambda:(base/'backup/copy/20261111/A001/video.mp4').write_bytes(b'wrong')
        with self.assertRaises(ValueError):m.run_pending()
        self.assertNotEqual(m.status()['jobs'][0]['state'],'verified')
    def test_symlink_source_added_after_queue_is_refused(self):
        base,b,m=self.fixture();outside=base/'outside';outside.write_text('outside')
        (base/'primary/project/20261111/A001/link').symlink_to(outside)
        with self.assertRaises(ValueError):m.run_pending()
        self.assertFalse(b.calls);self.assertEqual(outside.read_text(),'outside')
    def test_insufficient_capacity_preserves_pending_and_no_launch(self):
        base,b,m=self.fixture();b.free_bytes=10000 # reserve itself is 10K
        m.run_pending();self.assertFalse(b.calls);self.assertIsNone(m.state['jobs'][0]['set'])
        self.assertTrue(m.state['sets'][0]['closed'])
    def test_oversize_card_does_not_permanently_close_empty_drive(self):
        base,b,m=self.fixture();b.capacity_bytes=500;b.free_bytes=500
        m.run_pending();self.assertFalse(b.calls);self.assertFalse(m.state['sets'][0]['closed'])
        self.assertIsNone(m.state['jobs'][0]['set'])
    def test_two_capacities_rotate_whole_cards_preserving_same_date_and_companions(self):
        base,b,m=self.fixture()
        # Replace the initial fixture registration with a distinct larger-card fixture.
        project=base/'primary/project';card=project/'20261111/A001'
        (card/'video.mp4').write_bytes(b'a'*70000);(card/'video.LRV').write_bytes(b'l'*100);(card/'video.THM').write_bytes(b't'*50)
        # Initial fixture queue is disposable; rebuild its isolated state, never production state.
        shutil.rmtree(base/'state');shutil.rmtree(base/'primary/spool')
        second=base/'backup2';second.mkdir()
        class TwoDisks(Backend):
            offline=False
            def identity(self,path):
                path=Path(path).absolute()
                if path==second or path.is_relative_to(second):return {'uuid':'backup2-uuid','mount':str(second),'physical':['disk3'],'filesystem':'apfs'}
                if self.offline and (path==base/'backup' or path.is_relative_to(base/'backup')):raise OSError('old backup disconnected')
                return super().identity(path)
            def capacity(self,path):return 50000 if Path(path)==second else 100000
            def free(self,path):
                used=sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file())
                return self.capacity(path)-used
            def run(self,task):
                self.calls.append(task);label,mount=('B001',base/'backup') if task==TASK else ('B002',second)
                shutil.copytree(base/'primary/spool'/label/'payload',mount/'copy',dirs_exist_ok=True)
        b=TwoDisks(base);m=Manager.initialize(base/'state',project,base/'primary/spool',backend=b,clone=shutil.copy2)
        m.add_set('B001',base/'backup','copy',TASK,dedicated_task_reviewed=True)
        other_task='22222222-2222-4222-8222-222222222222';m.add_set('B002',second,'copy',other_task,dedicated_task_reviewed=True)
        m.register_card(card,'first')
        card2=project/'20261111/B001';card2.mkdir();(card2/'video.mp4').write_bytes(b'b'*30000);(card2/'video.LRV').write_bytes(b'preview');(card2/'video.THM').write_bytes(b'thumb')
        m.register_card(card2,'second');m.run_pending()
        self.assertEqual([(j['relative'],j['set'],j['state']) for j in m.state['jobs']],[('20261111/A001','B001','verified'),('20261111/B001','B002','verified')])
        self.assertTrue(m.state['sets'][0]['closed'])
        self.assertEqual((second/'copy/20261111/B001/video.LRV').read_bytes(),b'preview')
        self.assertEqual((base/'backup/copy/20261111/A001/video.THM').read_bytes(),b't'*50)
        self.assertFalse((base/'backup/copy/20261111/B001').exists());self.assertFalse((second/'copy/20261111/A001').exists())
        b.offline=True;card3=project/'20261112/A002';card3.mkdir(parents=True);(card3/'clip.mp4').write_bytes(b'c'*1000)
        m.register_card(card3,'third');m.run_pending()
        self.assertEqual(m.state['jobs'][-1]['set'],'B002');self.assertEqual(m.state['jobs'][-1]['state'],'verified')
    def test_concurrent_manager_cannot_launch(self):
        base,b,m=self.fixture();other=Manager(base/'state',backend=b,clone=shutil.copy2)
        with m.locked():
            with self.assertRaisesRegex(RuntimeError,'already working'):other.run_pending()
        self.assertFalse(b.calls)
    def test_corrupt_state_does_not_reset_or_launch(self):
        base,b,m=self.fixture();path=base/'state/state.json';path.write_text('{"schema":')
        with self.assertRaises((ValueError,json.JSONDecodeError)):Manager(base/'state',backend=b,clone=shutil.copy2)
        self.assertFalse(b.calls);self.assertEqual(path.read_text(),'{"schema":')
    def test_ledger_write_failure_recovers_after_verified_commit(self):
        base,b,m=self.fixture()
        def unavailable(entry):raise OSError('simulated ledger write interruption')
        m._mirror_ledger=unavailable
        with self.assertRaises(OSError):m.run_pending()
        self.assertEqual(m.status()['jobs'][0]['state'],'verified')
        self.assertFalse((base/'backup'/('.FieldLab-'+m.state['project_id']+'-B001')/'ledger.json').exists())
        resumed=Manager(base/'state',backend=b,clone=shutil.copy2);resumed.run_pending()
        ledger=json.loads((base/'backup'/('.FieldLab-'+m.state['project_id']+'-B001')/'ledger.json').read_text())
        self.assertEqual(ledger['jobs'][0]['state'],'verified')
        self.assertEqual(len(b.calls),1)
    def test_ui_status_poll_during_copy_cannot_lose_verified_transition(self):
        base,b,m=self.fixture();b.after_copy=lambda:m.status()
        m.run_pending()
        self.assertEqual(Manager(base/'state',backend=b,clone=shutil.copy2).status()['jobs'][0]['state'],'verified')
    def test_malformed_state_paths_fail_closed_without_outside_write(self):
        for field,value in [('relative','../outside'),('relative','/outside')]:
            with self.subTest(value=value):
                base,b,m=self.fixture();state=json.loads((base/'state/state.json').read_text())
                outside=base/'primary/outside';outside.mkdir();(outside/'video.mp4').write_bytes(b'footage'*100)
                state['jobs'][0][field]=value;(base/'state/state.json').write_text(json.dumps(state))
                with self.assertRaises(ValueError):Manager(base/'state',backend=b,clone=shutil.copy2).run_pending()
                self.assertFalse(b.calls)
    def test_malformed_manifest_paths_fail_closed(self):
        for key in ['../escape','/escape','x/../../escape']:
            with self.subTest(key=key):
                base,b,m=self.fixture();state=json.loads((base/'state/state.json').read_text())
                state['jobs'][0]['manifest']={key:{'bytes':0,'sha256':'0'*64}}
                (base/'state/state.json').write_text(json.dumps(state))
                with self.assertRaises(ValueError):Manager(base/'state',backend=b,clone=shutil.copy2).run_pending()
                self.assertFalse(b.calls)
    def test_sigkill_before_launch_and_after_copy_resume_without_false_success(self):
        for point in ['before_ccc','after_copy','verified_bytes']:
            with self.subTest(point=point):
                base,b,m=self.fixture()
                proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',str(base),point],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    ready,_,_=select.select([proc.stdout],[],[],15)
                    self.assertTrue(ready,'fault child did not reach checkpoint')
                    line=proc.stdout.readline().strip();self.assertEqual(line,'FAULT',proc.stderr.read() if proc.poll() is not None else line)
                    proc.kill();proc.wait(timeout=5)
                finally:
                    if proc.poll() is None:proc.kill();proc.wait(timeout=5)
                    proc.stdout.close();proc.stderr.close()
                resumed=Manager(base/'state',backend=b,clone=shutil.copy2)
                self.assertEqual(resumed.status()['jobs'][0]['state'],'copying')
                b.running=True
                with self.assertRaisesRegex(RuntimeError,'busy'):resumed.run_pending()
                self.assertFalse(b.calls)
                b.running=False;resumed.run_pending()
                self.assertEqual(resumed.status()['jobs'][0]['state'],'verified')
                self.assertEqual(len(b.calls),1 if point=='before_ccc' else 0)
                again=Manager(base/'state',backend=b,clone=shutil.copy2);again.run_pending()
                self.assertEqual(len(b.calls),1 if point=='before_ccc' else 0)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':
        import threading
        base=Path(sys.argv[2]);point=sys.argv[3];b=Backend(base)
        def pause():print('FAULT',flush=True);threading.Event().wait(60)
        m=Manager(base/'state',backend=b,clone=shutil.copy2,checkpoint=lambda stage:pause() if stage==point else None)
        if point=='after_copy':b.after_copy=pause
        m.run_pending()
    else:unittest.main()
