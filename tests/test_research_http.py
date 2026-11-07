"""CPU-only HTTP contract checks; no server, model, or user's project is opened."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ditnotes.research_api import handle_research_post


class ResearchHTTPTests(unittest.TestCase):
    def handler(self, path='/api/research/question', body=None, **headers):
        encoded = json.dumps(body if body is not None else {'question': 'Compare the accounts'}).encode()
        h = SimpleNamespace(path=path, server=SimpleNamespace(server_port=18971),
            headers={'Host': '127.0.0.1:18971', 'Origin': 'http://127.0.0.1:18971',
                     'Content-Type': 'application/json', 'Content-Length': str(len(encoded)), **headers},
            rfile=io.BytesIO(encoded), send_error=Mock(), _json=Mock())
        return h

    def test_foreign_origin_and_host_cannot_mutate_board(self):
        for headers in ({'Origin': 'https://evil.invalid'}, {'Host': 'evil.invalid:18971'}):
            with self.subTest(headers=headers), patch('ditnotes.research_api.get_research') as service:
                h = self.handler(**headers)
                self.assertTrue(handle_research_post(h))
                h.send_error.assert_called_once_with(403)
                service.assert_not_called()

    def test_non_json_body_is_rejected_without_starting_service(self):
        with patch('ditnotes.research_api.get_research') as service:
            h = self.handler(**{'Content-Type': 'text/plain'})
            handle_research_post(h)
            h.send_error.assert_called_once_with(415)
            service.assert_not_called()

    def test_invalid_size_and_non_object_never_open_project(self):
        for h in (self.handler(**{'Content-Length': '150001'}),
                  self.handler(**{'Content-Length': '-1'}), self.handler(body=['question'])):
            with self.subTest(headers=h.headers), patch('ditnotes.research_api.get_research') as service:
                handle_research_post(h)
                self.assertEqual(h._json.call_args.args[2], 400)
                service.assert_not_called()

    def test_create_and_ask_contracts_preserve_version(self):
        with patch('ditnotes.research_api.get_research') as factory:
            service = factory.return_value
            service.question.return_value = {'id': 'q', 'version': 3}
            h = self.handler(body={'action': 'update', 'id': 'q', 'expected_version': 2, 'notes': 'Saved'})
            handle_research_post(h)
            self.assertEqual(h._json.call_args.args[0], {'question': {'id': 'q', 'version': 3}})
            service.submit.return_value = {'run_id': 'r', 'question_id': 'q'}
            h = self.handler('/api/research/ask', {'question_id': 'q', 'expected_version': 3})
            handle_research_post(h)
            service.submit.assert_called_once_with('q', 3)

    def test_unknown_route_is_left_to_existing_app(self):
        with patch('ditnotes.research_api.get_research') as service:
            self.assertFalse(handle_research_post(self.handler('/api/catalog')))
            service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
