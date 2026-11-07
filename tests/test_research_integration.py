"""Failure and ownership checks using disposable projects and no ML inference."""
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from ditnotes.research_api import ResearchService
from ditnotes.research_store import ResearchBoard


class ResearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / '_DIT_NOTES').mkdir()
        self.catalog = {'project': {'id': 'disposable-project'}, 'catalog_revision': 'snapshot-a', 'assets': []}
        self.server = SimpleNamespace(project=self.project, catalog=SimpleNamespace(read=lambda: self.catalog),
                                      chat_init_lock=threading.Lock())

    def board(self):
        return ResearchBoard(self.project, 'disposable-project')

    def service(self):
        service = ResearchService(self.server)
        self.addCleanup(service.close)
        return service

    def test_secondary_service_cannot_recover_or_execute_owners_running_job(self):
        first = self.service()
        with self.board() as board:
            q = board.create('A question')
            run = board.start_run(q['id'], 1, 'snapshot-a')
            board.update_run(run['id'], status='running')
        second = self.service()
        self.assertTrue(first.owner)
        self.assertFalse(second.owner)
        self.assertFalse(second.state()['can_run'])
        with self.assertRaisesRegex(ValueError, 'another library server'):
            second.submit(q['id'], 1)
        with self.board() as board:
            self.assertEqual(board.runs()[0]['status'], 'running')

    def test_owner_startup_recovers_backlog_but_polling_does_not(self):
        with self.board() as board:
            q = board.create('A question')
            old = board.start_run(q['id'], 1, 'snapshot-a')
        service = self.service()
        self.assertEqual(service.state()['runs'][0]['status'], 'interrupted')
        with self.board() as board:
            fresh = board.start_run(q['id'], 1, 'snapshot-a')
        self.assertEqual(service.state()['runs'][-1]['status'], 'queued')
        self.assertNotEqual(old['id'], fresh['id'])

    def test_state_polling_never_constructs_index_or_loads_model(self):
        service = self.service()
        with patch('ditnotes.research_api.ResearchTools') as tools, patch('ditnotes.chat.get_chat') as chat:
            for _ in range(3):
                state = service.state()
                self.assertEqual(state['coverage']['total'], 0)
            tools.assert_not_called()
            chat.assert_not_called()

    def test_live_ingest_guard_happens_before_search_or_model_initialization(self):
        service = self.service()
        self.server.get_status = lambda: {'pending': 1}
        with patch('ditnotes.research_api.ResearchTools') as tools, patch('ditnotes.chat.get_chat') as chat:
            with self.assertRaisesRegex(ValueError, 'ingest'):
                service.submit('q', 1)
            tools.assert_not_called()
            chat.assert_not_called()

    def test_queue_failure_durably_interrupts_run_and_closes_tools(self):
        service = self.service()
        with self.board() as board:
            q = board.create('A question')
        tools = SimpleNamespace(snapshot_revision='snapshot-a', close=Mock())
        chat = SimpleNamespace(executor=SimpleNamespace(submit=Mock(side_effect=RuntimeError('executor closed'))))
        with patch('ditnotes.research_api.ResearchTools', return_value=tools), patch('ditnotes.chat.get_chat', return_value=chat):
            with self.assertRaisesRegex(RuntimeError, 'executor closed'):
                service.submit(q['id'], 1)
        tools.close.assert_called_once()
        with self.board() as board:
            self.assertEqual(board.runs()[0]['status'], 'interrupted')
            self.assertEqual(board.get(q['id'])['answers'], [])

    def test_zero_pending_does_not_bypass_ingest_warmup_or_finalization(self):
        service=self.service()
        for phase in ('starting','warming','processing','waiting_for_project'):
            self.server.get_status=lambda phase=phase:{'pending':0,'phase':phase}
            with self.subTest(phase=phase),patch('ditnotes.research_api.ResearchTools') as tools:
                with self.assertRaisesRegex(ValueError,'ingest'):service.submit('unused',1)
                tools.assert_not_called()

    def test_one_queued_job_bounds_node_indexes_and_cancel_cleans_up(self):
        service = self.service()
        with self.board() as board:
            one = board.create('First question')
            two = board.create('Second saved question')
        future = Future()
        tools = SimpleNamespace(snapshot_revision='snapshot-a', close=Mock())
        chat = SimpleNamespace(executor=SimpleNamespace(submit=Mock(return_value=future)))
        with patch('ditnotes.research_api.ResearchTools', return_value=tools) as factory, patch('ditnotes.chat.get_chat', return_value=chat):
            result = service.submit(one['id'], 1)
            with self.assertRaisesRegex(ValueError, 'already queued or running'):
                service.submit(two['id'], 1)
            factory.assert_called_once()
            self.assertTrue(future.cancel())
            tools.close.assert_called_once()
            self.assertFalse(service.job_pending)
            with self.board() as board:
                self.assertEqual(board._run(result['run_id'])['status'], 'interrupted')
                self.assertEqual(board.get(two['id'])['question'], 'Second saved question')
                self.assertEqual(board.get(one['id'])['answers'], [])

    def test_index_initialization_failure_releases_job_reservation(self):
        service = self.service()
        with patch('ditnotes.chat.get_chat'), patch('ditnotes.research_api.ResearchTools', side_effect=RuntimeError('bad snapshot')):
            with self.assertRaisesRegex(RuntimeError, 'bad snapshot'):
                service.submit('q', 1)
        self.assertFalse(service.job_pending)

    def test_constructor_recovery_failure_releases_execution_lock(self):
        with patch.object(ResearchService, 'board', side_effect=RuntimeError('injected recovery failure')):
            with self.assertRaisesRegex(RuntimeError, 'recovery failure'):
                ResearchService(self.server)
        service = self.service()
        self.assertTrue(service.owner)

    def test_two_simultaneous_operator_updates_have_one_cas_winner(self):
        with self.board() as board:
            q = board.create('A question')
        barrier = threading.Barrier(2)
        def edit(note):
            with self.board() as board:
                barrier.wait(timeout=5)
                try:
                    board.update(q['id'], 1, notes=note)
                    return 'saved'
                except ValueError:
                    return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(edit, ['one', 'two']))
        self.assertCountEqual(results, ['saved', 'conflict'])
        with self.board() as board:
            self.assertEqual(board.get(q['id'])['version'], 2)
            self.assertEqual(board.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
