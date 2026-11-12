"""Queue eligibility and pre-task source preparation, using disposable folders."""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from ditnotes.backup_rotation import Manager
from tests.test_backup_rotation_disaster import Backend,TASK

class DiscoveryTests(unittest.TestCase):
    def fixture(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);base=Path(t.name).resolve()
        project=base/'primary/project';project.mkdir(parents=True);(base/'backup').mkdir()
        card=project/'20261111/A001';card.mkdir(parents=True);(card/'clip.mp4').write_bytes(b'own disposable test')
        backend=Backend(base);m=Manager.initialize(base/'state',project,base/'primary/spool',backend=backend,clone=shutil.copy2)
        return base,project,card,backend,m
    def test_prepared_source_exists_for_ccc_picker_and_registers(self):
        base,project,card,b,m=self.fixture();result=m.prepare_set('B001')
        self.assertTrue(Path(result['source']).is_dir());self.assertFalse(list(Path(result['source']).iterdir()))
        self.assertEqual(m.prepare_set('B001'),result)
        m.add_set('B001',base/'backup','copy',TASK,dedicated_task_reviewed=True)
        m.register_card(card,'accepted');m.run_pending();self.assertEqual(m.status()['jobs'][0]['state'],'verified')
    def test_unmanaged_source_is_never_adopted(self):
        base,project,card,b,m=self.fixture();foreign=base/'primary/spool/B001/payload';foreign.mkdir(parents=True)
        (foreign/'keep').write_text('keep')
        with self.assertRaises(ValueError):m.prepare_set('B001')
        self.assertEqual((foreign/'keep').read_text(),'keep');self.assertFalse(m.state['sets']);self.assertFalse(b.calls)
    def test_only_matching_successful_accepted_queue_payloads_discover_once(self):
        base,project,card,b,m=self.fixture();q=base/'queue.sqlite3'
        good={'ingest_root':str(project),'volume_uuid':'primary-uuid','verification_mode':'Source & Destination',
              'job_id':'accepted','destination':str(card),'event':{'FileCopyCompleted_state':'Success'}}
        entries=[good,{**good,'job_id':'failed','event':{'FileCopyCompleted_state':'Failed'}},
                 {**good,'job_id':'wrong-volume','volume_uuid':'other'},
                 {**good,'job_id':'no-verification','verification_mode':'Transfer'},
                 {**good,'job_id':'other-project','ingest_root':str(base/'other')}]
        with sqlite3.connect(q) as c:
            c.execute('CREATE TABLE jobs(job_id TEXT,payload TEXT,enqueued_at REAL)')
            c.executemany('INSERT INTO jobs VALUES(?,?,?)',[(str(i),json.dumps(v),i) for i,v in enumerate(entries)])
        self.assertEqual(m.discover_queue(q),1);self.assertEqual(m.discover_queue(q),1)
        self.assertEqual(len(m.status()['jobs']),1);self.assertFalse(b.calls)
    def test_custom_state_inside_notes_is_rejected(self):
        base,project,card,b,m=self.fixture()
        with self.assertRaises(ValueError):Manager.initialize(project/'_DIT_NOTES/other-state',project,base/'primary/other-spool',backend=b,clone=shutil.copy2)

if __name__=='__main__':unittest.main()
