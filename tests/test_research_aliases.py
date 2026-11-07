"""Short model references never replace canonical source provenance."""
import tempfile
import unittest
from urllib.parse import unquote

from ditnotes.research import ResearchAnswer, ResearchClaim
from ditnotes.research_store import ResearchBoard
from test_research_tools import fixture


class ResearchAliasesTests(unittest.TestCase):
    def test_inventory_and_passages_use_bijective_request_local_aliases(self):
        tools = fixture()
        coverage = tools.model_coverage()
        self.assertEqual([row['id'] for row in coverage['interviews']], ['i1', 'i2'])
        rows = tools.read_passages(coverage['interviews'][0]['opening_passage_ids'], 0)['passages']
        aliases = tools.provenance_aliases()
        self.assertEqual(aliases['snapshot_revision'], tools.snapshot_revision)
        self.assertEqual({aliases['evidence'][r['id']] for r in rows}, {'one:speech:0', 'one:speech:1'})
        self.assertEqual({r['asset_id'] for r in rows}, {'i1'})
        self.assertEqual(len(set(aliases['evidence'].values())), len(aliases['evidence']))
        # Returned diagnostic mappings cannot mutate the live resolver.
        aliases['evidence'].clear()
        self.assertTrue(tools.provenance_aliases()['evidence'])

    def test_answer_links_claims_and_saved_sources_expand_to_canonical_ids(self):
        tools = fixture()
        short = tools.model_coverage()['interviews'][0]['opening_passage_ids'][0]
        passage = tools.read_passages([short], 0)['passages'][0]
        link = tools.open_sources([short])['sources'][0]['citation_url']
        self.assertIn('#shot='+short, link)
        text = passage['text']
        answer = ResearchAnswer(outcome='answered', answer='[Evidence]('+link+')',
            claims=[ResearchClaim(text='The interview discusses uncertainty.', evidence_ids=[short], quotes=[text])], result_ids=[short])
        tools.validate_answer(answer)
        tools.commit_answer(answer)
        canonical = 'one:speech:0'
        self.assertEqual(answer.result_ids, [canonical])
        self.assertEqual(answer.claims[0].evidence_ids, [canonical])
        self.assertEqual(answer.claims[0].quotes, [text])
        self.assertIn('#shot='+canonical, unquote(answer.answer))
        self.assertEqual(tools.shown[0]['id'], canonical)
        self.assertEqual(tools.shown[0]['asset_id'], 'one')
        self.assertEqual(tools.shown[0]['snapshot_revision'], tools.snapshot_revision)
        self.assertEqual(tools.shown[0]['text'], text)
        with tempfile.TemporaryDirectory() as project, ResearchBoard(project, 'alias-test') as board:
            question = board.create('What is uncertain?')
            board.save_answer(question['id'], 1, {**answer.model_dump(), 'results': tools.shown,
                'snapshot_revision': tools.snapshot_revision, 'provenance_aliases': tools.provenance_aliases()})
            saved = board.get(question['id'])['latest_answer']
            self.assertEqual(saved['claims'][0]['evidence_ids'], [canonical])
            self.assertEqual(saved['results'][0]['id'], canonical)
            self.assertEqual(saved['provenance_aliases']['evidence'][short], canonical)

    def test_unknown_and_foreign_unissued_aliases_are_rejected(self):
        tools = fixture()
        tools.model_coverage()
        for method in (tools.read_passages, tools.open_sources):
            with self.assertRaises(ValueError):
                method(['e99999'])
        with self.assertRaisesRegex(ValueError, 'Unknown research interviews'):
            tools.search_interviews('uncertainty', ['i99999'])
        other = fixture()
        with self.assertRaises(ValueError):
            other.read_passages(['e1'])
        self.assertEqual(other.provenance_aliases()['evidence'], {})
        bad = ResearchAnswer(outcome='not_supported', answer='[Invented](#shot=e99999&t=0)')
        with self.assertRaisesRegex(ValueError, 'ungrounded'):
            tools.validate_answer(bad)

    def test_invented_or_duplicate_citation_times_are_replaced_by_pinned_time(self):
        tools = fixture()
        tools.model_coverage()
        row = tools.read_passages(['one:speech:1'], 0)['passages'][0]
        for suffix in ('&t=9999', '&t=nan', '&t=99&t=500', '&other=x&t=300', ''):
            with self.subTest(suffix=suffix):
                answer = ResearchAnswer(outcome='answered', answer='[Evidence](#shot='+row['id']+suffix+')',
                    claims=[ResearchClaim(text='Supported claim', evidence_ids=[row['id']])], result_ids=[row['id']])
                tools.validate_answer(answer)
                self.assertEqual(unquote(answer.answer), '[Evidence](#shot=one:speech:1&t=10.0)')
                # Revalidation is idempotent for already-expanded references.
                tools.validate_answer(answer)
                self.assertEqual(unquote(answer.answer), '[Evidence](#shot=one:speech:1&t=10.0)')

    def test_interview_alias_filter_and_repeat_read_preserve_canonical_tracking(self):
        tools = fixture()
        inventory = tools.model_coverage()
        tools.call = lambda **kwargs: {'rows': list(tools.speech['one'])}
        tools.infer = lambda action, values, query='': {'vectors': [[1.0]+[0.0]*383]} if action == 'embed_text' else {'scores': [1]*len(values)}
        result = tools.search_interviews('uncertainty', [inventory['interviews'][0]['id']], 1)
        short = result['results'][0]['id']
        first = tools.read_passages([short], 0)
        again = tools.read_passages([short], 0)
        self.assertTrue(first['passages'])
        self.assertEqual(again['passages'], [])
        self.assertEqual(again['previously_read_ids'], [short])
        self.assertEqual(tools.searched_interviews, {'one'})
        self.assertEqual(tools.read_interviews, {'one'})
        self.assertEqual(tools.read_ids, {'one:speech:0'})
        self.assertEqual(result['coverage']['searched_interview_ids'], ['i1'])


if __name__ == '__main__':
    unittest.main()
