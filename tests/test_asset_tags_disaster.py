"""Independent asset-tag failure tests; disposable SQLite fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ditnotes.project_store import ProjectStore


def child(db,point,base):
    db=Path(db)
    if db.name!='registry.sqlite3' or not db.parent.name.startswith('asset-tags-disaster-'):
        raise ValueError('Only generated test fixtures may be opened')
    # Only this single-process disposable fixture bypasses host SQLite policy;
    # pinned-runtime/production-policy tests remain independent and unchanged.
    with patch('ditnotes.sqlite_safety.require_safe_sqlite'),ProjectStore(db,'tag-fixture') as store:
        if point=='mid':
            audit=store._audit
            def paused(*args,**kwargs):
                audit(*args,**kwargs)
                print('MID',flush=True);threading.Event().wait(60)
            store._audit=paused
        store.set_asset_tags('clip-a',['Replacement','New topic'],base)
        print('COMMITTED',flush=True);threading.Event().wait(60)


class AssetTagDisasters(unittest.TestCase):
    def setUp(self):
        safety=patch('ditnotes.sqlite_safety.require_safe_sqlite');safety.start();self.addCleanup(safety.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='asset-tags-disaster-');self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'registry.sqlite3'
        with ProjectStore(self.db,'tag-fixture') as store:
            store.add_annotation('clip-a','Keep this human note',metadata={'tags':['Legacy note topic']})
            store.set_asset_tags('clip-a',['Original'],0)
    def view(self):
        with ProjectStore(self.db,'tag-fixture') as store:
            return {'tags':store.asset_tags('clip-a'),'notes':store.annotations(),'revision':store.edit_revision,'audit':store.audit()}
    def integrity(self):
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertFalse(connection.execute('PRAGMA foreign_key_check').fetchall())
    def interrupt(self,point,base):
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',str(self.db),point,str(base)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ready,_,_=select.select([process.stdout],[],[],15)
            self.assertTrue(ready,'Child did not reach the requested durable boundary')
            line=process.stdout.readline().strip()
            self.assertEqual(line,'MID' if point=='mid' else 'COMMITTED',process.stderr.read() if process.poll() is not None else line)
            process.kill();process.wait(timeout=5)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close();process.stderr.close()
    def test_sigkill_before_commit_rolls_back_tags_vocabulary_audit_and_revision(self):
        before=self.view();self.interrupt('mid',before['tags']['revision'])
        self.assertEqual(self.view(),before);self.integrity()
    def test_sigkill_after_commit_preserves_full_replacement_with_open_wal(self):
        before=self.view();self.interrupt('committed',before['tags']['revision']);after=self.view()
        self.assertEqual([t['name'] for t in after['tags']['tags']],['Replacement','New topic'])
        self.assertEqual(after['tags']['revision'],before['tags']['revision']+1)
        self.assertEqual(after['revision'],before['revision']+1)
        self.assertEqual(len(after['audit']),len(before['audit'])+1)
        self.assertEqual(after['notes'],before['notes']);self.integrity()
    def test_audit_failure_never_leaks_new_vocabulary_or_removal(self):
        before=self.view()
        with ProjectStore(self.db,'tag-fixture') as store,patch.object(store,'_audit',side_effect=OSError('simulated persistence failure')):
            with self.assertRaises(OSError):store.set_asset_tags('clip-a',['Never committed'],before['tags']['revision'])
        self.assertEqual(self.view(),before);self.integrity()
    def test_error_after_commit_is_reconciled_without_reapplying_or_losing_edit(self):
        before=self.view()
        with ProjectStore(self.db,'tag-fixture') as store:
            with patch('ditnotes.project_store.fsync_dir',side_effect=OSError('directory sync failed after commit')):
                with self.assertRaises(OSError):store.set_asset_tags('clip-a',['Committed but unacknowledged'],before['tags']['revision'])
        current=self.view()
        self.assertEqual([t['name'] for t in current['tags']['tags']],['Committed but unacknowledged'])
        self.assertEqual(current['revision'],before['revision']+1)
        with ProjectStore(self.db,'tag-fixture') as store:
            with self.assertRaises(ValueError) as caught:store.set_asset_tags('clip-a',['Committed but unacknowledged'],before['tags']['revision'])
            self.assertEqual(type(caught.exception).__name__,'TagConflict')
            self.assertEqual(caught.exception.current['tags'],current['tags']['tags'])
        self.assertEqual(self.view(),current);self.integrity()
    def test_concurrent_independent_writers_cannot_silently_overwrite(self):
        barrier=threading.Barrier(2);base=self.view()['tags']['revision']
        def writer(name):
            with ProjectStore(self.db,'tag-fixture') as store:
                barrier.wait(timeout=10)
                try:return ('saved',store.set_asset_tags('clip-a',[name],base))
                except ValueError as error:
                    if type(error).__name__!='TagConflict':raise
                    return ('conflict',error.current)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(writer,['First writer','Second writer']))
        self.assertEqual(sorted(x[0] for x in results),['conflict','saved'])
        current=self.view()['tags'];self.assertEqual(current['revision'],base+1)
        self.assertEqual(next(v for key,v in results if key=='conflict')['tags'],current['tags'])
        self.integrity()
    def test_note_tags_only_seed_suggestions_and_survive_asset_clear(self):
        before=self.view();legacy=next(t for t in before['tags']['vocabulary'] if t['name']=='Legacy note topic')
        self.assertNotIn(legacy['id'],[t['id'] for t in before['tags']['tags']])
        with ProjectStore(self.db,'tag-fixture') as store:
            cleared=store.set_asset_tags('clip-a',[],before['tags']['revision'])
            self.assertFalse(cleared['tags'])
            self.assertIn('Original',[t['name'] for t in cleared['vocabulary']])
            self.assertEqual(store.annotations(),before['notes'])
            assigned=store.set_asset_tags('clip-b',['legacy note TOPIC'],0)
            self.assertEqual(assigned['tags'][0]['id'],legacy['id'])
        self.integrity()
    def test_unicode_canonical_equivalents_share_one_project_tag(self):
        with ProjectStore(self.db,'tag-fixture') as store:
            a=store.set_asset_tags('clip-b',['  CAFE\u0301  ','ＣＡＦÉ'],0)
            b=store.set_asset_tags('clip-c',['café'],0)
            self.assertEqual(len(a['tags']),1);self.assertEqual(a['tags'][0]['id'],b['tags'][0]['id'])
            self.assertEqual(a['tags'][0]['name'],b['tags'][0]['name'])


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':child(sys.argv[2],sys.argv[3],int(sys.argv[4]))
    else:unittest.main()
