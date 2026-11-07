"""Bounded collection must yield to a source-only writer without retrieval tools."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pydantic_ai.messages import ModelResponse, ToolCallPart, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from ditnotes.research_agent import make_research_agent, make_collection_agent, run_research_agent, original_dossier
from test_research_tools import fixture


def user_text(messages):
    return '\n'.join(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart) and isinstance(p.content, str))


def supported(short):
    return ModelResponse(parts=[TextPart(json.dumps({'outcome':'answered', 'answer':'The original interview discusses uncertainty ['+short+'].',
        'claims':[{'text':'The interview discusses uncertainty.', 'evidence_ids':[short]}]}))])


class ResearchPhaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_repeated_collection_requests_force_tool_free_writer_and_canonical_answer(self):
        tools=fixture();tools.model_coverage();tools.read_passages(['one:speech:0'],0)
        short=tools.model_passage(tools.by_id['one:speech:0'])['id']
        tools.search_interviews=Mock(return_value={'results':[]})
        counts={'collector':0,'writer':0}
        def collect(messages,info):
            counts['collector']+=1
            self.assertEqual(info.model_settings['max_tokens'],512)
            self.assertFalse(info.model_settings['extra_body']['enable_thinking'])
            return ModelResponse(parts=[ToolCallPart('search_interviews',{'query':'repeated concept'})])
        def write(messages,info):
            counts['writer']+=1
            self.assertEqual(info.function_tools,[])
            self.assertEqual(info.model_settings['max_tokens'],3600)
            self.assertTrue(info.model_settings['extra_body']['enable_thinking'])
            text=user_text(messages)
            self.assertIn(tools.by_id['one:speech:0']['text'],text)
            self.assertNotIn(tools.by_id['one:speech:1']['text'],text)
            self.assertIn('collection_budget_exhausted',text)
            self.assertEqual(tools.search_interviews.call_count,6)
            return supported(short)
        writer,client=make_research_agent();collector=make_collection_agent(FunctionModel(collect))
        with writer.override(model=FunctionModel(write)),patch('ditnotes.research_agent.make_research_agent',return_value=(writer,client)),patch('ditnotes.research_agent.make_collection_agent',return_value=collector) as factory:
            answer,history=await run_research_agent('Explain uncertainty',tools,model_settings={'extra_body':{'enable_thinking':True,'thinking_budget':256}})
        factory.assert_called_once_with(writer.model)
        self.assertEqual(counts,{'collector':6,'writer':1})
        self.assertEqual(answer.result_ids,['one:speech:0'])
        self.assertEqual(answer.claims[0].evidence_ids,['one:speech:0'])
        self.assertEqual(tools.shown[0]['id'],'one:speech:0')
        self.assertIn('repeated concept',history)
        self.assertIn('original interview discusses',history)
        self.assertTrue(client.is_closed)

    async def test_writer_retries_unread_reference_without_lookup(self):
        tools=fixture();tools.model_coverage();tools.read_passages(['one:speech:0'],0)
        good=tools.model_passage(tools.by_id['one:speech:0'])['id']
        unread=tools.model_passage(tools.by_id['one:speech:2'])['id']
        tools.search_interviews=Mock(side_effect=AssertionError('Writer cannot search'))
        attempts=[]
        def write(messages,info):
            attempts.append(1)
            self.assertEqual(info.function_tools,[])
            self.assertNotIn(tools.by_id['one:speech:2']['text'],user_text(messages))
            return supported(unread if len(attempts)==1 else good)
        writer,client=make_research_agent()
        collector=make_collection_agent(FunctionModel(lambda *_:ModelResponse(parts=[TextPart('Collector draft is not evidence.')])) )
        with writer.override(model=FunctionModel(write)),patch('ditnotes.research_agent.make_research_agent',return_value=(writer,client)),patch('ditnotes.research_agent.make_collection_agent',return_value=collector):
            answer,_=await run_research_agent('Explain uncertainty',tools)
        self.assertEqual(len(attempts),2)
        self.assertEqual(answer.result_ids,['one:speech:0'])
        tools.search_interviews.assert_not_called()

    async def test_collector_cross_checks_only_after_explicit_openings_and_drafts_are_not_writer_facts(self):
        tools=fixture();tools.configure_request('Compare accounts across interviews')
        tools.search_interviews=Mock(return_value={'results':[{'id':'unread','text':'Unread candidate sentinel'}]})
        steps=[]
        def collect(messages,info):
            steps.append(1)
            if len(steps)==1:return ModelResponse(parts=[ToolCallPart('read_interview_openings',{'interview_ids':['one','two']})])
            if len(steps)==2:return ModelResponse(parts=[ToolCallPart('search_interviews',{'query':'shared concept','interview_ids':['one']})])
            return ModelResponse(parts=[TextPart('COLLECTOR DRAFT SENTINEL')])
        def write(messages,info):
            text=user_text(messages)
            self.assertNotIn('COLLECTOR DRAFT SENTINEL',text)
            self.assertNotIn('Unread candidate sentinel',text)
            self.assertIn('"stop_reason": "returned"',text)
            self.assertEqual(info.function_tools,[])
            return ModelResponse(parts=[TextPart(json.dumps({'outcome':'not_supported','answer':'The collected evidence does not establish this premise.'}))])
        writer,client=make_research_agent();collector=make_collection_agent(FunctionModel(collect))
        with writer.override(model=FunctionModel(write)),patch('ditnotes.research_agent.make_research_agent',return_value=(writer,client)),patch('ditnotes.research_agent.make_collection_agent',return_value=collector):
            await run_research_agent('Compare the accounts',tools)
        self.assertTrue(tools.search_interviews.call_args.kwargs['cohort_cross_check'])
        dossier=original_dossier(tools)
        self.assertEqual(len(dossier['interviews']),2)
        self.assertTrue(all('asset_id' not in r for g in dossier['interviews'] for r in g['passages']))

    async def test_overall_deadline_wraps_both_phases_and_closes_shared_client(self):
        writer,client=make_research_agent()
        async def expire(coro,*,timeout):
            self.assertEqual(timeout,720)
            coro.close()
            raise TimeoutError('CPU deadline injection')
        with patch('ditnotes.research_agent.make_research_agent',return_value=(writer,client)),patch('ditnotes.research_agent.asyncio.wait_for',side_effect=expire):
            with self.assertRaisesRegex(TimeoutError,'12-minute') as caught:
                await run_research_agent('Question',fixture())
        self.assertEqual(json.loads(caught.exception.messages), [])
        self.assertTrue(client.is_closed)

    async def test_thrown_writer_error_retains_collector_and_writer_diagnostics(self):
        writer,client=make_research_agent()
        collector=make_collection_agent(FunctionModel(lambda *_:ModelResponse(parts=[TextPart('COLLECTOR DIAGNOSTIC')])) )
        def fail(*_): raise RuntimeError('CPU writer failure')
        with writer.override(model=FunctionModel(fail)),patch('ditnotes.research_agent.make_research_agent',return_value=(writer,client)),patch('ditnotes.research_agent.make_collection_agent',return_value=collector):
            with self.assertRaisesRegex(RuntimeError,'CPU writer failure') as caught:
                await run_research_agent('Question',fixture())
        self.assertIn('COLLECTOR DIAGNOSTIC',caught.exception.messages)
        self.assertIn('Original evidence dossier',caught.exception.messages)
        self.assertTrue(client.is_closed)


if __name__=='__main__':unittest.main()
