"""Explicit limitation paragraphs retain prose without bypassing evidence gates."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx2
from pydantic_ai import ModelRetry
from ditnotes.research_agent import NativeDraft, compile_draft, make_research_agent
from test_research_tools import fixture


class ResearchDraftLimitationTests(unittest.TestCase):
    def tools(self):
        tools = fixture()
        tools.interview_coverage()
        tools.read_passages(['one:speech:1'], 0)
        tools.notify = Mock()
        short = tools.model_passage(tools.by_id['one:speech:1'])['id']
        return tools, short

    def test_partial_gap_paragraph_moves_verbatim_to_existing_limitations(self):
        tools, short = self.tools()
        gap = 'Only one recording was read. The requested comparison remains incomplete.'
        draft = NativeDraft(outcome='partial', blocks=[
            {'kind':'evidence', 'text':'The researcher describes uncertainty.', 'evidence_ids':[short]},
            {'kind':'limitation', 'text':gap}], limitations=['Machine transcript; no audio check.'])
        original = deepcopy(draft.model_dump())
        answer = compile_draft(SimpleNamespace(deps=tools), draft)
        tools.validate_model_answer(answer)
        self.assertEqual(len(answer.claims), 1)
        self.assertEqual(answer.claims[0].text, 'The researcher describes uncertainty.')
        self.assertEqual(answer.limitations, ['Machine transcript; no audio check.', gap])
        self.assertNotIn(gap, answer.answer)
        self.assertEqual(draft.model_dump(), original)
        self.assertEqual(tools.notify.call_args.args[0], 'Checking draft structure')
        self.assertEqual(tools.notify.call_args.args[1]['retained_limitation_paragraphs'], 1)

    def test_untyped_or_evidence_factual_block_without_sources_is_not_reclassified(self):
        for kind in ({}, {'kind':'evidence'}):
            tools, short = self.tools()
            draft = NativeDraft(outcome='partial', blocks=[
                {'text':'A supported conclusion.', 'evidence_ids':[short]},
                {'text':'An unsupported factual conclusion.', **kind}])
            with self.assertRaisesRegex(ModelRetry, 'Evidence block 1 needs'):
                compile_draft(SimpleNamespace(deps=tools), draft)
            self.assertEqual(tools.notify.call_args.args[1]['status'], 'retry')
            self.assertEqual(tools.shown, [])

    def test_limitation_kind_cannot_hide_citations_or_quotes(self):
        for fields in ({'evidence_ids':['e1']}, {'quotes':['An exact-looking claim.']}):
            tools, short = self.tools()
            draft = NativeDraft(outcome='partial', blocks=[
                {'text':'A supported conclusion.', 'evidence_ids':[short]},
                {'kind':'limitation', 'text':'A coverage caveat.', **fields}])
            with self.assertRaisesRegex(ModelRetry, 'Limitation block 1 must have empty'):
                compile_draft(SimpleNamespace(deps=tools), draft)

    def test_all_limitation_partial_cannot_pretend_to_be_sourced(self):
        tools, _ = self.tools()
        draft = NativeDraft(outcome='partial', blocks=[{'kind':'limitation', 'text':'No supported comparison is available.'}])
        with self.assertRaisesRegex(ModelRetry, 'Only limitation blocks'):
            compile_draft(SimpleNamespace(deps=tools), draft)
        draft.outcome = 'not_supported'
        answer = compile_draft(SimpleNamespace(deps=tools), draft)
        tools.validate_model_answer(answer)
        self.assertEqual(answer.answer, draft.blocks[0].text)
        self.assertEqual(answer.claims, [])
        self.assertEqual(answer.result_ids, [])

    def test_kind_does_not_bypass_unread_or_quote_validation(self):
        for bad in ('unread', 'quote'):
            tools, short = self.tools()
            draft = NativeDraft(outcome='partial', blocks=[
                {'kind':'evidence', 'text':'A conclusion.', 'evidence_ids':[short],
                 'quotes':['A fabricated quote.'] if bad == 'quote' else []},
                {'kind':'limitation', 'text':'A bounded comparison.'}])
            if bad == 'unread':
                draft.blocks[0].evidence_ids = [tools.model_passage(tools.by_id['two:speech:1'])['id']]
            answer = compile_draft(SimpleNamespace(deps=tools), draft)
            with self.assertRaises(ValueError):
                tools.validate_model_answer(answer)
            self.assertEqual(tools.shown, [])


class ResearchDraftLimitationWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_kind_schema_compiles_gap_without_an_extra_model_request(self):
        tools = fixture()
        tools.interview_coverage()
        tools.read_passages(['one:speech:1'], 0)
        short = tools.model_passage(tools.by_id['one:speech:1'])['id']
        gap = 'The remaining recordings were not reviewed.'
        requests = []
        original_client = httpx2.AsyncClient
        def respond(request):
            requests.append(json.loads(request.content))
            payload = {'outcome':'partial', 'blocks':[
                {'kind':'evidence', 'text':'The researcher describes uncertainty.', 'evidence_ids':[short], 'quotes':[]},
                {'kind':'limitation', 'text':gap, 'evidence_ids':[], 'quotes':[]}]}
            return httpx2.Response(200, json={'id':'cpu-kind', 'object':'chat.completion', 'created':1, 'model':'local',
                'choices':[{'index':0, 'message':{'role':'assistant', 'content':json.dumps(payload)}, 'finish_reason':'stop'}]})
        class MockedClient(original_client):
            def __init__(self, **kwargs):
                super().__init__(**kwargs, transport=httpx2.MockTransport(respond))
        with patch('httpx2.AsyncClient', MockedClient):
            agent, client = make_research_agent()
            try:
                result = await agent.run('CPU limitation block fixture.', deps=tools)
            finally:
                await client.aclose()
        self.assertEqual(len(requests), 1)
        schema = requests[0]['response_format']['json_schema']['schema']
        self.assertEqual(schema['$defs']['DraftBlock']['properties']['kind']['enum'], ['evidence', 'limitation'])
        self.assertEqual(result.output.limitations, [gap])
        self.assertEqual(len(result.output.claims), 1)
        self.assertEqual(result.output.result_ids, ['one:speech:1'])


if __name__ == '__main__':
    unittest.main()
