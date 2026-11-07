"""Controller lock collisions use temporary projects, real flocks, queue and HTTP."""
import contextlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import ingest_service as service
from ditnotes import library
from ditnotes.server import create_server
from ditnotes.project_retry import retry_project

class Engine:
    def __init__(self,**kwargs):self.calls=0
    def prewarm(self):return {}
    def close(self):pass
    def analyze(self,source,output):
        self.calls+=1
        (output/'result.json').write_text(json.dumps({'status':'complete','duration':1,'probe':{'streams':[]},'highlights':[],'tracks':[],'timing':{}}))

class ControllerContention(unittest.TestCase):
    def setUp(self):
        # The separate SQLite policy tests enforce the production version gate.
        # These disposable fixture databases test queue/lock behavior on CI Python.
        safety=patch('ditnotes.sqlite_safety.require_safe_sqlite');safety.start();self.addCleanup(safety.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'runtime').mkdir();self.project=self.root/'media';self.project.mkdir()
        self.scope=self.project/'card';self.scope.mkdir();(self.scope/'clip.mp4').write_bytes(b'disposable')
        self.c={'ingest_root':str(self.project),'queue_db':str(self.root/'runtime/queue.sqlite3')}
        with service.connect(self.c) as db:db.execute("INSERT INTO jobs(job_id,payload,enqueued_at) VALUES('job',?,0)",(json.dumps({'event':{}}),))
        self.stop=threading.Event();self.server=SimpleNamespace()
        self.ctl=service.Controller(self.c,self.server,self.stop)
        self.engine=Engine();self.waiting=threading.Event()
        original=self.ctl.waiting_for_project
        def waiting():original();self.waiting.set()
        self.ctl.waiting_for_project=waiting
        self.patches=[patch.object(service,'ROOT',self.root),patch('ditnotes.engine.Engine',return_value=self.engine),patch.object(service,'validate_event',return_value={'destination':str(self.scope)}),patch('ditnotes.offshoot_receipts.recover'),patch.object(service.subprocess,'Popen',return_value=Mock()),patch.object(self.ctl,'reconcile',return_value=None)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        # Exercise timeout/requeue transitions quickly, without changing defaults.
        self.ctl.project_call=lambda call:retry_project(call,timeout=.05,stop_event=self.stop,on_wait=self.ctl.waiting_for_project)
    def state(self):
        with service.connect(self.c) as db:return dict(db.execute("SELECT * FROM jobs WHERE job_id='job'").fetchone())
    def worker(self):
        thread=threading.Thread(target=self.ctl.loop);thread.start()
        self.addCleanup(lambda:(self.stop.set(),thread.join(3)))
        return thread
    def status_server(self):
        with patch('ditnotes.server.SearchBackend',return_value=Mock()),patch('ditnotes.server.MetadataAPI',return_value=Mock()):
            server=create_server(self.project,0,self.root)
        server.get_status=self.ctl.status
        thread=threading.Thread(target=server.serve_forever);thread.start()
        self.addCleanup(lambda:(server.shutdown(),thread.join(3),server.server_close()))
        return server
    def test_publication_collision_requeues_and_reuses_sealed_analysis(self):
        held=threading.Event();release=threading.Event();snapshots=[];results=[]
        def holder():
            with library._lock(self.project/'_DIT_NOTES'):held.set();release.wait(3)
        lock_thread=None
        def snapshot():
            nonlocal lock_thread
            snapshots.append(1)
            if len(snapshots)==1:
                lock_thread=threading.Thread(target=holder);lock_thread.start();self.assertTrue(held.wait(2))
            with library._lock(self.project/'_DIT_NOTES'):pass
            self.stop.set()
        self.ctl.snapshot=snapshot
        original=library.process
        def process(*args,**kwargs):
            result=original(*args,**kwargs);results.append(result['counts']);return result
        web=self.status_server()
        with patch.object(library,'process',side_effect=process):
            thread=self.worker();self.assertTrue(self.waiting.wait(3))
            self.assertEqual(self.state()['state'],'pending')
            client=http.client.HTTPConnection('127.0.0.1',web.server_port,timeout=2)
            client.request('GET','/api/status');response=client.getresponse();status=json.loads(response.read());client.close()
            self.assertEqual(response.status,200);self.assertEqual(status['phase'],'waiting_for_project');self.assertIsNone(status['last_error'])
            release.set();lock_thread.join(2);thread.join(4)
        self.assertFalse(thread.is_alive());self.assertEqual(self.state()['state'],'done')
        self.assertEqual(self.engine.calls,1);self.assertEqual(results[0]['processed'],1);self.assertEqual(results[-1]['reused'],1)
    def test_persistent_entry_collision_requeues_and_shutdown_is_prompt(self):
        self.ctl.snapshot=Mock()
        with library._lock(self.project/'_DIT_NOTES'):
            thread=self.worker();self.assertTrue(self.waiting.wait(2))
            deadline=time.monotonic()+2
            while self.state()['state']!='pending' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(self.state()['state'],'pending');self.assertIsNone(self.state()['last_error'])
            started=time.monotonic();self.stop.set();thread.join(1)
            self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-started,1)
        self.assertEqual(self.engine.calls,0)
    def test_reconcile_timeout_keeps_worker_alive_until_stop(self):
        def reconcile():
            self.ctl.project_call(lambda:self.take_lock())
        self.ctl.reconcile=reconcile
        with library._lock(self.project/'_DIT_NOTES'):
            thread=self.worker();self.assertTrue(self.waiting.wait(2));time.sleep(.12)
            self.assertTrue(thread.is_alive());self.assertEqual(self.ctl.status()['phase'],'waiting_for_project')
            self.stop.set();thread.join(1);self.assertFalse(thread.is_alive())
    def test_requested_reconcile_retains_request_until_lock_recovers(self):
        request=self.root/'runtime/reconcile.request';request.write_text('requested')
        calls=[]
        def reconcile():
            calls.append(1)
            if len(calls)>1:self.ctl.project_call(self.take_lock)
        self.ctl.reconcile=reconcile;self.ctl.snapshot=lambda:self.stop.set()
        with library._lock(self.project/'_DIT_NOTES'):
            thread=self.worker();self.assertTrue(self.waiting.wait(2))
            self.assertTrue(request.exists());self.assertTrue(thread.is_alive())
        thread.join(4)
        self.assertFalse(thread.is_alive());self.assertFalse(request.exists());self.assertEqual(self.state()['state'],'done')

    def test_failed_job_summary_uses_scoped_cause_without_external_paths(self):
        result={'counts':{'failed':1},'failures':[
            {'path':'other-card/other.mp4','stage':'asr','error':'unrelated failure'},
            {'path':'../private.mp4','stage':'asr','error':'unsafe path'},
            {'path':'card/clip.mp4','stage':'vlm','error':'worker failed at /Users/private/hidden.mp4'},
        ]}
        publish=self.ctl.publish
        def stop_after_error(phase=None,active=None,error=None):
            publish(phase,active,error)
            if phase=='error':self.stop.set()
        self.ctl.publish=stop_after_error;self.ctl.snapshot=Mock()
        with patch.object(library,'process',return_value=result):
            thread=self.worker();thread.join(3)
        self.assertFalse(thread.is_alive());row=self.state();self.assertEqual(row['state'],'error')
        self.assertIn('1 media file(s) failed; completed files are retained',row['last_error'])
        self.assertIn('card/clip.mp4 [vlm]: worker failed',row['last_error'])
        self.assertNotIn('unrelated failure',row['last_error']);self.assertNotIn('unsafe path',row['last_error']);self.assertNotIn('/Users/private',row['last_error'])
        self.assertEqual(self.ctl.status()['last_error'],row['last_error']);self.ctl.snapshot.assert_not_called()
        # Older process results without structured diagnostics keep the original fallback.
        self.stop.clear()
        with service.connect(self.c) as db:db.execute("UPDATE jobs SET state='pending',last_error=NULL WHERE job_id='job'")
        with patch.object(library,'process',return_value={'counts':{'failed':1}}):
            thread=self.worker();thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.state()['last_error'],'1 media file(s) failed; completed files are retained')

    def take_lock(self):
        with library._lock(self.project/'_DIT_NOTES'):return True

if __name__=='__main__':unittest.main()
