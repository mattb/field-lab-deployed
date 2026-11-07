"""Explicit clip-face labels are atomic operator edits, never voice inference."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from ditnotes.project_store import ProjectStore
from ditnotes.face_labels import label_clip_faces

class FaceLabels(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        safety=patch('ditnotes.sqlite_safety.require_safe_sqlite');safety.start();self.addCleanup(safety.stop)
        self.path=Path(self.tmp.name)/'project.sqlite3';self.store=ProjectStore(self.path,'test');self.addCleanup(self.store.close)
    def face(self,clip,seconds,*,detection='0'):
        return self.store.assign_face(clip,seconds,[1,0],'fixture',1,bbox=[.1,.1,.4,.4],detection_id=detection)['face_id']
    def apply(self,mapping,**kwargs):return label_clip_faces(self.store,mapping,reason='Explicit operator clip assertion',**kwargs)
    def named(self):return {o['content_id']:next(f['name'] for f in self.store.faces() if f['id']==o['face_id']) for o in self.store.face_observations()}
    def snapshot(self):return {table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in ['faces','face_observations','face_profiles','face_voice_links','face_feedback','audit','meta']}
    def test_fragments_merge_with_raw_evidence_audit_and_no_voice_assignment(self):
        a=self.face('clip-a',1);b=self.face('clip-a',2)
        raw=[tuple(r) for r in self.store.db.execute('SELECT id,embedding,bbox,frame_ref FROM face_observations ORDER BY id')]
        result=self.apply({'clip-a':'Person A'})
        self.assertEqual(result['merges'],1);self.assertEqual(self.store.canonical_face(a),self.store.canonical_face(b));self.assertEqual(self.named(),{'clip-a':'Person A'})
        self.assertEqual(raw,[tuple(r) for r in self.store.db.execute('SELECT id,embedding,bbox,frame_ref FROM face_observations ORDER BY id')])
        self.assertEqual(self.store.people(),[]);self.assertEqual(list(self.store.db.execute('SELECT * FROM face_voice_links')),[])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM audit WHERE operation='clip_faces_labeled'").fetchone()[0],1)
    def test_mixed_group_splits_labels_and_preserves_unmapped_group(self):
        a=self.face('clip-a',1);b=self.face('clip-b',2);unknown=self.face('unmapped',3)
        self.store.merge_faces(b,a,'fixture grouping');self.store.merge_faces(unknown,a,'fixture grouping')
        self.apply({'clip-a':'Person A','clip-b':'Person B'})
        self.assertEqual(self.named(),{'clip-a':'Person A','clip-b':'Person B','unmapped':None})
        rows=self.store.face_observations();self.assertEqual(len({r['face_id'] for r in rows}),3)
        self.assertEqual(next(r['face_id'] for r in rows if r['content_id']=='unmapped'),a)
    def test_failure_rolls_back_all_names_merges_and_audits(self):
        for clip in ['clip-a','clip-b']:
            self.face(clip,1);self.face(clip,2)
        before=self.snapshot();audit=self.store._audit
        def fail(operation,payload):
            audit(operation,payload)
            if operation=='clip_faces_labeled':raise RuntimeError('simulated interruption')
        with patch.object(self.store,'_audit',side_effect=fail),self.assertRaisesRegex(RuntimeError,'interruption'):
            self.apply({'clip-a':'Person A','clip-b':'Person B'})
        self.assertEqual(self.snapshot(),before)
    def test_outer_transaction_rollback_includes_completed_bulk_call(self):
        self.face('clip-a',1);self.face('clip-a',2);before=self.snapshot()
        with self.assertRaises(KeyboardInterrupt):
            with self.store._transaction():
                self.apply({'clip-a':'Person A'});raise KeyboardInterrupt()
        self.assertEqual(self.snapshot(),before)
    def test_concurrent_reader_sees_no_half_labeled_state(self):
        self.face('clip-a',1);self.face('clip-b',2)
        halfway=threading.Event();release=threading.Event();errors=[]
        def writer():
            try:
                with ProjectStore(self.path,'test') as store:
                    original=store._audit
                    def pause(operation,payload):
                        original(operation,payload)
                        if operation=='face_named' and not halfway.is_set():halfway.set();release.wait(3)
                    with patch.object(store,'_audit',side_effect=pause):label_clip_faces(store,{'clip-a':'Person A','clip-b':'Person B'},reason='Operator assertion')
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=writer);thread.start()
        try:
            self.assertTrue(halfway.wait(2));self.assertEqual(self.named(),{'clip-a':None,'clip-b':None})
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(self.named(),{'clip-a':'Person A','clip-b':'Person B'})
    def test_existing_face_name_requires_explicit_override(self):
        face=self.face('clip-a',1);self.store.name_face(face,'Prior name');before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'override'):self.apply({'clip-a':'Person A'})
        self.assertEqual(self.snapshot(),before)
        self.apply({'clip-a':'Person A'},override_existing_names=True);self.assertEqual(self.named(),{'clip-a':'Person A'})
    def test_same_frame_distinct_faces_are_never_collapsed(self):
        self.face('clip-a',1);self.face('clip-a',1,detection='1');before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'distinct faces'):self.apply({'clip-a':'Person A'})
        self.assertEqual(self.snapshot(),before)
    def test_negative_review_is_not_overridden(self):
        a=self.face('clip-a',1);b=self.face('clip-a',2);self.store.review_face_matching('different',a,b);before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'different'):self.apply({'clip-a':'Person A'},override_existing_names=True)
        self.assertEqual(self.snapshot(),before)
    def test_existing_unnamed_voice_link_is_preserved_without_naming_voice(self):
        face=self.face('clip-a',1);voice=self.store.provisional_speaker('clip-a','speaker',0,4)['person_id']
        self.store.link_face_voice(face,voice,'Operator reviewed link')
        self.apply({'clip-a':'Person A'})
        self.assertIsNone(next(p['name'] for p in self.store.people() if p['id']==voice))
        self.assertEqual(self.store.db.execute('SELECT person_id FROM face_voice_links WHERE face_id=?',(face,)).fetchone()[0],voice)
    def test_conflicting_linked_voice_name_is_not_overridden(self):
        face=self.face('clip-a',1);voice=self.store.provisional_speaker('clip-a','speaker',0,4)['person_id']
        self.store.link_face_voice(face,voice,'Operator reviewed link');self.store.name_person(voice,'Prior voice name');before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'voice name'):self.apply({'clip-a':'Person A'},override_existing_names=True)
        self.assertEqual(self.snapshot(),before)
    def test_conflicting_voice_links_block_merge_even_with_name_override(self):
        for seconds in [1,2]:
            face=self.face('clip-a',seconds);voice=self.store.provisional_speaker('clip-a',str(seconds),seconds*5,seconds*5+4)['person_id']
            self.store.link_face_voice(face,voice,'Operator reviewed link')
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'voice links'):self.apply({'clip-a':'Person A'},override_existing_names=True)
        self.assertEqual(self.snapshot(),before)
    def test_nested_savepoint_failure_can_be_caught_without_rolling_back_outer_edit(self):
        a=self.face('clip-a',1);b=self.face('clip-b',2)
        with self.store._transaction():
            self.store.name_face(a,'Person A')
            try:
                with self.store._transaction():
                    self.store.name_face(b,'Temporary');raise ValueError('rollback nested edit')
            except ValueError:pass
        self.assertEqual(self.named(),{'clip-a':'Person A','clip-b':None})
    def test_override_requires_boolean(self):
        self.face('clip-a',1)
        with self.assertRaisesRegex(ValueError,'boolean'):self.apply({'clip-a':'Person A'},override_existing_names='false')

    def test_split_does_not_bypass_parent_different_person_feedback(self):
        mixed=self.face('clip-a',1);unknown=self.face('unmapped',2);other=self.face('clip-b',3)
        self.store.merge_faces(unknown,mixed,'Fixture mixed group');self.store.review_face_matching('different',mixed,other);before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'different'):
            self.apply({'clip-a':'Person A','clip-b':'Person A'})
        self.assertEqual(self.snapshot(),before)
    def test_split_does_not_bypass_parent_conflicting_voice_links(self):
        mixed=self.face('clip-a',1);unknown=self.face('unmapped',2);other=self.face('clip-b',3)
        self.store.merge_faces(unknown,mixed,'Fixture mixed group')
        for face,clip in [(mixed,'clip-a'),(other,'clip-b')]:
            voice=self.store.provisional_speaker(clip,'speaker',0,4)['person_id'];self.store.link_face_voice(face,voice,'Operator reviewed link')
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'voice links'):
            self.apply({'clip-a':'Person A','clip-b':'Person A'})
        self.assertEqual(self.snapshot(),before)

    def test_split_preserves_existing_named_anchor_id_over_alphabetical_order(self):
        anchor=self.face('clip-z',1);mistaken=self.face('clip-a',2)
        self.store.merge_faces(mistaken,anchor,'Fixture earlier mistaken grouping');self.store.name_face(anchor,'Person Z')
        self.apply({'clip-z':'Person Z','clip-a':'Person A'},override_existing_names=True)
        result={o['content_id']:o['face_id'] for o in self.store.face_observations()}
        self.assertEqual(result['clip-z'],anchor);self.assertNotEqual(result['clip-a'],anchor)
        self.assertEqual(self.named(),{'clip-z':'Person Z','clip-a':'Person A'})
    def test_named_anchor_and_unmapped_observations_keep_their_original_id(self):
        anchor=self.face('clip-z',1);mistaken=self.face('clip-a',2);unknown=self.face('unmapped',3)
        self.store.merge_faces(mistaken,anchor,'Fixture earlier grouping');self.store.merge_faces(unknown,anchor,'Fixture earlier grouping');self.store.name_face(anchor,'Person Z')
        self.apply({'clip-z':'Person Z','clip-a':'Person A'},override_existing_names=True)
        result={o['content_id']:o['face_id'] for o in self.store.face_observations()}
        self.assertEqual(result['clip-z'],anchor);self.assertEqual(result['unmapped'],anchor);self.assertNotEqual(result['clip-a'],anchor)
        self.assertEqual(self.named()['unmapped'],'Person Z')

    def test_earlier_anonymous_fragment_does_not_replace_named_anchor(self):
        fragment=self.face('clip-z',0);anchor=self.face('clip-z',10);mistaken=self.face('clip-a',20)
        self.store.merge_faces(mistaken,anchor,'Fixture earlier mistaken grouping');self.store.name_face(anchor,'Person Z')
        self.apply({'clip-z':'Person Z','clip-a':'Person A'},override_existing_names=True)
        self.assertEqual(self.store.canonical_face(anchor),anchor);self.assertEqual(self.store.canonical_face(fragment),anchor)
        self.assertEqual(self.named(),{'clip-z':'Person Z','clip-a':'Person A'})

    def test_missing_clip_observations_fail_without_partial_labels(self):
        self.face('clip-a',1);before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'No face observations'):self.apply({'clip-a':'Person A','missing':'Person B'})
        self.assertEqual(self.snapshot(),before)

if __name__=='__main__':unittest.main()
