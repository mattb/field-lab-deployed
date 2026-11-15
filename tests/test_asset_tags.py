"""First-class tags: disposable SQLite fixtures, no media or production registry."""
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from ditnotes.project_store import ProjectStore,TagConflict
from ditnotes.metadata_api import MetadataAPI,handle_post

class AssetTagsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='asset-tags-');self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve();self.db=self.project/'_DIT_NOTES/project.sqlite3'
        # The host guard remains tested separately. Only these disposable
        # fixtures may run under CI's older system SQLite build.
        guard=patch('ditnotes.sqlite_safety.require_safe_sqlite');guard.start();self.addCleanup(guard.stop)
    def store(self):return ProjectStore(self.db,project_id='tag-fixture')
    def test_persist_normalize_deduplicate_and_stable_vocabulary_ids(self):
        with self.store() as s:
            result=s.set_asset_tags('clip-a',['  Sea   ice  ','INTERVIEW','sea ice','Ｋｅｅｐｅｒ'],0)
            self.assertEqual([r['name'] for r in result['tags']],['Sea ice','INTERVIEW','Keeper'])
            first={r['name']:r['id'] for r in result['tags']}
            self.assertEqual(result['revision'],1);self.assertEqual(s.edit_revision,1)
        with self.store() as s:
            self.assertEqual(s.asset_tags('clip-a'),result)
            other=s.set_asset_tags('clip-b',['SEA ICE','interview'],0)
            self.assertEqual(other['tags'],[{'id':first['Sea ice'],'name':'Sea ice'},{'id':first['INTERVIEW'],'name':'INTERVIEW'}])
            self.assertEqual(s.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_deletion_keeps_history_and_noop_does_not_reindex(self):
        with self.store() as s:
            before=s.set_asset_tags('clip',['B-roll'],0)
            no_op=s.set_asset_tags('clip',['b-roll','B-ROLL'],1)
            self.assertEqual(no_op,before);self.assertEqual(s.edit_revision,1)
            cleared=s.set_asset_tags('clip',[],1)
            self.assertFalse(cleared['tags']);self.assertEqual(cleared['revision'],2)
            self.assertEqual(cleared['vocabulary'],before['vocabulary'])
            restored=s.set_asset_tags('clip',['b-roll'],2)
            self.assertEqual(restored['tags'],before['tags'])
    def test_two_sessions_conflict_per_asset_without_lost_edits(self):
        with self.store() as first,self.store() as second:
            a=first.asset_tags('clip-a');b=second.asset_tags('clip-a')
            first.set_asset_tags('clip-a',['First window'],a['revision'])
            with self.assertRaises(TagConflict) as e:second.set_asset_tags('clip-a',['Second window'],b['revision'])
            self.assertEqual([r['name'] for r in e.exception.current['tags']],['First window'])
            second.set_asset_tags('clip-b',['Independent clip'],0)
            self.assertEqual(first.asset_tags('clip-a')['revision'],1)
            self.assertEqual(len(first.tag_vocabulary()),2)
    def test_failed_edit_rolls_back_vocab_associations_and_audit(self):
        with self.store() as s:
            before=s.set_asset_tags('clip',['Preserve'],0);revision=s.edit_revision
            with patch.object(s,'_audit',side_effect=OSError('simulated disk failure')):
                with self.assertRaises(OSError):s.set_asset_tags('clip',['Uncommitted'],1)
            self.assertEqual(s.asset_tags('clip'),before);self.assertEqual(s.edit_revision,revision)
            self.assertEqual(s.db.execute('SELECT count(*) FROM audit').fetchone()[0],1)
    def test_validation_is_bounded_and_no_partial_updates(self):
        with self.store() as s:
            before=s.set_asset_tags('clip',['Keep'],0)
            for value in [['Valid','bad,tag'],['line\nbreak'],['line\u2028break'],['control\x00'],[''],['x'*101],['x']*65,'not a list',[None],['\u200b']]:
                with self.subTest(value=repr(value)):
                    with self.assertRaises(ValueError):s.set_asset_tags('clip',value,1)
                    self.assertEqual(s.asset_tags('clip'),before)
            for base in [True,-1,None,'1']:
                with self.assertRaises(ValueError):s.set_asset_tags('clip',['Other'],base)
    def test_legacy_note_tags_suggest_without_promoting_or_changing_notes(self):
        with self.store() as s:
            note=s.add_annotation('clip','Human note',metadata={'tags':['  Field work  ','bad,tag','FIELD WORK',None]})
            before=s.annotations();view=s.asset_tags('clip')
            self.assertFalse(view['tags']);self.assertEqual([r['name'] for r in view['vocabulary']],['Field work'])
            suggested=view['vocabulary'][0]
            assigned=s.set_asset_tags('other',['field work'],0)
            self.assertEqual(assigned['tags'],[suggested]);self.assertEqual(s.annotations(),before)
            self.assertEqual(s.annotations()[0]['id'],note)
            self.assertFalse(s.asset_tags('clip')['tags'])
    def test_additive_schema_preserves_existing_identity_and_annotation(self):
        with self.store() as s:
            person=s.provisional_speaker('clip','speaker0',0,2)['person_id'];s.name_person(person,'Existing voice')
            note=s.add_annotation('clip','Existing text');before=s.people(),s.annotations()
        # Simulate the old release's database, without any tag tables.
        with sqlite3.connect(self.db) as c:
            for table in ['asset_tags','asset_tag_state','tag_vocabulary']:c.execute('DROP TABLE '+table)
        with self.store() as s:
            self.assertEqual((s.people(),s.annotations()),before)
            s.set_asset_tags('clip',['New concept'],0)
            self.assertEqual((s.people(),s.annotations()),before)
            self.assertEqual(s.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],'1')
    def api(self):
        catalog={'project':{'id':'tag-fixture'},'assets':[{'id':'asset','content_id':'clip'}]}
        api=object.__new__(MetadataAPI)
        api.server=types.SimpleNamespace(project=self.project,catalog=types.SimpleNamespace(read=lambda:catalog),search_backend=object())
        api.stop=threading.Event();api.changed=threading.Event();api.error=None
        return api
    def test_api_validates_known_assets_and_returns_tags_independently(self):
        api=self.api()
        with self.assertRaises(ValueError):api.asset_tags('unknown')
        with self.assertRaises(ValueError):api.mutate('/api/asset-tags',{'content_id':'unknown','tags':['No'],'base_revision':0})
        self.assertFalse(self.db.exists())
        self.assertEqual(api.asset_tags('clip')['revision'],0)
        result=api.mutate('/api/asset-tags',{'content_id':'clip','tags':['Interview'],'base_revision':0})
        self.assertTrue(api.changed.is_set());self.assertEqual(result['revision'],1)
        state=api.state();self.assertEqual(state['asset_tags'],[{'content_id':'clip',**result['tags'][0]}])
        self.assertEqual(state['tag_vocabulary'],result['vocabulary']);self.assertFalse(state['annotations'])
    def test_http_conflict_is_409_with_current_data_and_no_overwrite(self):
        api=self.api();api.mutate('/api/asset-tags',{'content_id':'clip','tags':['Saved'],'base_revision':0})
        class Handler:
            path='/api/asset-tags'
            server=types.SimpleNamespace(metadata=api,server_port=8765)
            def _json(self,value,head,code=200):self.result=(code,value)
            def send_error(self,code):self.result=(code,None)
        h=Handler();value=json.dumps({'content_id':'clip','tags':['Stale'],'base_revision':0}).encode()
        h.headers={'Host':'127.0.0.1:8765','Origin':'http://127.0.0.1:8765','Content-Type':'application/json','Content-Length':str(len(value))};h.rfile=io.BytesIO(value)
        self.assertTrue(handle_post(h));self.assertEqual(h.result[0],409)
        self.assertEqual([r['name'] for r in h.result[1]['current']['tags']],['Saved'])
        h.headers['Origin']='https://unrelated.example';h.rfile=io.BytesIO(value)
        self.assertTrue(handle_post(h));self.assertEqual(h.result[0],403)
    def test_tag_refresh_is_projection_only_without_enrichment_side_effects(self):
        api=self.api();api.mutate('/api/asset-tags',{'content_id':'clip','tags':['Operator tag'],'base_revision':0})
        calls=[];fake=types.ModuleType('ditnotes.enrichment')
        def publish(*args,**kwargs):calls.append((args,kwargs));api.stop.set()
        fake.publish_index=publish
        # No actual index/model import; verify the worker's production dispatch.
        with patch.dict(sys.modules,{'ditnotes.enrichment':fake}):api._loop()
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][1],{'projection_only':True})

if __name__=='__main__':unittest.main()
