"""Actual local-agent HTTP serialization and pure progress parsing; no ML/network."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx2

from ditnotes.chat import local_chat_model
from ditnotes.research_agent import make_research_agent
from ditnotes.research_progress import model_progress


class ResearchTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_3600_token_budget_reaches_mlx_chat_wire(self):
        requests = []
        original_client = httpx2.AsyncClient

        def respond(request):
            requests.append(request)
            payload = {'outcome': 'not_supported', 'answer': 'No transcript evidence is available.',
                       'claims': [], 'result_ids': []}
            return httpx2.Response(200, json={
                'id': 'cpu-test', 'object': 'chat.completion', 'created': 1, 'model': 'local',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(payload)}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})

        test = self
        class MockedClient(original_client):
            def __init__(self, **kwargs):
                # Preserve the real factory's request hook and trust_env policy.
                test.assertFalse(kwargs['trust_env'])
                super().__init__(**kwargs, transport=httpx2.MockTransport(respond))

        with patch.dict('os.environ', {'FIELD_LAB_CHAT_PORT': '18983', 'PYDANTIC_AI_NO_BANNER': '1'}), \
                patch('httpx2.AsyncClient', MockedClient):
            agent, client = make_research_agent()
            try:
                validator = Mock()
                tools = SimpleNamespace(validate_answer=validator, validate_model_answer=validator)
                result = await agent.run('CPU-only research serialization test', deps=tools)
                self.assertEqual(result.output.outcome, 'not_supported')
                tools.validate_answer.assert_called_once()
            finally:
                await client.aclose()
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(str(request.url), 'http://127.0.0.1:18983/v1/chat/completions')
        body = json.loads(request.content)
        self.assertEqual(body['max_tokens'], 3600)
        self.assertNotIn('max_completion_tokens', body)
        self.assertFalse(body['enable_thinking'])
        self.assertEqual(request.extensions['timeout']['read'], 600)
        self.assertFalse(body.get('tools'))
        self.assertEqual(body['response_format']['type'], 'json_schema')
        schema = body['response_format']['json_schema']['schema']
        self.assertNotIn('result_ids', schema['properties'])
        self.assertEqual(schema['properties']['claims']['maxItems'], 10)
        self.assertEqual(schema['properties']['limitations']['maxItems'], 4)
        self.assertEqual(schema['properties']['followups']['maxItems'], 4)
        self.assertEqual(schema['properties']['limitations']['items']['maxLength'], 400)
        self.assertEqual(schema['$defs']['WriterResearchClaim']['properties']['quotes']['items']['maxLength'], 600)

    async def test_failed_local_generation_has_no_hidden_sdk_retry(self):
        from pydantic_ai.exceptions import ModelHTTPError
        requests = []
        original_client = httpx2.AsyncClient
        def respond(request):
            requests.append(request)
            return httpx2.Response(503, json={'error': {'message': 'CPU injected unavailable', 'type': 'server_error'}})
        class MockedClient(original_client):
            def __init__(self, **kwargs):
                super().__init__(**kwargs, transport=httpx2.MockTransport(respond))
        with patch('httpx2.AsyncClient', MockedClient):
            agent, client = make_research_agent()
            try:
                with self.assertRaises(ModelHTTPError):
                    await agent.run('CPU-only failure test', deps=SimpleNamespace(validate_answer=Mock()))
            finally:
                await client.aclose()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, '127.0.0.1')
        self.assertEqual(requests[0].extensions['timeout']['read'], 600)

    async def test_shared_transport_rejects_remote_url_before_transport(self):
        original_client = httpx2.AsyncClient
        transport = Mock(side_effect=AssertionError('Remote transport must never run'))
        class MockedClient(original_client):
            def __init__(self, **kwargs):
                super().__init__(**kwargs, transport=httpx2.MockTransport(transport))
        with patch('httpx2.AsyncClient', MockedClient):
            _, client = local_chat_model()
            try:
                with self.assertRaisesRegex(ValueError, 'stay on this Mac'):
                    await client.post('https://remote.invalid/v1/chat/completions', json={})
            finally:
                await client.aclose()
        transport.assert_not_called()

    async def test_native_profile_is_scoped_to_writer_and_reuses_shared_transport(self):
        base, client = local_chat_model()
        base.profile['supports_json_schema_output'] = False
        with patch('ditnotes.research_agent.local_chat_model', return_value=(base, client)):
            writer, returned = make_research_agent()
        try:
            self.assertIs(returned, client)
            self.assertIsNot(writer.model, base)
            self.assertFalse(base.profile['supports_json_schema_output'])
            self.assertTrue(writer.model.profile['supports_json_schema_output'])
            self.assertIs(writer.model.provider, base.provider)
            self.assertEqual(writer.model.settings, base.settings)
        finally:
            await client.aclose()


class ResearchProgressTests(unittest.TestCase):
    def test_queue_prefill_and_decode_events_have_measured_values_only(self):
        cases = [
            ('INFO Generation queued: request=abc prompt_tokens=12000',
             {'stage': 'Model reading context', 'prompt_tokens': 12000, 'processed_tokens': 0}),
            ('INFO Prefill progress: request=abc tokens=2048/12000 elapsed=3.0s',
             {'stage': 'Model reading context', 'processed_tokens': 2048, 'prompt_tokens': 12000}),
            ('INFO Decode progress: request=abc generated_tokens=48 elapsed=2.0s rate=24.5 tok/s',
             {'stage': 'Model writing response', 'generated_tokens': 48, 'tokens_per_second': 24.5}),
        ]
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(model_progress(line), expected)
                self.assertNotIn('eta', model_progress(line))

    def test_unrelated_or_incomplete_lines_do_not_invent_progress(self):
        for line in ('', 'GET /v1/models 200', 'Generation queued:',
                     'Prefill progress: tokens=unknown', 'Decode progress: generated_tokens=4'):
            with self.subTest(line=line):
                self.assertIsNone(model_progress(line))


if __name__ == '__main__':
    unittest.main()
