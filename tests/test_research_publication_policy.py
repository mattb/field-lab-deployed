"""Writer-visible application gates match live state without adding evidence."""
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import Mock

from ditnotes.research import ResearchAnswer, ResearchClaim
from ditnotes.research_agent import original_dossier, dossier_for_model, dossier_from_model
from test_research_tools import fixture


class PublicationPolicyTests(unittest.TestCase):
    def tools(self):
        tools = fixture()
        tools.configure_request('Discuss one account')
        tools.model_coverage()
        tools.read_passages(['one:speech:0'],0)
        tools.searched_interviews.add('one')
        tools.infer = Mock(side_effect=AssertionError('CPU metadata test must not infer'))
        tools.call = Mock(side_effect=AssertionError('CPU metadata test must not access a live project'))
        return tools

    def answer(self, tools, **extra):
        ref = tools.model_passage(tools.by_id['one:speech:0'])['id']
        data = dict(outcome='answered', answer='A qualified account. ['+ref+']',
                    claims=[ResearchClaim(text='A qualified account.',evidence_ids=[ref])])
        data.update(extra)
        return ResearchAnswer(**data)

    def test_live_collection_override_is_exposed_instead_of_reinferring_prompt_count(self):
        tools = self.tools()
        for index in range(7):
            key = f'extra-{index}'
            row = dict(tools.by_id['one:speech:0'],id=key+':speech:0',assetId=key)
            tools.assets[key] = dict(id=key,name=key+'.mp4',status='ready')
            tools.speech[key] = [row]
            tools.by_id[row['id']] = row
        tools.configure_request('Compare evidence from at least three interviews.')
        self.assertEqual(tools.minimum_interviews,3)
        tools.minimum_interviews = 9  # Application-selected collection cohort.
        before = (set(tools.read_ids),set(tools.read_interviews),set(tools.searched_interviews))
        dossier = original_dossier(tools)
        policy = dossier['publication_policy']
        self.assertEqual(policy['owner'],'application_validation')
        self.assertIn('not inferred user intent',policy['note'])
        self.assertEqual(policy['minimum_distinct_cited_recordings'],9)
        self.assertEqual(policy['minimum_distinct_supported_blocks_for_answered'],3)
        self.assertEqual(policy['available_transcribed_recording_count'],9)
        self.assertFalse(policy['corpus_coverage_required_by_application'])
        self.assertEqual(policy['missing_searched_recordings']['count'],8)
        self.assertEqual(policy['missing_read_context_recordings']['count'],8)
        for field in ('missing_searched_recordings','missing_read_context_recordings'):
            self.assertTrue(all(re.fullmatch(r'i[1-9][0-9]*',key) for key in policy[field]['recording_ids']))
            canonical = {tools._source_id('interviews',key) for key in policy[field]['recording_ids']}
            self.assertEqual(canonical,set(tools.speech)-{'one'})
        self.assertEqual(before,(tools.read_ids,tools.read_interviews,tools.searched_interviews))
        with self.assertRaisesRegex(ValueError,'at least 9'):
            tools.validate_model_answer(self.answer(tools))
        limited = self.answer(tools,outcome='partial',limitations=['Only one selected recording has source context.'])
        tools.validate_model_answer(limited)

    def test_application_flag_and_writer_chosen_corpus_scope_both_retain_gates(self):
        tools = self.tools()
        policy = original_dossier(tools)['publication_policy']
        self.assertFalse(policy['corpus_coverage_required_by_application'])
        self.assertTrue(policy['writer_scope_corpus_also_requires_complete_coverage'])
        tools.validate_model_answer(self.answer(tools,scope='focused'))
        with self.assertRaisesRegex(ValueError,'corpus-wide'):
            tools.validate_model_answer(self.answer(tools,scope='corpus'))
        limited = self.answer(tools,scope='corpus',outcome='partial',limitations=['One available recording has no read context.'])
        tools.validate_model_answer(limited)
        tools.requires_corpus = True
        self.assertTrue(original_dossier(tools)['publication_policy']['corpus_coverage_required_by_application'])
        with self.assertRaisesRegex(ValueError,'corpus-wide'):
            tools.validate_model_answer(self.answer(tools,scope='focused'))
        unread = tools.model_passage(tools.by_id['two:speech:0'])['id']
        limited.claims[0].evidence_ids = [unread]
        limited.answer = 'A qualified account. ['+unread+']'
        with self.assertRaisesRegex(ValueError,'unread|read transcript evidence'):
            tools.validate_model_answer(limited)

    def test_unavailable_inventory_limit_is_distinct_from_missing_available_context(self):
        tools = self.tools()
        tools.read_passages(['two:speech:0'],0)
        tools.searched_interviews.update(tools.speech)
        policy = original_dossier(tools)['publication_policy']
        self.assertEqual(policy['missing_searched_recordings'],{'count':0,'recording_ids':[]})
        self.assertEqual(policy['missing_read_context_recordings'],{'count':0,'recording_ids':[]})
        self.assertEqual(policy['unavailable_inventory_count'],1)
        self.assertTrue(policy['corpus_with_unavailable_inventory_requires_limitations'])
        with self.assertRaisesRegex(ValueError,'pending or unavailable'):
            tools.validate_model_answer(self.answer(tools,scope='corpus'))
        tools.validate_model_answer(self.answer(tools,scope='corpus',limitations=['One inventory asset is unavailable.']))

    def test_fully_supported_multi_source_floor_is_visible_and_unchanged(self):
        tools = self.tools()
        key = 'a'*32  # Canonical IDs must not leak into policy.
        row = dict(tools.by_id['one:speech:0'],id=key+':speech:0',assetId=key)
        tools.assets[key] = dict(id=key,name='third.mp4',status='ready')
        tools.speech[key] = [row]
        tools.by_id[row['id']] = row
        tools.pool[row['id']] = row
        tools.read_passages(['two:speech:0',row['id']],0)
        tools.minimum_interviews = 3
        policy = original_dossier(tools)['publication_policy']
        self.assertEqual(policy['minimum_distinct_supported_blocks_for_answered'],3)
        self.assertNotIn(key,json.dumps(policy))
        refs = [tools.model_passage(tools.by_id[item])['id']
                for item in ('one:speech:0','two:speech:0',row['id'])]
        answer = ResearchAnswer(outcome='answered',answer='A combined explanation. ['+', '.join(refs)+']',
                                claims=[ResearchClaim(text='A combined explanation.',evidence_ids=refs)])
        with self.assertRaisesRegex(ValueError,'three distinct'):
            tools.validate_model_answer(answer)

    def test_policy_precedes_arrays_roundtrips_losslessly_and_refreshes_from_live_state(self):
        tools = self.tools()
        dossier = original_dossier(tools)
        before = deepcopy(dossier)
        # Rendering must move policy before sources even for an appended field.
        policy = dossier.pop('publication_policy')
        dossier['publication_policy'] = policy
        rendered = dossier_for_model(dossier)
        self.assertLess(rendered.index('"publication_policy"'),rendered.index('"interviews"'))
        self.assertEqual(dossier_from_model(rendered),before)
        self.assertEqual(dossier,before)
        tools.minimum_interviews = 9
        refreshed = original_dossier(tools)
        self.assertEqual(refreshed['interviews'],before['interviews'])
        self.assertEqual(refreshed['publication_policy']['minimum_distinct_cited_recordings'],9)
        self.assertEqual(before['publication_policy']['minimum_distinct_cited_recordings'],1)
        self.assertNotEqual(dossier_for_model(refreshed),rendered)
        self.assertEqual(tools.read_ids,{'one:speech:0'})


if __name__ == '__main__':
    unittest.main()
