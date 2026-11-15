import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ditnotes.premiere_bridge import PremiereBridge
from ditnotes.project_store import ProjectStore


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.notes = self.project / '_DIT_NOTES'
        self.notes.mkdir()
        (self.project / 'clip.mp4').write_bytes(b'synthetic fixture media')
        self.cat = {'project': {'id': 'synthetic'}, 'assets': [
            {'id': 'asset-1', 'content_id': 'sha', 'name': 'clip.mp4', 'relative_path': 'clip.mp4',
             'duration': 10, 'kind': 'video', 'highlights': [{'seconds': 2, 'note': 'A red fox'}],
             'transcript': [{'start': 1, 'end': 3, 'text': 'We saw a fox.'}]}]}
        self.write_catalog()
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            store.set_asset_tags('sha', ['Wildlife', 'Interview'], 0)
            store.add_annotation('sha', 'Use this detail', start=2, end=4)
            store.add_annotation('sha', 'Whole clip note')
        self.bridge = PremiereBridge(self.project)
        self.pair = self.bridge.prepare(str(self.project / 'Synthetic.prproj'))

    def tearDown(self):
        self.temp.cleanup()

    def write_catalog(self):
        (self.notes / 'catalog.json').write_text(json.dumps(self.cat))

    def receipt(self, op, status='saved', **kwargs):
        return {key: op[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')} | {'status': status, 'readback': {'assets': [{'asset_id': 'asset-1', 'path': str(self.project.resolve() / 'clip.mp4')}]}} | kwargs

    def test_snapshot_tags_notes_sparse_markers_and_idempotent_save(self):
        before = (self.project / 'clip.mp4').read_bytes()
        op = self.bridge.queue_snapshot()
        self.assertEqual(op['revision'], 1)
        self.assertEqual(op['assets'][0]['relative_path'], 'clip.mp4')
        self.assertEqual(op['assets'][0]['tags'][0]['name'], 'Wildlife')
        self.assertEqual(len(op['assets'][0]['notes']), 2)
        sample = next(m for m in op['assets'][0]['markers'] if m['source'] == 'ai_visual_sample')
        self.assertEqual(sample['start'], sample['end'])
        self.assertIsNone(op['assets'][0]['transcript_json'])
        self.assertEqual(self.bridge.queue_snapshot()['operation_id'], op['operation_id'])
        self.assertEqual(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path']), op)
        state = self.bridge.acknowledge(self.receipt(op, 'applied'), self.pair['token'])
        self.assertEqual(state['saved_revision'], 0)
        self.assertEqual(state['applied_revision'], 1)
        state = self.bridge.acknowledge(self.receipt(op), self.pair['token'])
        self.assertEqual(state['saved_revision'], 1)
        self.assertIsInstance(state['last_saved_at'], float)
        self.assertIsNone(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path']))
        self.assertIsNone(json.loads((self.bridge.exchange / 'request.json').read_text()))
        self.assertEqual(self.bridge.acknowledge(self.receipt(op))['saved_revision'], 1)
        self.assertEqual((self.project / 'clip.mp4').read_bytes(), before)

    def test_restart_reconciles_commit_without_json_publication(self):
        with patch('ditnotes.premiere_bridge.atomic_json', side_effect=OSError('power loss')):
            with self.assertRaises(OSError):
                self.bridge.queue_snapshot()
        restarted = PremiereBridge(self.project)
        self.assertEqual(restarted.status()['desired_revision'], 1)
        op = restarted.queue_snapshot()
        self.assertEqual(op['revision'], 1)
        self.assertEqual(json.loads((restarted.exchange / 'request.json').read_text())['operation_id'], op['operation_id'])

    def test_saved_receipt_survives_lost_publication_ack_and_retry(self):
        op = self.bridge.queue_snapshot()
        with patch('ditnotes.premiere_bridge.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.bridge.acknowledge(self.receipt(op))
        restarted = PremiereBridge(self.project)
        self.assertEqual(restarted.status()['saved_revision'], 1)
        restarted.publish()
        self.assertIsNone(json.loads((restarted.exchange / 'request.json').read_text()))
        self.assertEqual(restarted.acknowledge(self.receipt(op))['saved_revision'], 1)

    def test_corrupt_or_foreign_receipt_does_not_change_queue(self):
        op = self.bridge.queue_snapshot()
        for update in ({'project_id': 'other'}, {'desired_hash': 'wrong'}, {'native_project_path': '/foreign.prproj'}, {'protocol': 999}, {'status': 'saved', 'readback': None}):
            with self.assertRaises(ValueError):
                self.bridge.acknowledge(self.receipt(op, **update))
        self.assertEqual(self.bridge.status()['saved_revision'], 0)
        with self.assertRaises(PermissionError):
            self.bridge.acknowledge(self.receipt(op), 'bad token')
        with self.assertRaises(ValueError):
            self.bridge.next_operation(self.pair['token'], '/other.prproj')

    def test_ordered_queue_and_base_native_readback(self):
        first = self.bridge.queue_snapshot()
        self.bridge.acknowledge(self.receipt(first))
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            store.set_asset_tags('sha', ['Sea ice'], 1)
        second = self.bridge.queue_snapshot()
        self.assertEqual(second['revision'], 2)
        self.assertEqual(second['base_readback'], self.receipt(first)['readback'])
        self.cat['assets'][0]['transcript'][0]['text'] = 'A changed transcript.'
        self.write_catalog()
        third = self.bridge.queue_snapshot()
        with self.assertRaises(ValueError):
            self.bridge.acknowledge(self.receipt(third))
        self.assertEqual(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])['revision'], 2)
        self.bridge.acknowledge(self.receipt(second))
        self.assertEqual(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])['revision'], 3)

    def test_conflict_requires_explicit_retry_stale_receipt_does_not_reblock(self):
        op = self.bridge.queue_snapshot()
        receipt = self.receipt(op, 'conflict', message='Editor changed marker')
        self.bridge.acknowledge(receipt)
        self.assertIsNone(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path']))
        (self.bridge.exchange / 'receipt.json').write_text(json.dumps(receipt))
        self.bridge.retry(op['operation_id'])
        self.assertEqual(self.bridge.reconcile()['state'], 'queued')
        self.assertEqual(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])['operation_id'], op['operation_id'])

    def test_reject_outside_paths_symlinks_missing_media_and_invalid_ranges(self):
        for relative in ('../outside.mp4', '/absolute.mp4', 'missing.mp4'):
            self.cat['assets'][0]['relative_path'] = relative
            self.write_catalog()
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.bridge.queue_snapshot()
        self.cat['assets'][0]['relative_path'] = 'clip.mp4'
        self.cat['assets'][0]['highlights'][0]['seconds'] = 100
        self.write_catalog()
        with self.assertRaises(ValueError):
            self.bridge.queue_snapshot()
        self.assertEqual(self.bridge.status()['desired_revision'], 0)
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'source.mp4'
            external.write_bytes(b'external')
            (self.project / 'link.mp4').symlink_to(external)
            self.cat['assets'][0]['relative_path'] = 'link.mp4'
            self.cat['assets'][0]['highlights'][0]['seconds'] = 1
            self.write_catalog()
            with self.assertRaises(ValueError):
                self.bridge.queue_snapshot()

    def test_projection_upgrade_queues_native_resync_without_reindexing_ai(self):
        first = self.bridge.queue_snapshot()
        current_version = first['projection_version']
        self.assertGreaterEqual(current_version, 1)
        self.bridge.acknowledge(self.receipt(first))
        before_catalog = (self.notes / 'catalog.json').read_bytes()
        before_media = (self.project / 'clip.mp4').read_bytes()
        with patch('ditnotes.premiere_bridge.PREMIERE_PROJECTION_VERSION', current_version + 1):
            upgraded = self.bridge.queue_snapshot()
            self.assertEqual(upgraded['revision'], 2)
            self.assertEqual(upgraded['projection_version'], current_version + 1)
            self.assertNotEqual(upgraded['desired_hash'], first['desired_hash'])
            self.assertEqual(self.bridge.queue_snapshot()['operation_id'], upgraded['operation_id'])
        self.assertEqual((self.notes / 'catalog.json').read_bytes(), before_catalog)
        self.assertEqual((self.project / 'clip.mp4').read_bytes(), before_media)
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            self.assertEqual(store.edit_revision, 3)

    def test_token_rotation_invalidates_old_plugin_without_losing_queue(self):
        op = self.bridge.queue_snapshot()
        old = self.pair['token']
        self.pair = self.bridge.prepare(self.pair['native_project_path'])
        with self.assertRaises(PermissionError):
            self.bridge.authorize(old)
        self.assertEqual(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])['operation_id'], op['operation_id'])

    def test_sigkill_uncommitted_outbox_rolls_back(self):
        source = '''import os,signal,sys
from ditnotes.premiere_bridge import PremiereBridge
b=PremiereBridge(sys.argv[1]);s=b._store();s.db.execute('BEGIN IMMEDIATE')
s.db.execute("INSERT INTO premiere_bridge_operations(operation_id,desired_hash,payload,status,created_at,updated_at) VALUES('interrupted','x','{}','queued',0,0)")
os.kill(os.getpid(),signal.SIGKILL)
'''
        done = subprocess.run([sys.executable, '-c', source, str(self.project)], cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(done.returncode, -9)
        self.assertEqual(PremiereBridge(self.project).status()['desired_revision'], 0)

    def test_sigkill_after_commit_open_wal_recovers(self):
        source = '''import os,signal,sys
from ditnotes.premiere_bridge import PremiereBridge
b=PremiereBridge(sys.argv[1]);b.queue_snapshot();s=b._store()
os.kill(os.getpid(),signal.SIGKILL)
'''
        done = subprocess.run([sys.executable, '-c', source, str(self.project)], cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(done.returncode, -9)
        restarted = PremiereBridge(self.project)
        self.assertEqual(restarted.reconcile()['desired_revision'], 1)
        self.assertEqual(restarted.queue_snapshot()['revision'], 1)


if __name__ == '__main__':
    unittest.main()

class JournalWorkerTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    tearDown = BridgeTests.tearDown
    write_catalog = BridgeTests.write_catalog
    receipt = BridgeTests.receipt

    def test_old_seen_receipt_cannot_regress_after_a_newer_applied_ack(self):
        op = self.bridge.queue_snapshot()
        old = self.receipt(op, 'error', receipt_id='attempt-a', message='First failure')
        self.bridge.acknowledge(old)
        self.bridge.retry(op['operation_id'])
        applied = self.receipt(op, 'applied', receipt_id='attempt-b')
        self.bridge.acknowledge(applied)
        self.assertEqual(self.bridge.acknowledge(old)['state'], 'applied')
        new_error = self.receipt(op, 'error', receipt_id='attempt-c', message='First failure')
        self.assertEqual(self.bridge.acknowledge(new_error)['state'], 'error')

    def test_receipt_history_upgrade_seeds_existing_error_before_retry(self):
        op = self.bridge.queue_snapshot()
        old = self.receipt(op, 'error', receipt_id='old-prototype-attempt', message='Old failure')
        self.bridge.acknowledge(old)
        with self.bridge._store() as store:
            with store._transaction():
                store.db.execute('DELETE FROM premiere_bridge_receipts')
                store.db.execute('DELETE FROM premiere_bridge_meta')
        restarted = PremiereBridge(self.project)
        restarted.retry(op['operation_id'])
        restarted.acknowledge(self.receipt(op, 'applied', receipt_id='new-attempt'))
        self.assertEqual(restarted.acknowledge(old)['state'], 'applied')

    def test_worker_reconciles_saved_receipt_and_new_metadata_without_models(self):
        from ditnotes.premiere_bridge import PremiereJournalWorker
        worker = PremiereJournalWorker(self.project)
        worker.tick()
        op = json.loads((self.bridge.exchange / 'request.json').read_text())
        (self.bridge.exchange / 'receipt.json').write_text(json.dumps(self.receipt(op, receipt_id='attempt-a')))
        worker.tick()
        self.assertEqual(self.bridge.status()['saved_revision'], 1)
        self.assertIsNone(json.loads((self.bridge.exchange / 'request.json').read_text()))
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            store.set_asset_tags('sha', ['After restart'], 1)
        worker = PremiereJournalWorker(self.project)
        worker.tick()
        op = json.loads((self.bridge.exchange / 'request.json').read_text())
        self.assertEqual(op['revision'], 2)
        self.assertEqual(op['assets'][0]['tags'][0]['name'], 'After restart')

    def test_worker_keeps_interruption_and_parse_failure_diagnostics_then_recovers(self):
        from ditnotes.premiere_bridge import PremiereJournalWorker
        import time
        worker = PremiereJournalWorker(self.project, interval=.05)
        worker.bridge.queue_snapshot()
        (self.bridge.exchange / 'receipt.json').write_text('{incomplete')
        worker.start()
        deadline = time.monotonic() + 2
        while not worker.error and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(worker.error)
        self.assertEqual(self.bridge.status()['saved_revision'], 0)
        (self.bridge.exchange / 'receipt.json').unlink()
        deadline = time.monotonic() + 2
        while worker.error and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNone(worker.error)
        start = time.monotonic()
        worker.close()
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(worker.status()['running'])

    def test_unpaired_worker_does_not_create_outbox_or_exchange(self):
        from ditnotes.premiere_bridge import PremiereJournalWorker
        with self.bridge._store() as store:
            with store._transaction():
                store.db.execute('DELETE FROM premiere_bridge_config')
        PremiereJournalWorker(self.project).tick()
        self.assertFalse(self.bridge.exchange.exists())
        self.assertEqual(self.bridge.status()['desired_revision'], 0)

class CommandTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    tearDown = BridgeTests.tearDown
    write_catalog = BridgeTests.write_catalog
    receipt = BridgeTests.receipt

    def initialized(self):
        initial = self.bridge.queue_snapshot()
        self.bridge.acknowledge(self.receipt(initial))
        return initial

    def test_command_is_typed_durable_and_does_not_manufacture_snapshot_revisions(self):
        initial = self.initialized()
        selected = [{'asset_id': 'asset-1', 'start': 2, 'end': 4}]
        command = self.bridge.queue_command('reveal', selected, 'A human result', 'click-1')
        self.assertEqual(command['command'], {'kind': 'reveal', 'ranges': selected, 'name': 'A human result'})
        self.assertEqual(command['revision'], 2)
        self.assertEqual(self.bridge.queue_snapshot()['operation_id'], initial['operation_id'])
        self.assertEqual(self.bridge.status()['desired_revision'], 2)
        restarted = PremiereBridge(self.project)
        restarted.reconcile()
        self.assertEqual(json.loads((self.bridge.exchange / 'request.json').read_text())['command']['kind'], 'reveal')
        self.assertEqual(restarted.queue_command('reveal', selected, 'A human result', 'click-1')['operation_id'], command['operation_id'])
        restarted.acknowledge(self.receipt(command))
        self.assertEqual(restarted.queue_snapshot()['revision'], 1)
        self.assertEqual(restarted.status()['desired_revision'], 2)

    def test_saved_snapshot_summary_survives_command_and_reports_metadata_save_time(self):
        self.assertIsNone(self.bridge.status()['snapshot_summary'])
        initial = self.initialized()
        summary = self.bridge.status()['snapshot_summary']
        self.assertEqual(summary['clips'], 1)
        self.assertEqual(summary['tag_bins'], 2)
        self.assertEqual(summary['revision'], initial['revision'])
        self.assertGreater(summary['saved_at'], 0)
        command = self.bridge.queue_command('reveal', [{'asset_id': 'asset-1', 'start': 1, 'end': 2}])
        self.bridge.acknowledge(self.receipt(command))
        self.assertEqual(self.bridge.status()['snapshot_summary'], summary)
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            store.set_asset_tags('sha', ['Only one tag now'], 1)
        next_snapshot = self.bridge.queue_snapshot()
        self.assertEqual(self.bridge.status()['snapshot_summary'], summary)
        self.bridge.acknowledge(self.receipt(next_snapshot))
        updated = self.bridge.status()['snapshot_summary']
        self.assertEqual(updated['clips'], 1)
        self.assertEqual(updated['tag_bins'], 1)
        self.assertEqual(updated['revision'], next_snapshot['revision'])
        self.assertGreaterEqual(updated['saved_at'], summary['saved_at'])

    def test_explicit_retry_delivers_only_that_operations_failed_readback_without_changing_hash(self):
        self.initialized()
        command = self.bridge.queue_command('selects', [{'asset_id': 'asset-1', 'start': 2, 'end': 4}])
        self.assertNotIn('previous_attempt_readback', command)
        failed = {'actual': [{'source_in': 0, 'source_out': 2}], 'expected': [{'source_in': 2, 'source_out': 4}]}
        receipt = self.receipt(command, 'conflict', receipt_id='failed-trim-attempt', readback=failed, plugin_build='synthetic-test-build')
        self.bridge.acknowledge(receipt)
        self.assertEqual(self.bridge.status()['last_receipt']['plugin_build'], 'synthetic-test-build')
        self.assertIsNone(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path']))
        self.bridge.retry(command['operation_id'])
        delivered = self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])
        self.assertEqual(delivered['previous_attempt_readback'], failed)
        self.assertEqual(delivered['desired_hash'], command['desired_hash'])
        self.assertEqual(delivered['operation_id'], command['operation_id'])
        with self.bridge._store() as store:
            payload = store.db.execute('SELECT payload FROM premiere_bridge_operations WHERE operation_id=?', (command['operation_id'],)).fetchone()[0]
            self.assertNotIn('previous_attempt_readback', json.loads(payload))
        # The old failed receipt is consumed history, not a new failed retry.
        (self.bridge.exchange / 'receipt.json').write_text(json.dumps(receipt))
        self.assertEqual(self.bridge.reconcile()['state'], 'queued')
        self.assertEqual(json.loads((self.bridge.exchange / 'request.json').read_text())['previous_attempt_readback'], failed)

    def test_selects_preserve_source_order_and_operator_request_retry_is_idempotent(self):
        self.initialized()
        ranges = [{'asset_id': 'asset-1', 'start': 4, 'end': 6}, {'asset_id': 'asset-1', 'start': 1, 'end': 3}]
        command = self.bridge.queue_command('selects', ranges, 'Fox selects', 'selection-1')
        self.assertEqual(command['command']['ranges'], ranges)
        self.assertEqual(self.bridge.queue_command('selects', ranges, 'Fox selects', 'selection-1')['operation_id'], command['operation_id'])
        with self.assertRaises(ValueError):
            self.bridge.queue_command('selects', ranges, 'Different title', 'selection-1')
        self.assertEqual(self.bridge.status()['desired_revision'], 2)
        distinct = self.bridge.queue_command('selects', ranges, 'Another intentionally generated sequence', 'selection-2')
        self.assertEqual(distinct['revision'], 3)

    def test_command_validation_rejects_uninitialized_unknown_or_out_of_bounds_inputs(self):
        selected = [{'asset_id': 'asset-1', 'start': 1, 'end': 3}]
        with self.assertRaises(ValueError):
            self.bridge.queue_command('reveal', selected)
        self.initialized()
        for kind, ranges in [('eval', selected), ('reveal', []), ('reveal', selected * 2), ('selects', selected * 201),
                             ('reveal', [{'asset_id': 'foreign', 'start': 1, 'end': 2}]),
                             ('reveal', [{'asset_id': 'asset-1', 'start': 8, 'end': 11}]),
                             ('reveal', [{'asset_id': 'asset-1', 'start': 2, 'end': 2}]),
                             ('reveal', [{'asset_id': 'asset-1', 'start': 1, 'end': 3, 'code': 'arbitrary'}])]:
            with self.assertRaises(ValueError):
                self.bridge.queue_command(kind, ranges)
        self.assertEqual(self.bridge.status()['desired_revision'], 1)

    def test_metadata_changed_after_command_becomes_next_real_snapshot_without_mutating_notes(self):
        self.initialized()
        command = self.bridge.queue_command('selects', [{'asset_id': 'asset-1', 'start': 2, 'end': 4}])
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            annotations = store.annotations('sha')
            store.set_asset_tags('sha', ['New editorial tag'], 1)
        snapshot = self.bridge.queue_snapshot()
        self.assertEqual(snapshot['revision'], 3)
        self.assertNotIn('command', snapshot)
        self.assertEqual(snapshot['assets'][0]['tags'][0]['name'], 'New editorial tag')
        with self.assertRaises(ValueError):
            self.bridge.acknowledge(self.receipt(snapshot))
        self.bridge.acknowledge(self.receipt(command))
        self.bridge.acknowledge(self.receipt(snapshot))
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            self.assertEqual(store.annotations('sha'), annotations)

class CancellationAndAuditTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    tearDown = BridgeTests.tearDown
    write_catalog = BridgeTests.write_catalog
    receipt = BridgeTests.receipt
    initialized = CommandTests.initialized

    def command(self):
        self.initialized()
        return self.bridge.queue_command('selects', [{'asset_id': 'asset-1', 'start': 2, 'end': 4}])

    def test_cancel_retains_partial_output_evidence_and_never_claims_a_save(self):
        command = self.command()
        failed = self.receipt(command, 'conflict', receipt_id='failed-a', readback={'actual': [1], 'expected': [2]})
        self.bridge.acknowledge(failed)
        state = self.bridge.cancel(command['operation_id'], 'Operator retained the partial sequence for review')
        self.assertEqual(state['state'], 'cancelled')
        self.assertEqual(state['saved_revision'], 1)
        self.assertEqual(state['pending'], [])
        self.assertEqual(state['cancel_event']['reason'], 'Operator retained the partial sequence for review')
        self.assertEqual(state['last_receipt']['readback'], failed['readback'])
        self.assertIsNone(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path']))
        self.assertIsNone(json.loads((self.bridge.exchange / 'request.json').read_text()))
        self.assertEqual(self.bridge.cancel(command['operation_id'], 'Repeated request')['cancel_event'], state['cancel_event'])
        with self.assertRaises(ValueError):
            self.bridge.retry(command['operation_id'])
        self.assertEqual((self.project / 'clip.mp4').read_bytes(), b'synthetic fixture media')

    def test_cancel_does_not_block_next_command_and_late_receipt_cannot_regress(self):
        command = self.command()
        next_command = self.bridge.queue_command('reveal', [{'asset_id': 'asset-1', 'start': 1, 'end': 3}])
        self.assertEqual(self.bridge.status()['pending'][0]['operation_kind'], 'command')
        self.bridge.cancel(command['operation_id'], 'Keep old output; make a fresh request')
        self.assertEqual(self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])['operation_id'], next_command['operation_id'])
        late = self.receipt(command, receipt_id='completed-after-cancel')
        self.assertEqual(self.bridge.acknowledge(late)['saved_revision'], 1)
        self.assertEqual(self.bridge.receipt_history(command['operation_id'])[0]['receipt'], late)
        self.bridge.acknowledge(self.receipt(next_command))
        self.assertEqual(self.bridge.status()['saved_revision'], next_command['revision'])
        self.assertEqual(self.bridge.status()['snapshot_summary']['revision'], 1)
        with self.bridge._store() as store:
            self.assertEqual(store.db.execute('SELECT status FROM premiere_bridge_operations WHERE operation_id=?', (command['operation_id'],)).fetchone()[0], 'cancelled')

    def test_cannot_cancel_snapshot_or_saved_or_applied_command(self):
        snapshot = self.initialized()
        with self.assertRaises(ValueError):
            self.bridge.cancel(snapshot['operation_id'], 'No metadata cancellation')
        command = self.bridge.queue_command('reveal', [{'asset_id': 'asset-1', 'start': 1, 'end': 3}])
        self.bridge.acknowledge(self.receipt(command, 'applied'))
        with self.assertRaises(ValueError):
            self.bridge.cancel(command['operation_id'], 'Not allowed after apply')
        self.bridge.acknowledge(self.receipt(command))
        with self.assertRaises(ValueError):
            self.bridge.cancel(command['operation_id'], 'Not allowed after save')

    def test_full_receipt_packets_remain_immutable_after_later_attempts(self):
        command = self.command()
        first = self.receipt(command, 'error', receipt_id='first', readback={'actual': [1], 'expected': [2]})
        self.bridge.acknowledge(first)
        first_archive = self.bridge.receipt_history(command['operation_id'])[0]
        self.bridge.retry(command['operation_id'])
        second = self.receipt(command, 'conflict', receipt_id='second', readback={'conflicts': ['Different preflight result']})
        self.bridge.acknowledge(second)
        history = self.bridge.receipt_history(command['operation_id'])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0], first_archive)
        self.assertEqual(history[1]['receipt'], second)
        self.bridge.retry(command['operation_id'])
        delivery = self.bridge.next_operation(self.pair['token'], self.pair['native_project_path'])
        self.assertEqual(delivery['previous_attempt_readback'], second['readback'])
        self.assertNotEqual(delivery['previous_attempt_readback'], first['readback'])

    def test_old_hash_only_history_migration_restores_known_packet_without_fabricating_lost_one(self):
        command = self.command()
        first = self.receipt(command, 'error', receipt_id='old-lost', readback={'actual': ['not reconstructable later']})
        self.bridge.acknowledge(first)
        self.bridge.retry(command['operation_id'])
        second = self.receipt(command, 'error', receipt_id='old-current', readback={'actual': ['still known']})
        self.bridge.acknowledge(second)
        with self.bridge._store() as store:
            with store._transaction():
                store.db.execute('ALTER TABLE premiere_bridge_receipts DROP COLUMN receipt')
                store.db.execute("DELETE FROM premiere_bridge_meta WHERE key='receipt_packets_seeded'")
        history = PremiereBridge(self.project).receipt_history(command['operation_id'])
        self.assertFalse(history[0]['available'])
        self.assertIsNone(history[0]['receipt'])
        self.assertTrue(history[1]['available'])
        self.assertEqual(history[1]['receipt'], second)
        self.assertEqual(history[0]['receipt_hash'], __import__('ditnotes.premiere_bridge', fromlist=['digest']).digest(first))

    def test_archive_and_status_rollback_together_if_ack_fails_before_commit(self):
        command = self.command()
        first = self.receipt(command, 'error', receipt_id='rolled-back', readback={'actual': [1]})
        with patch.object(PremiereBridge, '_status', side_effect=OSError('Interrupted before commit')):
            with self.assertRaises(OSError):
                self.bridge.acknowledge(first)
        self.assertEqual(self.bridge.receipt_history(command['operation_id']), [])
        self.assertEqual(self.bridge.status()['pending'][0]['status'], 'queued')

    def test_cancellation_commit_survives_lost_journal_publication(self):
        command = self.command()
        with patch('ditnotes.premiere_bridge.atomic_json', side_effect=OSError('Disk full after commit')):
            with self.assertRaises(OSError):
                self.bridge.cancel(command['operation_id'], 'Keep partial output')
        restarted = PremiereBridge(self.project)
        self.assertEqual(restarted.reconcile()['state'], 'cancelled')
        self.assertIsNone(json.loads((self.bridge.exchange / 'request.json').read_text()))

class ProductionTranscriptBridgeTests(unittest.TestCase):
    setUp = BridgeTests.setUp
    tearDown = BridgeTests.tearDown
    write_catalog = BridgeTests.write_catalog
    receipt = BridgeTests.receipt

    def native_words(self):
        return {'language': 'en-us', 'speakers': [{'id': 'voice', 'name': 'Morgan'}],
                'segments': [{'start': 1, 'duration': 1, 'speaker': 'voice', 'language': 'en-us',
                              'words': [{'start': 1, 'duration': 1, 'text': 'Hello.', 'type': 'word'}]}]}

    def test_projection3_compact_receipt_requires_verified_words_and_source_timing(self):
        self.cat['assets'][0]['transcript_json'] = self.native_words(); self.write_catalog()
        operation = self.bridge.queue_snapshot(); receipt = self.receipt(operation)
        with self.assertRaisesRegex(ValueError, 'transcript readback'):
            self.bridge.acknowledge(receipt)
        asset = receipt['readback']['assets'][0]
        asset.update(transcript_verified=True, transcript_export_hash='a' * 64,
                     transcript_summary={'verified': True, 'word_count': 1, 'first_text': 'Hello.',
                                         'last_text': 'Hello.', 'first_start': 1, 'last_end': 2})
        asset['transcript_summary']['word_count'] = 2
        with self.assertRaisesRegex(ValueError, 'word readback'):
            self.bridge.acknowledge(receipt)
        asset['transcript_summary']['word_count'] = 1
        asset['transcript_summary']['last_end'] = 5
        with self.assertRaisesRegex(ValueError, 'timing readback'):
            self.bridge.acknowledge(receipt)
        self.assertEqual(self.bridge.status()['saved_revision'], 0)
        asset['transcript_summary']['last_end'] = 2
        self.assertEqual(self.bridge.acknowledge(receipt)['saved_revision'], 1)

    def test_idle_worker_does_not_rebuild_or_rewrite_large_unchanged_word_projection(self):
        from ditnotes.premiere_bridge import PremiereJournalWorker
        worker = PremiereJournalWorker(self.project); worker.tick()
        request = self.bridge.exchange / 'request.json'; before = request.stat().st_mtime_ns
        with patch.object(worker.bridge, 'queue_snapshot', wraps=worker.bridge.queue_snapshot) as queued:
            worker.tick(); worker.tick()
            self.assertEqual(queued.call_count, 0)
        self.assertEqual(request.stat().st_mtime_ns, before)
        with ProjectStore(self.notes / 'project.sqlite3', project_id='synthetic') as store:
            store.add_annotation('sha', 'New operator edit')
        worker.tick()
        self.assertEqual(self.bridge.status()['desired_revision'], 2)
        self.assertNotEqual(request.stat().st_mtime_ns, before)

    def test_worker_invalidation_includes_updated_identity_alignment(self):
        from ditnotes.premiere_bridge import PremiereJournalWorker
        worker = PremiereJournalWorker(self.project); worker.tick()
        with patch.object(worker.bridge, 'queue_snapshot', wraps=worker.bridge.queue_snapshot) as queued:
            (self.notes / 'sha-identity-alignment.json').write_text('{}')
            worker.tick(); self.assertEqual(queued.call_count, 1)
