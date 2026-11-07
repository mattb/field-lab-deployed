"""CPU native-output compilation, storage and unchanged original-source gates."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx2
from pydantic_ai import ModelRetry
from ditnotes.research import ResearchAnswer
from ditnotes.research_store import ResearchBoard
from test_research_tools import fixture
import ditnotes.research_agent as spike


class NativeDraftTests(unittest.IsolatedAsyncioTestCase):
    def tools(self):
        t=fixture();t.configure_request('Discuss this account');t.model_coverage()
        t.read_passages(['one:speech:0'],0)
        short=t.model_passage(t.by_id['one:speech:0'])['id']
        return t,short

    async def run_drafts(self,tools,payloads):
        requests=[];original=httpx2.AsyncClient
        def response(request):
            requests.append(request)
            payload=payloads[min(len(requests)-1,len(payloads)-1)]
            return httpx2.Response(200,json={'id':'cpu','object':'chat.completion','created':1,'model':'local',
                'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(payload)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
        class Client(original):
            def __init__(self,**kw):super().__init__(**kw,transport=httpx2.MockTransport(response))
        with patch('httpx2.AsyncClient',Client):
            writer,client=spike.make_research_agent()
            try:result=await writer.run('A source-only question',deps=tools)
            finally:await client.aclose()
        return result.output,requests

    def draft(self,short,**extra):
        return {'outcome':'answered','blocks':[{'text':'The interviewer asks about uncertainty.','evidence_ids':[short],'quotes':[]}],**extra}

    async def test_actual_wire_unwraps_draft_and_returns_canonical_answer_and_storage(self):
        t,short=self.tools();answer,requests=await self.run_drafts(t,[self.draft(short)])
        self.assertIsInstance(answer,ResearchAnswer)
        self.assertEqual(answer.claims[0].text,'The interviewer asks about uncertainty.')
        self.assertEqual(answer.claims[0].evidence_ids,['one:speech:0'])
        self.assertEqual(answer.result_ids,['one:speech:0'])
        self.assertIn('[source](#shot=',answer.answer)
        body=json.loads(requests[0].content);schema=body['response_format']['json_schema']['schema']
        self.assertFalse(body.get('tools'))
        self.assertEqual(body['max_tokens'],5500)
        self.assertIn('blocks',schema['properties'])
        self.assertNotIn('draft',schema['properties'])
        self.assertNotIn('answer',schema['properties'])
        self.assertNotIn('claims',schema['properties'])
        t.commit_answer(answer)
        with tempfile.TemporaryDirectory() as path,ResearchBoard(Path(path),'draft-fixture') as board:
            q=board.create('Question')
            board.save_answer(q['id'],1,{**answer.model_dump(),'results':t.shown,'snapshot_revision':t.snapshot_revision})
            self.assertEqual(board.get(q['id'])['latest_answer']['results'][0]['id'],'one:speech:0')

    async def test_official_retry_for_missing_ids_then_source_validator_for_unread(self):
        t,short=self.tools();bad=self.draft(short);bad['blocks'][0]['evidence_ids']=[]
        good=self.draft(short)
        answer,requests=await self.run_drafts(t,[bad,good])
        self.assertEqual(len(requests),2)
        self.assertIn('needs exact original read evidence_ids',requests[1].content.decode())
        self.assertEqual(answer.result_ids,['one:speech:0'])
        unread=t.model_passage(t.by_id['one:speech:2'])['id']
        answer,requests=await self.run_drafts(t,[self.draft(unread),good])
        self.assertEqual(len(requests),2)
        self.assertEqual(t.read_ids,{'one:speech:0'})

    def test_unsupported_and_partial_structure_does_not_weaken_source_or_scope_checks(self):
        t,short=self.tools();ctx=SimpleNamespace(deps=t)
        unsupported=spike.NativeDraft(outcome='not_supported',blocks=[{'text':'This bounded dossier does not establish support.'}])
        answer=spike.compile_draft(ctx,unsupported);t.validate_model_answer(answer)
        self.assertEqual(answer.claims,[])
        with self.assertRaises(ModelRetry):spike.compile_draft(ctx,spike.NativeDraft(**self.draft(short,outcome='not_supported')))
        t.minimum_interviews=3
        answer=spike.compile_draft(ctx,spike.NativeDraft(**self.draft(short)))
        with self.assertRaises(ValueError):t.validate_model_answer(answer)
        partial=self.draft(short,outcome='partial',limitations=['Only one requested recording has read evidence.'])
        answer=spike.compile_draft(ctx,spike.NativeDraft(**partial));t.validate_model_answer(answer)
        bad=self.draft(short);bad['blocks'][0]['quotes']=['A fabricated quotation.']
        answer=spike.compile_draft(ctx,spike.NativeDraft(**bad));t.minimum_interviews=1
        with self.assertRaisesRegex(ValueError,'quotation'):t.validate_model_answer(answer)
        t.assets['three']={'id':'three','name':'three.mp4','status':'ready'}
        row={**t.by_id['one:speech:0'],'id':'three:speech:0','assetId':'three'}
        t.speech['three']=[row];t.by_id[row['id']]=row;t.pool[row['id']]=row
        t.read_passages(['two:speech:0','three:speech:0'],0);t.minimum_interviews=3
        ids=[t.model_passage(t.by_id[key])['id'] for key in ('one:speech:0','two:speech:0','three:speech:0')]
        omnibus={'outcome':'answered','blocks':[{'text':'One omnibus conclusion.','evidence_ids':ids}]}
        answer=spike.compile_draft(ctx,spike.NativeDraft(**omnibus))
        with self.assertRaisesRegex(ValueError,'three distinct'):t.validate_model_answer(answer)

    def test_invalid_draft_is_atomic_and_rejects_manual_refs_and_missing_sources(self):
        t,short=self.tools();ctx=SimpleNamespace(deps=t)
        cases=[
            {'text':'No supporting IDs.'},
            {'text':'Manual reference ['+short+'].','evidence_ids':[short]},
            {'text':'Manual [source](#shot=one&t=0).','evidence_ids':[short]},
        ]
        for block in cases:
            with self.subTest(block=block):
                draft=spike.NativeDraft(outcome='partial',blocks=[{'text':'A supported paragraph.','evidence_ids':[short]},block],limitations=['Bounded context.'])
                before=draft.model_dump();ledger=set(t.read_ids)
                with self.assertRaises(ModelRetry):spike.compile_draft(ctx,draft)
                self.assertEqual(draft.model_dump(),before)
                self.assertEqual(t.read_ids,ledger)
        for fields in ({'evidence_ids':[short]},{'quotes':['An unsupported quote.']}):
            draft=spike.NativeDraft(outcome='not_supported',blocks=[{'text':'Evidence is insufficient.',**fields}])
            with self.assertRaises(ModelRetry):spike.compile_draft(ctx,draft)

    def test_repeated_block_ids_are_deduplicated_without_mutation(self):
        t,short=self.tools()
        draft=spike.NativeDraft(**self.draft(short))
        draft.blocks[0].evidence_ids=[short,short]
        answer=spike.compile_draft(SimpleNamespace(deps=t),draft)
        self.assertEqual(draft.blocks[0].evidence_ids,[short,short])
        self.assertEqual(answer.claims[0].evidence_ids,[short])
        self.assertEqual(answer.answer.count(short),1)
        t.validate_model_answer(answer)
        self.assertEqual(answer.result_ids,['one:speech:0'])

    def test_no_decoder_length_mask_or_compiler_clipping(self):
        schema=spike.NativeDraft.model_json_schema()
        self.assertNotIn('maxLength',json.dumps(schema))
        text=('A complete supported paragraph. '*250).strip()
        quote='A long exact quotation. '*60
        draft=spike.NativeDraft(outcome='partial',blocks=[{'text':text,'evidence_ids':['e1'],'quotes':[quote]}])
        answer=spike.compile_draft(SimpleNamespace(),draft)
        self.assertGreater(len(text),6000)
        self.assertEqual(answer.claims[0].text,text)
        self.assertEqual(answer.claims[0].quotes,[quote])
        self.assertEqual(answer.answer,text+' [e1]')

    def test_completeness_checks_only_obvious_format_tails(self):
        for text in ('The methods differ but are','A cut-off claim 或','```text\nunfinished'):
            with self.subTest(text=text):self.assertFalse(spike.complete_block(text))
        for text in ('A finished sentence.','“A finished sentence.”','**A finished sentence.**',
                     'A question?','```text\ncode without punctuation\n```',
                     '| Topic | Value |\n| --- | --- |\n| method | qualitative |'):
            with self.subTest(text=text):self.assertTrue(spike.complete_block(text))
        draft=spike.NativeDraft(outcome='not_supported',blocks=[{'text':'The sentence ends but are'}])
        with self.assertRaisesRegex(ModelRetry,'Complete its last sentence'):
            spike.compile_draft(SimpleNamespace(),draft)

    async def test_unknown_source_retries_and_keeps_original_ledger(self):
        t,short=self.tools();bad=self.draft('e999')
        answer,requests=await self.run_drafts(t,[bad,self.draft(short)])
        self.assertEqual(len(requests),2)
        self.assertEqual(t.read_ids,{'one:speech:0'})
        self.assertEqual(answer.result_ids,['one:speech:0'])


if __name__=='__main__':unittest.main()
