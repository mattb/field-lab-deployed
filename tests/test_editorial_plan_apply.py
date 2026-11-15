import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from development.apply_editorial_plan import apply_plan
from ditnotes.project_store import ProjectStore
from ditnotes.premiere_bridge import build_snapshot


class EditorialApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        notes = self.root / '_DIT_NOTES'; notes.mkdir()
        (self.root / 'clip.mp4').write_bytes(b'fixture')
        self.sentences = [{'start': 1, 'end': 4, 'text': 'A recorded explanation.'}]
        self.catalog = {'project': {'id': 'project'}, 'assets': [{'id': 'asset', 'content_id': 'sha',
            'relative_path': 'clip.mp4', 'name': 'clip.mp4', 'duration': 10, 'transcript': self.sentences}]}
        (notes / 'catalog.json').write_text(json.dumps(self.catalog))
        self.db = notes / 'project.sqlite3'
        self.row = {'asset_id': 'asset', 'content_id': 'sha', 'relative_path': 'clip.mp4', 'origin': 'ai_suggested',
            'transcript_sha256': hashlib.sha256(json.dumps(self.sentences, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'annotation_key': hashlib.sha256(b'project:sha:prompt-v1').hexdigest(), 'tags': ['Interview', 'Method'],
            'summary': 'Explains the recording method.', 'passages': [{'sentence_start': 0, 'sentence_end': 0,
                'start': 1, 'end': 4, 'quote': 'A recorded explanation.', 'note': 'Useful explanation.'}]}
        self.plan = {'schema_version': 1, 'project_id': 'project', 'origin': 'ai_suggested',
            'review_state': 'reviewed_for_trial', 'model': 'local-model', 'prompt_version': 'prompt-v1', 'assets': [self.row]}
        with ProjectStore(self.db, project_id='project') as store:
            self.human = store.add_annotation('sha', 'Keep this human note')
            store.set_asset_tags('sha', ['Existing'], 0)

    def tearDown(self):
        self.temp.cleanup()

    def test_replay_and_extended_plan_preserve_operator_edits_and_provenance(self):
        first = apply_plan(self.root, self.plan)
        with ProjectStore(self.db) as store:
            notes = store.annotations('sha')
            self.assertEqual(notes[0]['id'], self.human)
            self.assertEqual([n['source'] for n in notes], ['operator', 'editorial_suggestion', 'editorial_suggestion'])
            state = store.asset_tags('sha')
            self.assertEqual([t['name'] for t in state['tags']], ['Existing', 'Interview', 'Method'])
            store.set_asset_tags('sha', ['Operator revision'], state['revision'])
            before = store.edit_revision
            projected = build_snapshot(self.root, self.catalog, store)['assets'][0]
            self.assertEqual(projected['markers'][0]['source'], 'editorial_suggestion')
            self.assertTrue(projected['markers'][0]['comments'].startswith('AI editorial suggestion:'))
        self.assertTrue(apply_plan(self.root, self.plan)['replayed'])
        extended = copy.deepcopy(self.plan); extended['review_notes'] = ['Additional review context']
        apply_plan(self.root, extended)
        with ProjectStore(self.db) as store:
            self.assertEqual(len(store.annotations()), 3)
            self.assertEqual([t['name'] for t in store.asset_tags('sha')['tags']], ['Operator revision'])
            self.assertGreaterEqual(store.edit_revision, before)
            self.assertIn('"plan":', store.db.execute("SELECT payload FROM audit WHERE operation='editorial_plan_applied' LIMIT 1").fetchone()[0])
        self.assertEqual(first['annotation_ids'], apply_plan(self.root, self.plan)['annotation_ids'])

    def test_interruption_after_notes_rolls_back_notes_tags_and_receipts(self):
        with patch.object(ProjectStore, 'set_asset_tags', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError): apply_plan(self.root, self.plan)
        with ProjectStore(self.db) as store:
            self.assertEqual(len(store.annotations()), 1)
            self.assertEqual([t['name'] for t in store.asset_tags('sha')['tags']], ['Existing'])
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'editorial_%'").fetchone()[0], 0)
        self.assertFalse(apply_plan(self.root, self.plan)['replayed'])

    def test_stale_or_invented_evidence_and_bad_provenance_never_write(self):
        for field, value in [('transcript_sha256', 'wrong'), ('annotation_key', '0' * 64)]:
            plan = copy.deepcopy(self.plan); plan['assets'][0][field] = value
            with self.assertRaises(ValueError): apply_plan(self.root, plan)
        plan = copy.deepcopy(self.plan); plan['assets'][0]['passages'][0]['quote'] = 'Invented words'
        with self.assertRaises(ValueError): apply_plan(self.root, plan)
        plan = copy.deepcopy(self.plan); plan['model'] = ''
        with self.assertRaises(ValueError): apply_plan(self.root, plan)
        with ProjectStore(self.db) as store: self.assertEqual(len(store.annotations()), 1)

    def test_duplicate_tag_union_and_conflicting_stable_note_id(self):
        with ProjectStore(self.db) as store:
            store.set_asset_tags('sha', ['Tag ' + str(i) for i in range(40)], 1)
        self.plan['assets'][0]['tags'] = ['Tag ' + str(i) for i in range(30)]
        apply_plan(self.root, self.plan)
        changed = copy.deepcopy(self.plan); changed['assets'][0]['summary'] = 'Changed suggestion'
        with self.assertRaisesRegex(ValueError, 'different evidence'): apply_plan(self.root, changed)
        with ProjectStore(self.db) as store:
            self.assertEqual(len(store.annotations()), 3)
            self.assertEqual(len(store.asset_tags('sha')['tags']), 40)

    def test_new_generator_vocabulary_bound_identity(self):
        vocabulary = hashlib.sha256(b'fixture vocabulary').hexdigest()
        self.plan['vocabulary_sha256'] = vocabulary
        self.plan['assets'][0]['annotation_key'] = hashlib.sha256(('project:sha:prompt-v1:' + vocabulary).encode()).hexdigest()
        self.assertFalse(apply_plan(self.root, self.plan)['replayed'])
        bad = copy.deepcopy(self.plan); bad['vocabulary_sha256'] = 'bad'
        with self.assertRaises(ValueError): apply_plan(self.root, bad)
