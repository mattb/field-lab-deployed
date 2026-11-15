"""Independent identity-migration preservation and interruption acceptance tests."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from ditnotes.project_store import ProjectStore


class UnifiedPeopleSafetyReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'project.sqlite3'
        self.store = ProjectStore(self.path, 'safety-fixture')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def voice(self, content, name=None):
        row = self.store.assign_speaker(content, 'speaker', 0, 4, [1, 0], 'voice-fixture', 1)
        if name:
            with self.store._transaction():
                self.store.db.execute('UPDATE people SET name=? WHERE id=?', (name, row['person_id']))
        return row['person_id']

    def face(self, content, name=None):
        row = self.store.assign_face(content, 2, [1, 0], 'face-fixture', 1, bbox=[.1, .1, .4, .4], auto_assign=False)
        if name:
            with self.store._transaction():
                self.store.db.execute('UPDATE faces SET name=? WHERE id=?', (name, row['face_id']))
        return row['face_id']

    def legacy_link(self, face, voice):
        # Represent historical assertions exactly, including inconsistent old labels.
        with self.store._transaction():
            self.store.db.execute('INSERT INTO face_voice_links VALUES(?,?,?,?)', (face, voice, 'Historical reviewed link', 123.0))

    def legacy_snapshot(self):
        tables = ('people', 'observations', 'clusters', 'profiles', 'faces', 'face_observations', 'face_profiles', 'face_voice_links', 'embedding_models')
        return {table: [tuple(row) for row in self.store.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables}

    def reopen(self):
        self.store.close()
        self.store = ProjectStore(self.path, 'safety-fixture')

    def test_migration_preserves_legacy_evidence_and_equal_names_never_merge(self):
        face = self.face('clip-a', 'Alex')
        voice = self.voice('clip-b', 'Alex')
        before = self.legacy_snapshot()
        audit_before = [tuple(row) for row in self.store.db.execute('SELECT * FROM audit ORDER BY id')]
        self.store.ensure_unified_people()
        self.assertNotEqual(self.store.person_for_evidence('face', face), self.store.person_for_evidence('voice', voice))
        first = self.store.unified_people()
        self.assertEqual(len(first), 2)
        self.assertEqual(self.legacy_snapshot(), before)
        self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM audit ORDER BY id')][:len(audit_before)], audit_before)
        self.reopen()
        self.store.ensure_unified_people()
        self.assertEqual(self.store.unified_people(), first)
        self.assertEqual(self.legacy_snapshot(), before)

    def test_only_explicit_link_unifies_modalities_and_conflicting_names_survive(self):
        face = self.face('visible-only', 'Face label')
        voice = self.voice('heard-only', 'Voice label')
        other = self.face('unlinked', 'Face label')
        self.legacy_link(face, voice)
        before = self.legacy_snapshot()
        self.store.ensure_unified_people()
        person = self.store.person_for_evidence('face', face)
        self.assertEqual(person, self.store.person_for_evidence('voice', voice))
        self.assertNotEqual(person, self.store.person_for_evidence('face', other))
        row = next(p for p in self.store.unified_people() if p['id'] == person)
        self.assertTrue(row['name_conflict'])
        self.assertIsNone(row['name'])
        self.assertEqual({a['name'] for a in row['aliases']}, {'Face label', 'Voice label'})
        self.assertEqual(self.legacy_snapshot(), before)

    def test_killed_migration_retries_without_partial_identity_assignments(self):
        self.face('one', 'One')
        self.face('two', 'Two')
        self.voice('three', 'Three')
        before = self.legacy_snapshot()
        self.store.close()
        script = '''
import os, signal, sys
# Disposable contract fixture: subprocess does not inherit the CI parent mock.
from unittest.mock import patch
patch('ditnotes.sqlite_safety.require_safe_sqlite').start()
from ditnotes.project_store import ProjectStore
s=ProjectStore(sys.argv[1], 'safety-fixture')
n=[0]
def trace(sql):
    if sql.lstrip().upper().startswith('INSERT') and 'unified_' in sql.lower():
        n[0]+=1
        if n[0]==2: os.kill(os.getpid(), signal.SIGKILL)
s.db.set_trace_callback(trace)
s.ensure_unified_people()
s.close()
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.store = ProjectStore(self.path, 'safety-fixture')
        self.assertEqual(self.legacy_snapshot(), before)
        for row in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'unified_%'"):
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM '+row[0]).fetchone()[0], 0, row[0])
        self.store.ensure_unified_people()
        people = self.store.unified_people()
        self.assertEqual(len(people), 3)
        self.assertEqual(sum(len(p['members']) for p in people), 3)
        self.reopen()
        self.assertEqual(self.store.unified_people(), people)

    def test_detached_face_is_not_reattached_by_historical_link_after_reopen(self):
        face = self.face('visible', 'Alex')
        voice = self.voice('heard', 'Alex')
        self.legacy_link(face, voice)
        self.store.ensure_unified_people()
        together = self.store.person_for_evidence('face', face)
        self.assertEqual(together, self.store.person_for_evidence('voice', voice))
        before = self.legacy_snapshot()
        self.store.detach_person_evidence(together, 'face', face, 'Operator reviewed a wrong match')
        separate = self.store.person_for_evidence('face', face)
        self.assertNotEqual(together, separate)
        self.assertEqual(self.store.person_for_evidence('voice', voice), together)
        self.assertEqual(self.legacy_snapshot(), before)
        self.reopen()
        self.store.ensure_unified_people()
        self.assertEqual(self.store.person_for_evidence('face', face), separate)
        self.assertEqual(self.store.person_for_evidence('voice', voice), together)
        self.assertEqual(self.legacy_snapshot(), before)

    def test_new_clusters_after_migration_map_once_without_name_based_union(self):
        old_face = self.face('initial', 'Alex')
        self.store.ensure_unified_people()
        original = self.store.person_for_evidence('face', old_face)
        new_face = self.face('later-visual', 'Alex')
        new_voice = self.voice('later-audio', 'Alex')
        self.store.ensure_unified_people()
        ids = {original, self.store.person_for_evidence('face', new_face), self.store.person_for_evidence('voice', new_voice)}
        self.assertEqual(len(ids), 3)
        self.assertEqual(sum(len(p['members']) for p in self.store.unified_people()), 3)
        self.reopen()
        self.store.ensure_unified_people()
        self.assertEqual(len(self.store.unified_people()), 3)
        self.assertEqual(sum(len(p['members']) for p in self.store.unified_people()), 3)

    def test_detach_and_undo_survive_reopen_without_changing_legacy_embeddings(self):
        face = self.face('visible', 'Alex')
        voice = self.voice('heard', 'Alex')
        self.legacy_link(face, voice)
        self.store.ensure_unified_people()
        person = self.store.person_for_evidence('face', face)
        before = self.legacy_snapshot()
        change = self.store.detach_person_evidence(person, 'voice', voice, 'Wrong voice match')
        detached = self.store.person_for_evidence('voice', voice)
        self.reopen()
        self.assertEqual(self.store.person_for_evidence('voice', voice), detached)
        self.store.undo_person_edit(change['event_id'])
        self.reopen()
        self.assertEqual(self.store.person_for_evidence('voice', voice), person)
        self.assertEqual(self.store.person_for_evidence('face', face), person)
        self.assertEqual(self.legacy_snapshot(), before)
        with self.assertRaisesRegex(ValueError, 'already undone'):
            self.store.undo_person_edit(change['event_id'])

    def test_conflict_resolution_changes_one_name_and_retains_both_aliases(self):
        face = self.face('visible', 'First name')
        voice = self.voice('heard', 'Second name')
        self.legacy_link(face, voice)
        self.store.ensure_unified_people()
        person = self.store.person_for_evidence('face', face)
        before = self.legacy_snapshot()
        event = self.store.rename_unified_person(person, 'Reviewed name')
        self.reopen()
        row = next(p for p in self.store.unified_people() if p['id'] == person)
        self.assertEqual(row['name'], 'Reviewed name')
        self.assertFalse(row['name_conflict'])
        self.assertTrue({'First name', 'Second name'} <= {a['name'] for a in row['aliases']})
        self.assertEqual(next(f for f in self.store.faces() if f['id'] == face)['name'], 'Reviewed name')
        self.assertEqual(next(p for p in self.store.people() if p['id'] == voice)['name'], 'Reviewed name')
        self.assertEqual(self.legacy_snapshot(), before)
        self.store.undo_person_edit(event['event_id'])
        row = next(p for p in self.store.unified_people() if p['id'] == person)
        self.assertTrue(row['name_conflict'])
        self.assertIsNone(row['name'])

    def test_outer_transaction_rollback_covers_migration_marker_and_assignments(self):
        self.face('visible', 'Alex')
        self.voice('heard', 'Alex')
        before = self.legacy_snapshot()
        with self.assertRaisesRegex(RuntimeError, 'power cut'):
            with self.store._transaction():
                self.store.ensure_unified_people()
                raise RuntimeError('power cut')
        self.assertIsNone(self.store.db.execute("SELECT value FROM meta WHERE key='unified_people_version'").fetchone())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM unified_evidence').fetchone()[0], 0)
        self.assertEqual(self.legacy_snapshot(), before)
        self.reopen()
        self.store.ensure_unified_people()
        self.assertEqual(len(self.store.unified_people()), 2)

    def test_failed_edit_rolls_back_person_name_mapping_and_event_history(self):
        from unittest.mock import patch
        face = self.face('visible', 'Alex')
        voice = self.voice('heard', 'Blair')
        self.store.ensure_unified_people()
        a = self.store.person_for_evidence('face', face)
        b = self.store.person_for_evidence('voice', voice)
        before = self.store.unified_people()
        events = self.store.db.execute('SELECT COUNT(*) FROM unified_person_events').fetchone()[0]
        with patch.object(self.store, '_audit', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.store.merge_unified_people(a, b, 'Reviewed same person', chosen_name='Alex')
        self.reopen()
        self.assertEqual(self.store.unified_people(), before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM unified_person_events').fetchone()[0], events)

    def test_legacy_face_merge_and_split_reconcile_without_copying_identity_to_wrong_match(self):
        first = self.face('first', 'Alex')
        second = self.face('second', 'Alex')
        self.store.ensure_unified_people()
        owner = self.store.person_for_evidence('face', second)
        first_observation = next(o['observation_id'] for o in self.store.face_observations() if o['content_id'] == 'first')
        self.store.merge_faces(first, second, 'Legacy reviewed merge')
        self.store.ensure_unified_people()
        self.assertEqual(self.store.person_for_evidence('face', first), owner)
        new_face = self.store.split_face(second, [first_observation], 'Operator separated a wrong face')
        self.store.ensure_unified_people()
        separate = self.store.person_for_evidence('face', new_face)
        self.assertNotEqual(separate, owner)
        self.assertIsNone(next(p for p in self.store.unified_people() if p['id'] == separate)['name'])
        self.reopen()
        self.assertEqual(self.store.person_for_evidence('face', new_face), separate)
        self.assertEqual(self.store.person_for_evidence('face', second), owner)

    def test_legacy_voice_merge_and_split_reconcile_as_separate_evidence(self):
        first = self.voice('first', 'Alex')
        second = self.voice('second', 'Alex')
        self.store.ensure_unified_people()
        owner = self.store.person_for_evidence('voice', second)
        observation = next(o['observation_id'] for o in self.store.observations() if o['content_id'] == 'first')
        self.store.merge_people(first, second, 'Legacy reviewed merge')
        self.store.ensure_unified_people()
        self.assertEqual(self.store.person_for_evidence('voice', first), owner)
        new_voice = self.store.split_person(second, [observation], 'Operator separated wrong speaker')
        self.store.ensure_unified_people()
        separate = self.store.person_for_evidence('voice', new_voice)
        self.assertNotEqual(separate, owner)
        self.assertIsNone(next(p for p in self.store.unified_people() if p['id'] == separate)['name'])

    def test_combining_operator_renamed_people_surfaces_actual_conflicting_labels(self):
        face = self.face('visible')
        voice = self.voice('heard')
        self.store.ensure_unified_people()
        a = self.store.person_for_evidence('face', face)
        b = self.store.person_for_evidence('voice', voice)
        self.store.rename_unified_person(a, 'Reviewed face label')
        self.store.rename_unified_person(b, 'Reviewed voice label')
        self.store.merge_unified_people(a, b, 'Operator combined evidence, name undecided')
        row = next(p for p in self.store.unified_people() if p['id'] == b)
        self.assertTrue(row['name_conflict'])
        self.assertIsNone(row['name'])
        self.assertTrue({'Reviewed face label', 'Reviewed voice label'} <= {x['name'] for x in row['aliases']})

    def test_detach_removes_effective_legacy_link_but_preserves_historical_assertion(self):
        face = self.face('visible', 'Alex')
        voice = self.voice('heard', 'Alex')
        self.legacy_link(face, voice)
        self.store.ensure_unified_people()
        person = self.store.person_for_evidence('face', face)
        self.store.detach_person_evidence(person, 'voice', voice, 'Operator removed a wrong AV match')
        self.assertIsNone(next(f for f in self.store.faces() if f['id'] == face)['voice_link'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM face_voice_links').fetchone()[0], 1)

    def test_review_appearances_distinguish_seen_heard_and_both(self):
        from ditnotes.people_api import review_people
        face = self.face('seen-only', 'Alex')
        voice = self.voice('heard-only', 'Alex')
        self.legacy_link(face, voice)
        self.store.ensure_unified_people()
        person = self.store.person_for_evidence('face', face)
        self.store.assign_face('both', 2, [1, 0], 'face-fixture', 1, bbox=[.1, .1, .4, .4], auto_assign=True, match_threshold=.9, min_member_similarity=.9)
        self.store.assign_speaker('both', 'speaker', 0, 4, [1, 0], 'voice-fixture', 1, auto_assign=True, match_threshold=.9)
        catalog = {'assets': [{'id': x, 'content_id': x} for x in ('seen-only', 'heard-only', 'both')]}
        result = review_people(self.store, catalog)
        row = next(p for p in result['people'] if p['id'] == person)
        flags = {a['content_id']: (a['visible'], a['heard']) for a in row['appearances']}
        self.assertEqual(flags, {'seen-only': (True, False), 'heard-only': (False, True), 'both': (True, True)})
        self.assertTrue(row['face_samples'])
        self.assertTrue(row['voice_samples'])

    def test_stale_people_api_revision_cannot_overwrite_operator_changes(self):
        import threading
        from ditnotes.metadata_api import MetadataAPI
        from ditnotes.project_store import TagConflict
        face = self.face('visible', 'Alex')
        self.store.ensure_unified_people()
        person = self.store.person_for_evidence('face', face)
        base = self.store.edit_revision
        api = MetadataAPI.__new__(MetadataAPI)
        api.people_migration_ready = True
        api.store = lambda: ProjectStore(self.path, 'safety-fixture')
        api.changed = threading.Event()
        api.mutate('/api/people/name', {'person_id': person, 'name': 'Reviewed name', 'base_revision': base})
        with self.assertRaises(TagConflict):
            api.mutate('/api/people/name', {'person_id': person, 'name': 'Stale overwrite', 'base_revision': base})
        with self.assertRaises(TagConflict):
            api.mutate('/api/people/note', {'person_id': person, 'text': 'Invalid boolean revision', 'base_revision': True})
        self.reopen()
        row = next(p for p in self.store.unified_people() if p['id'] == person)
        self.assertEqual(row['name'], 'Reviewed name')
        self.assertEqual(row['notes'], '')
