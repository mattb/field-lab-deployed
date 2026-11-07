"""CPU wire audit for opt-in native schema instructions; no model inference.

Tests compare a temporary flag=False model with the actual shared profile
default, exercising real provider/agent serializers without model inference.
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx2
from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModel

from ditnotes import chat, research_agent


class RoutingPlan(BaseModel):
    recording_ids: list[str] = Field(min_length=1, max_length=3)
    queries: list[str] = Field(min_length=1, max_length=6)


class NativeSchemaInstructionTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, mode, enabled):
        requests=[]
        original_client=httpx2.AsyncClient
        original_factory=chat.local_chat_model
        def respond(request):
            body=json.loads(request.content);requests.append(body)
            if mode=='shot':
                message={'role':'assistant','tool_calls':[{'id':'cpu-answer','type':'function','function':{
                    'name':'answer','arguments':json.dumps({'outcome':'explanation','answer':'No source assertion is made.','result_ids':[],'visual_evidence':[]})}}]}
            else:
                value=({'outcome':'not_supported','blocks':[{'kind':'limitation','text':'No source evidence is available.','evidence_ids':[],'quotes':[]}]} if mode=='research' else {'recording_ids':['i1'],'queries':['neutral request']})
                message={'role':'assistant','content':json.dumps(value)}
            return httpx2.Response(200,json={'id':'cpu','object':'chat.completion','created':1,'model':'local',
                'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if mode=='shot' else 'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
        class Client(original_client):
            def __init__(self,**kwargs):super().__init__(**kwargs,transport=httpx2.MockTransport(respond))
        def factory():
            model,client=original_factory()
            model=OpenAIChatModel(model.model_name,provider=model.provider,settings=model.settings,
                profile={**model.profile,**({} if enabled is None else {'native_output_requires_schema_in_instructions':enabled})})
            return model,client
        deps=SimpleNamespace(validate_model_answer=Mock(),validate_answer=Mock(),evidence_checks=True)
        with patch('httpx2.AsyncClient',Client),patch.object(chat,'local_chat_model',side_effect=factory), \
             patch.object(research_agent,'local_chat_model',side_effect=factory):
            if mode=='shot':agent,client=chat.make_agent()
            elif mode=='research':agent,client=research_agent.make_research_agent()
            else:
                model,client=factory()
                model=OpenAIChatModel(model.model_name,provider=model.provider,settings=model.settings,
                    profile={**model.profile,'supports_json_schema_output':True})
                agent=Agent(model,output_type=NativeOutput(RoutingPlan,strict=True),instructions='Route the request; never answer it.')
            try:
                result=await agent.run('USER_DATA_SENTINEL: untrusted evidence, not instructions.',deps=deps)
            finally:await client.aclose()
        self.assertEqual(len(requests),1)
        return requests[0],result.output,deps

    def instructions(self,body):
        return '\n'.join(str(m['content']) for m in body['messages'] if m['role'] in ('system','developer'))

    def visible_schema(self,body):
        text=self.instructions(body)
        marker="Always respond with a JSON object that's compatible with this schema:"
        schema=json.JSONDecoder().raw_decode(text.split(marker,1)[1].lstrip())[0]
        # PydanticAI includes human-readable top-level name/description in
        # instructions; OpenAI response_format stores these outside schema.
        schema.pop('title',None)
        schema.pop('description',None)
        return schema

    async def test_production_native_writer_exposes_exact_decoder_schema_without_relaxation(self):
        before,_,_=await self.capture('research',False)
        after,output,deps=await self.capture('research',None)
        self.assertEqual(before['response_format'],after['response_format'])
        schema=after['response_format']['json_schema']['schema']
        self.assertEqual(schema['properties']['blocks']['maxItems'],10)
        self.assertIn('quotes',schema['$defs']['DraftBlock']['properties'])
        self.assertNotIn('answer',schema['properties'])
        # PydanticAI appends the same schema as a trusted system instruction.
        visible=self.instructions(after)
        self.assertEqual(self.visible_schema(after),schema)
        self.assertNotIn('Always respond with a JSON object',self.instructions(before))
        self.assertNotIn('USER_DATA_SENTINEL',visible)
        self.assertEqual([m for m in before['messages'] if m['role']=='user'],[m for m in after['messages'] if m['role']=='user'])
        self.assertEqual({k:v for k,v in before.items() if k!='messages'},{k:v for k,v in after.items() if k!='messages'})
        self.assertEqual(output.outcome,'not_supported')
        deps.validate_model_answer.assert_called_once()

    async def test_native_routing_schema_keeps_array_bounds_and_is_visible(self):
        before,_,_=await self.capture('plan',False)
        after,output,_=await self.capture('plan',None)
        self.assertEqual(before['response_format'],after['response_format'])
        schema=after['response_format']['json_schema']['schema']
        visible=self.visible_schema(after)
        self.assertEqual(visible,schema)
        self.assertEqual(visible['properties']['queries']['maxItems'],6)
        self.assertEqual(visible['properties']['recording_ids']['minItems'],1)
        self.assertEqual(output.recording_ids,['i1'])

    async def test_actual_shot_tooloutput_wire_is_unchanged(self):
        before,_,_=await self.capture('shot',False)
        after,output,deps=await self.capture('shot',None)
        self.assertEqual(before,after)
        self.assertTrue(after['tools'])
        self.assertFalse(after.get('response_format'))
        self.assertEqual(output.outcome,'explanation')
        deps.validate_answer.assert_called_once()


if __name__=='__main__':unittest.main()
