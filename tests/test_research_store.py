"""Question-board writes preserve project metadata and survive interruption."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ditnotes.research_store import ResearchBoard


class ResearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def board(self):
        return ResearchBoard(self.project, 'fixture-project')

    def answer(self, text='A supported comparison', revision='snapshot-a'):
        return {'answer': text, 'outcome': 'answered', 'snapshot_revision': revision,
                'claims': [{'text': text, 'evidence_ids': ['one:speech:0:0', 'two:speech:0:0']}],
                'results': [{'id': 'one:speech:0:0'}], 'coverage': {'ready': 2, 'total': 3}}

    def test_question_notes_followups_and_answer_survive_restart_without_reindex(self):
        with self.board() as b:
            with b.store._transaction():
                person = b.store._new_person('A saved operator label')
            revision = b.store.edit_revision
            q = b.create('Compare the accounts?', notes='Operator context', followups=['What changed?'])
            b.save_answer(q['id'], q['version'], self.answer())
            self.assertEqual(b.store.edit_revision, revision)
        with self.board() as b:
            q = b.list('snapshot-a')[0]
            self.assertEqual(q['notes'], 'Operator context')
            self.assertEqual(q['followups'], ['What changed?'])
            self.assertEqual(q['latest_answer']['answer'], 'A supported comparison')
            self.assertFalse(q['stale'])
            self.assertEqual(b.store.people()[0]['name'], 'A saved operator label')
            self.assertEqual(b.store.people()[0]['id'], person)

    def test_optimistic_updates_do_not_silently_drop_other_operator_edits(self):
        with self.board() as a, self.board() as b:
            q = a.create('Question')
            a.update(q['id'], 1, notes='First editor')
            with self.assertRaisesRegex(ValueError, 'changed'):
                b.update(q['id'], 1, notes='Stale editor')
            self.assertEqual(b.get(q['id'])['notes'], 'First editor')

    def test_question_edited_during_run_retains_historical_answer_and_new_notes(self):
        with self.board() as b:
            q = b.create('Original question')
            run = b.start_run(q['id'], 1, 'snapshot-a')
            b.update(q['id'], 1, question='A better question', notes='Preserve this', status='follow_up')
            b.finish_run(run['id'], self.answer())
            q = b.get(q['id'], 'snapshot-a')
            self.assertEqual(q['question'], 'A better question')
            self.assertEqual(q['notes'], 'Preserve this')
            self.assertEqual(q['status'], 'follow_up')
            self.assertTrue(q['latest_answer']['question_changed'])
            self.assertTrue(q['stale'])

    def test_catalog_revision_marks_answers_stale_without_discarding_evidence(self):
        with self.board() as b:
            q = b.create('Question')
            b.save_answer(q['id'], 1, self.answer())
            self.assertTrue(b.get(q['id'], 'snapshot-b')['stale'])
            self.assertEqual(b.get(q['id'], 'snapshot-b')['latest_answer']['snapshot_revision'], 'snapshot-a')

    def test_partial_or_unsupported_investigation_keeps_question_open(self):
        with self.board() as b:
            for outcome in ('partial','not_supported'):
                q=b.create('A question with missing evidence')
                payload=self.answer();payload['outcome']=outcome
                b.save_answer(q['id'],1,payload)
                saved=b.get(q['id'],'snapshot-a')
                self.assertEqual(saved['status'],'open')
                self.assertEqual(saved['latest_answer']['outcome'],outcome)

    def test_failed_completion_rolls_back_answer_and_run_atomically(self):
        with self.board() as b:
            q = b.create('Question')
            run = b.start_run(q['id'], 1, 'snapshot-a')
            with patch.object(b, '_audit', side_effect=RuntimeError('injected interruption')):
                with self.assertRaisesRegex(RuntimeError, 'interruption'):
                    b.finish_run(run['id'], self.answer())
            self.assertEqual(b.runs()[0]['status'], 'queued')
            self.assertEqual(b.get(q['id'])['answers'], [])
            b.finish_run(run['id'], self.answer())
            self.assertEqual(len(b.get(q['id'])['answers']), 1)

    def test_completion_is_idempotent_but_cannot_replace_a_saved_answer(self):
        with self.board() as b:
            q = b.create('Question')
            run = b.start_run(q['id'], 1, 'snapshot-a')
            b.finish_run(run['id'], self.answer())
            b.finish_run(run['id'], self.answer())
            self.assertEqual(len(b.get(q['id'])['answers']), 1)
            with self.assertRaisesRegex(ValueError, 'immutable'):
                b.finish_run(run['id'], self.answer('A replacement'))
            with self.assertRaisesRegex(ValueError, 'immutable'):
                b.update_run(run['id'], status='error')

    def test_ordinary_state_reads_do_not_interrupt_running_jobs(self):
        with self.board() as b:
            q = b.create('Question')
            run = b.start_run(q['id'], 1, 'snapshot-a')
            b.update_run(run['id'], status='running', trace=[{'stage': 'Searching'}])
        with self.board() as b:
            self.assertEqual(b.state()['runs'][0]['status'], 'running')
            self.assertEqual(b.recover_runs(), 1)
            self.assertEqual(b.recover_runs(), 0)
            self.assertEqual(b.runs()[0]['status'], 'interrupted')
            next_run = b.start_run(q['id'], 1, 'snapshot-a')
            self.assertNotEqual(next_run['id'], run['id'])

    def test_archive_is_reversible_and_keeps_answers(self):
        with self.board() as b:
            q = b.create('Question')
            b.save_answer(q['id'], 1, self.answer())
            q = b.archive(q['id'], 1)
            self.assertEqual(b.list(), [])
            with self.assertRaisesRegex(ValueError, 'archived'):
                b.start_run(q['id'], 2, 'snapshot-a')
            q = b.update(q['id'], 2, status='open')
            self.assertEqual(len(q['answers']), 1)

    def test_crashed_uncommitted_writer_leaves_no_partial_question_or_metadata_loss(self):
        with self.board() as b:
            q = b.create('A saved question')
        code = '''import sqlite3,sys,os
c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA synchronous=FULL');c.execute('BEGIN IMMEDIATE')
c.execute("UPDATE research_questions SET notes='partial unsafe edit' WHERE id=?",(sys.argv[2],))
os._exit(73)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.project/'_DIT_NOTES/project.sqlite3'), q['id']])
        self.assertEqual(result.returncode, 73)
        with self.board() as b:
            self.assertEqual(b.get(q['id'])['notes'], '')
            self.assertEqual(b.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_invalid_followups_and_wrong_snapshot_do_not_publish(self):
        with self.board() as b:
            with self.assertRaises(ValueError):
                b.create('Question', followups='not a list')
            q = b.create('Question')
            run = b.start_run(q['id'], 1, 'snapshot-a')
            with self.assertRaisesRegex(ValueError, 'snapshot'):
                b.finish_run(run['id'], self.answer(revision='snapshot-b'))
            self.assertEqual(b.get(q['id'])['answers'], [])

    def test_model_payload_cannot_replace_canonical_ids_or_question_version(self):
        with self.board() as b:
            q = b.create('Question')
            run = b.start_run(q['id'], 1, 'snapshot-a')
            payload = self.answer()
            payload['question_version'] = 999
            with self.assertRaisesRegex(ValueError, 'canonical'):
                b.finish_run(run['id'], payload)
            self.assertEqual(b.get(q['id'])['answers'], [])
            self.assertEqual(b.runs()[0]['question_version'], 1)


if __name__ == '__main__':
    unittest.main()
