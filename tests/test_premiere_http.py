"""The real HTTP bridge contract; only disposable project/own fixture bytes."""
import http.client
import json
import unittest
from urllib.parse import urlencode

from tests import test_metadata_api as fixture


class PremiereHTTPTests(unittest.TestCase):
    def setUp(self):
        fixture.API.setUp(self)
        cat = self.server.catalog.read()
        cat['assets'][0]['duration'] = 10
        (self.project / 'clip.mp4').write_bytes(b'synthetic media bytes')
        (self.project / '_DIT_NOTES/catalog.json').write_text(json.dumps(cat))
        self.native = str(self.project.resolve() / 'Synthetic.prproj')
        self.host = '127.0.0.1:' + str(self.server.server_port)

    tearDown = fixture.API.tearDown

    def request(self, path, value=None, headers=None, method=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = dict(headers or {})
        if value is not None:
            headers.setdefault('Content-Type', 'application/json')
        connection.request(method or ('POST' if value is not None else 'GET'), path,
                           json.dumps(value) if value is not None else None, headers)
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        connection.close()
        return status, json.loads(payload) if payload else None

    def pair_and_queue(self):
        status, pairing = self.request('/api/premiere/prepare', {'native_project_path': self.native}, {'Origin': 'http://' + self.host})
        self.assertEqual(status, 200)
        status, queue = self.request('/api/premiere/queue', {})
        self.assertEqual(status, 200)
        self.assertEqual(queue['saved_revision'], 0)
        return pairing, queue

    def test_web_pair_plugin_poll_applied_then_saved_status_without_token_leak(self):
        pairing, state = self.pair_and_queue()
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        query = urlencode({'native_project_path': self.native})
        status, operation = self.request('/api/premiere/request?' + query, headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(operation['operation_id'], state['operation_id'])
        receipt = {key: operation[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')}
        receipt |= {'status': 'applied', 'readback': {'assets': [{'asset_id': 'clip', 'path': str(self.project.resolve() / 'clip.mp4')}]}}
        status, state = self.request('/api/premiere/receipt', receipt, auth)
        self.assertEqual(status, 200)
        self.assertEqual(state['applied_revision'], 1)
        self.assertEqual(state['saved_revision'], 0)
        receipt['status'] = 'saved'
        status, state = self.request('/api/premiere/receipt', receipt, auth)
        self.assertEqual(status, 200)
        self.assertEqual(state['saved_revision'], 1)
        self.assertEqual(state['exchange_directory'], pairing['exchange_directory'])
        self.assertEqual(state['snapshot_summary']['clips'], 1)
        self.assertEqual(state['snapshot_summary']['tag_bins'], 0)
        self.assertGreater(state['snapshot_summary']['saved_at'], 0)
        self.assertNotIn(pairing['token'], json.dumps(state))
        self.assertNotIn('token_hash', json.dumps(state))
        self.assertEqual(self.request('/api/premiere/request?' + query, headers=auth), (200, None))
        status, state = self.request('/api/premiere/status')
        self.assertEqual(status, 200)
        self.assertEqual(state['saved_revision'], 1)

    def test_authenticated_poll_automatically_reconciles_web_edits_after_restart(self):
        from ditnotes.project_store import ProjectStore
        status, pairing = self.request('/api/premiere/prepare', {'native_project_path': self.native})
        self.assertEqual(status, 200)
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        query = urlencode({'native_project_path': self.native})
        status, first = self.request('/api/premiere/request?' + query, headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(first['revision'], 1)
        with ProjectStore(self.project / '_DIT_NOTES/project.sqlite3', project_id='fixture') as store:
            store.set_asset_tags('sha', ['Web edit while Premiere was closed'], 0)
        # The old operation stays first until Premiere acknowledges its save.
        status, still_first = self.request('/api/premiere/request?' + query, headers=auth)
        self.assertEqual(still_first['revision'], 1)
        receipt = {key: first[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')}
        receipt |= {'status': 'saved', 'readback': {'assets': [{'asset_id': 'clip', 'path': str(self.project.resolve() / 'clip.mp4')}]}}
        self.assertEqual(self.request('/api/premiere/receipt', receipt, auth)[0], 200)
        status, updated = self.request('/api/premiere/request?' + query, headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(updated['assets'][0]['tags'][0]['name'], 'Web edit while Premiere was closed')
        self.assertEqual(self.request('/api/premiere/request?' + query, headers=auth)[1]['revision'], 2)
        self.assertEqual(self.request('/api/premiere/status')[1]['desired_revision'], 2)

    def test_browser_cross_origin_and_rebinding_hosts_cannot_prepare_or_queue(self):
        for headers in ({'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'evil.example'}):
            status, response = self.request('/api/premiere/prepare', {'native_project_path': self.native}, headers)
            self.assertEqual(status, 403)
        self.assertFalse(self.request('/api/premiere/status')[1]['paired'])
        self.assertEqual(self.request('/api/premiere/queue', {}, {'Content-Type': 'text/plain'})[0], 400)

    def test_plugin_request_and_receipt_require_exact_pairing_and_project(self):
        pairing, _ = self.pair_and_queue()
        query = urlencode({'native_project_path': self.native})
        for headers in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'Basic nope'}):
            self.assertEqual(self.request('/api/premiere/request?' + query, headers=headers)[0], 403)
            self.assertEqual(self.request('/api/premiere/receipt', {}, headers)[0], 403)
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        self.assertEqual(self.request('/api/premiere/request?' + urlencode({'native_project_path': self.native + '.foreign'}), headers=auth)[0], 400)
        self.assertEqual(self.request('/api/premiere/request', headers=auth)[0], 400)
        self.assertEqual(self.request('/api/premiere/request?' + query + '&' + query, headers=auth)[0], 400)
        self.assertEqual(self.request('/api/premiere/unknown', {})[0], 404)
        self.assertEqual(self.request('/api/premiere/unknown')[0], 404)

    def test_web_navigation_and_selects_use_the_same_durable_outbox(self):
        pairing, _ = self.pair_and_queue()
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        query = urlencode({'native_project_path': self.native})
        _, initial = self.request('/api/premiere/request?' + query, headers=auth)
        receipt = {key: initial[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')}
        receipt |= {'status': 'saved', 'readback': {'assets': [{'asset_id': 'clip', 'path': str(self.project.resolve() / 'clip.mp4')}]}}
        self.assertEqual(self.request('/api/premiere/receipt', receipt, auth)[0], 200)
        body = {'ranges': [{'asset_id': 'clip', 'start': 1, 'end': 3}], 'name': 'Test result', 'request_id': 'web-click'}
        status, queued = self.request('/api/premiere/reveal', body)
        self.assertEqual(status, 200)
        self.assertEqual(queued['desired_revision'], 2)
        self.assertEqual(self.request('/api/premiere/reveal', body)[1]['operation_id'], queued['operation_id'])
        _, command = self.request('/api/premiere/request?' + query, headers=auth)
        self.assertEqual(command['command']['kind'], 'reveal')
        self.assertEqual(command['command']['ranges'], body['ranges'])
        # Polling must not create a snapshot behind every navigation request.
        self.assertEqual(self.request('/api/premiere/status')[1]['desired_revision'], 2)
        self.assertEqual(self.request('/api/premiere/selects', body | {'request_id': 'selects-click'})[0], 200)
        self.assertEqual(self.request('/api/premiere/reveal', body, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/premiere/selects', {'ranges': [{'asset_id': 'foreign', 'start': 1, 'end': 3}]})[0], 400)

    def test_cancel_command_retains_receipts_and_allows_explicit_new_request(self):
        pairing, _ = self.pair_and_queue()
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        query = urlencode({'native_project_path': self.native})
        _, initial = self.request('/api/premiere/request?' + query, headers=auth)
        receipt = {key: initial[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')}
        receipt |= {'status': 'saved', 'readback': {'assets': [{'asset_id': 'clip', 'path': str(self.project.resolve() / 'clip.mp4')}]}}
        self.assertEqual(self.request('/api/premiere/receipt', receipt, auth)[0], 200)
        self.assertEqual(self.request('/api/premiere/cancel', {'operation_id': initial['operation_id']})[0], 400)
        _, queued = self.request('/api/premiere/selects', {'ranges': [{'asset_id': 'clip', 'start': 1, 'end': 3}]})
        self.assertEqual(queued['pending'][0]['operation_kind'], 'command')
        cancel = {'operation_id': queued['operation_id'], 'reason': 'Operator cancelled; retain Premiere changes'}
        self.assertEqual(self.request('/api/premiere/cancel', cancel, {'Origin': 'https://evil.example'})[0], 403)
        status, result = self.request('/api/premiere/cancel', cancel)
        self.assertEqual(status, 200)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['saved_revision'], 1)
        self.assertIn('retain Premiere', result['cancel_event']['reason'])
        self.assertEqual(self.request('/api/premiere/retry', {'operation_id': queued['operation_id']})[0], 400)
        status, history = self.request('/api/premiere/receipts?' + urlencode({'operation_id': initial['operation_id']}))
        self.assertEqual(status, 200)
        self.assertEqual(history['receipts'][0]['receipt'], receipt)
        self.assertEqual(self.request('/api/premiere/receipts?operation_id=unknown')[0], 400)
        self.assertEqual(self.request('/api/premiere/receipts')[0], 400)

    def test_operation_status_is_authoritative_even_after_other_cancellations(self):
        pairing, _ = self.pair_and_queue()
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        query = urlencode({'native_project_path': self.native})
        _, initial = self.request('/api/premiere/request?' + query, headers=auth)
        receipt = {key: initial[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')}
        receipt |= {'status': 'saved', 'readback': {'assets': [{'asset_id': 'clip', 'path': str(self.project.resolve() / 'clip.mp4')}]}}
        self.assertEqual(self.request('/api/premiere/receipt', receipt, auth)[0], 200)
        body = {'ranges': [{'asset_id': 'clip', 'start': 1, 'end': 3}]}
        _, first = self.request('/api/premiere/reveal', body)
        self.request('/api/premiere/cancel', {'operation_id': first['operation_id'], 'reason': 'Keep first output'})
        _, second = self.request('/api/premiere/reveal', body)
        self.request('/api/premiere/cancel', {'operation_id': second['operation_id'], 'reason': 'Keep second output'})
        status, operation = self.request('/api/premiere/operation?' + urlencode({'operation_id': first['operation_id']}))
        self.assertEqual(status, 200)
        self.assertEqual(operation['status'], 'cancelled')
        self.assertEqual(operation['message'], 'Keep first output')
        self.assertEqual(operation['operation_kind'], 'command')
        self.assertEqual(self.request('/api/premiere/operation?' + urlencode({'operation_id': initial['operation_id']}))[1]['status'], 'saved')
        self.assertEqual(self.request('/api/premiere/operation?operation_id=unknown')[0], 400)
        self.assertEqual(self.request('/api/premiere/operation')[0], 400)
        self.assertEqual(self.request('/api/premiere/operation?operation_id=a&operation_id=b')[0], 400)

    def test_native_editor_conflict_is_visible_and_requires_explicit_retry(self):
        pairing, _ = self.pair_and_queue()
        auth = {'Authorization': 'Bearer ' + pairing['token']}
        query = urlencode({'native_project_path': self.native})
        _, operation = self.request('/api/premiere/request?' + query, headers=auth)
        receipt = {key: operation[key] for key in ('protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path')}
        receipt |= {'status': 'conflict', 'message': 'Editor changed owned note', 'readback': {'conflicts': [{'asset_id': 'clip', 'field': 'notes'}]}}
        status, state = self.request('/api/premiere/receipt', receipt, auth)
        self.assertEqual(status, 200)
        self.assertEqual(state['state'], 'conflict')
        self.assertEqual(state['saved_revision'], 0)
        self.assertIn('Editor changed', state['pending'][0]['message'])
        self.assertEqual(state['last_receipt']['readback']['conflicts'][0]['field'], 'notes')
        self.assertEqual(self.request('/api/premiere/request?' + query, headers=auth), (200, None))
        status, state = self.request('/api/premiere/retry', {'operation_id': operation['operation_id']})
        self.assertEqual(status, 200)
        self.assertEqual(state['state'], 'queued')
        self.assertEqual(self.request('/api/premiere/request?' + query, headers=auth)[1]['operation_id'], operation['operation_id'])


if __name__ == '__main__':
    unittest.main()
