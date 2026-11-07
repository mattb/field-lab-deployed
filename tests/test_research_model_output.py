"""Model synthesis must expose distinct claims and playable source coverage."""
import unittest
from urllib.parse import unquote

from ditnotes.research import ResearchAnswer, ResearchClaim
from test_research_tools import fixture


class ResearchModelOutputTests(unittest.TestCase):
    def tools(self):
        tools = fixture()
        # Three disposable interviews; no project transcript is read.
        tools.assets['three'] = {**tools.assets['two'], 'id': 'three', 'name': 'three.mp4'}
        tools.speech['three'] = [{**tools.speech['two'][0], 'id': 'three:speech:0', 'assetId': 'three'}]
        tools.by_id.update({row['id']: row for row in tools.speech['three']})
        for rows in tools.speech.values():
            tools.pool.update({row['id']: row for row in rows})
        tools.read_passages(['one:speech:0', 'two:speech:0', 'three:speech:0'], 0)
        tools.minimum_interviews = 3
        return tools

    def answer(self, tools, *, links=True):
        ids = ['one:speech:0', 'two:speech:0', 'three:speech:0']
        answer = ResearchAnswer(outcome='answered', answer='A supported comparison.', result_ids=ids,
            claims=[ResearchClaim(text='Comparison point '+str(i), evidence_ids=[key]) for i, key in enumerate(ids)])
        if links:
            sources = tools.open_sources(ids)['sources']
            answer.answer += ' ' + ' '.join('[Source]('+source['citation_url']+')' for source in sources)
        return answer

    def test_omnibus_and_duplicate_claims_rejected_only_at_model_gate(self):
        tools = self.tools()
        answer = self.answer(tools)
        answer.claims = [ResearchClaim(text='One omnibus comparison', evidence_ids=answer.result_ids)]
        tools.validate_answer(answer)
        with self.assertRaisesRegex(ValueError, 'three distinct'):
            tools.validate_model_answer(answer)
        answer.claims = [ResearchClaim(text=' Same claim ', evidence_ids=[key]) for key in answer.result_ids]
        with self.assertRaisesRegex(ValueError, 'three distinct'):
            tools.validate_model_answer(answer)

    def test_no_inline_links_and_missing_interview_link_are_rejected(self):
        tools = self.tools()
        answer = self.answer(tools, links=False)
        tools.validate_answer(answer)
        with self.assertRaisesRegex(ValueError, 'every interview'):
            tools.validate_model_answer(answer)
        first = tools.open_sources(answer.result_ids[:2])['sources']
        answer.answer += ' ' + ' '.join('[Source]('+source['citation_url']+')' for source in first)
        with self.assertRaisesRegex(ValueError, 'every interview'):
            tools.validate_model_answer(answer)

    def test_distinct_claims_and_one_inline_link_per_interview_pass(self):
        tools = self.tools()
        answer = self.answer(tools)
        tools.validate_model_answer(answer)
        tools.commit_answer(answer)
        self.assertEqual(set(answer.result_ids), {'one:speech:0', 'two:speech:0', 'three:speech:0'})
        self.assertIn('#shot=one:speech:0&t=0.0', unquote(answer.answer))
        self.assertEqual({row['asset_id'] for row in tools.shown}, {'one', 'two', 'three'})

    def test_partial_with_limitations_can_have_one_claim_but_needs_its_link(self):
        tools = self.tools()
        answer = self.answer(tools)
        answer.outcome = 'partial'
        answer.limitations = ['Only one interview supports this narrower point.']
        answer.claims = answer.claims[:1]
        answer.result_ids = answer.result_ids[:1]
        source = tools.open_sources(answer.result_ids)['sources'][0]
        answer.answer = '[Limited source]('+source['citation_url']+')'
        tools.validate_model_answer(answer)

    def test_quoted_neighbor_text_requires_neighbor_id_not_the_search_hit(self):
        tools = self.tools()
        tools.minimum_interviews = 1
        tools.read_passages(['one:speech:1'], 0)
        hit = tools.model_passage(tools.by_id['one:speech:0'])['id']
        neighbor = tools.model_passage(tools.by_id['one:speech:1'])['id']
        link = tools.open_sources([hit])['sources'][0]['citation_url']
        quote = tools.by_id['one:speech:1']['text']
        answer = ResearchAnswer(outcome='answered', answer='[Evidence]('+link+')', result_ids=[hit],
            claims=[ResearchClaim(text='The interview describes uncertainty.', evidence_ids=[hit], quotes=[quote])])
        with self.assertRaisesRegex(ValueError, 'quotation'):
            tools.validate_model_answer(answer)
        answer.result_ids = [neighbor]
        answer.claims[0].evidence_ids = [neighbor]
        source = tools.open_sources([neighbor])['sources'][0]
        answer.answer = '[Correct evidence]('+source['citation_url']+')'
        tools.validate_model_answer(answer)
        self.assertEqual(answer.result_ids, ['one:speech:1'])
        self.assertEqual(answer.claims[0].quotes, [quote])

    def test_model_schema_hides_result_ids_but_api_field_is_still_validated(self):
        self.assertNotIn('result_ids', ResearchAnswer.model_json_schema()['properties'])
        answer = ResearchAnswer(outcome='not_supported', answer='No evidence.', result_ids=['canonical-source'])
        self.assertEqual(answer.model_dump()['result_ids'], ['canonical-source'])
        with self.assertRaises(ValueError):
            ResearchAnswer(outcome='not_supported', answer='No evidence.', result_ids=42)

    def test_model_derives_claim_source_union_ignoring_unused_selection(self):
        tools = self.tools()
        answer = self.answer(tools)
        answer.result_ids = ['unused-invented-selection']
        tools.validate_model_answer(answer)
        self.assertEqual(answer.result_ids, ['one:speech:0', 'two:speech:0', 'three:speech:0'])
        # Programmatic validation still requires an explicit exact union.
        answer.result_ids = answer.result_ids[:1]
        with self.assertRaises(ValueError):
            tools.validate_answer(answer)

    def test_derived_union_does_not_permit_unknown_unread_or_unclaimed_links(self):
        for bad_id in ('e99999', 'two:speech:2'):
            with self.subTest(bad_id=bad_id):
                tools = self.tools()
                answer = self.answer(tools)
                answer.claims[0].evidence_ids = [bad_id]
                with self.assertRaises(ValueError):
                    tools.validate_model_answer(answer)
        tools = self.tools()
        answer = self.answer(tools)
        answer.answer += ' [Invented](#shot=e99999&t=10)'
        with self.assertRaisesRegex(ValueError, 'ungrounded'):
            tools.validate_model_answer(answer)

    def test_quote_error_points_only_to_exact_already_read_passage_alias(self):
        tools = self.tools()
        tools.minimum_interviews = 1
        tools.read_passages(['one:speech:1'], 0)
        correct = tools.model_passage(tools.by_id['one:speech:1'])['id']
        hit = tools.model_passage(tools.by_id['one:speech:0'])['id']
        quote = tools.by_id['one:speech:1']['text']
        answer = ResearchAnswer(outcome='answered', answer='Evidence.',
            claims=[ResearchClaim(text='A claim.', evidence_ids=[hit], quotes=[quote])])
        with self.assertRaises(ValueError) as caught:
            tools.validate_model_answer(answer)
        message = str(caught.exception)
        self.assertIn('"quote_excerpt": "'+quote+'"', message)
        self.assertIn('"cited_passage_ids": ["'+hit+'"]', message)
        self.assertIn('"exact_match_already_read_passage_ids": ["'+correct+'"]', message)
        self.assertNotIn('two:speech:1', tools.provenance_aliases()['evidence'].values())
        answer.claims[0].quotes = ['x'*1000]
        with self.assertRaises(ValueError) as caught:
            tools.validate_model_answer(answer)
        self.assertLess(len(str(caught.exception)), 650)


if __name__ == '__main__':
    unittest.main()
