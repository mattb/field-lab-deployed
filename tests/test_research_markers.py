"""Explicit evidence markers become pinned source links without source guessing."""
import unittest
from urllib.parse import unquote

from ditnotes.research import ResearchAnswer, ResearchClaim
from test_research_tools import fixture


class ResearchMarkerTests(unittest.TestCase):
    def tools(self):
        tools = fixture()
        tools.model_coverage()
        rows = tools.read_passages(['one:speech:0', 'two:speech:0'], 0)['passages']
        aliases = {tools.provenance_aliases()['evidence'][r['id']]: r['id'] for r in rows}
        return tools, aliases['one:speech:0'], aliases['two:speech:0']

    def answer(self, one, two, text):
        return ResearchAnswer(outcome='answered', answer=text,
            claims=[ResearchClaim(text='First account.', evidence_ids=[one]),
                    ResearchClaim(text='Second account.', evidence_ids=[two])])

    def test_multiple_repeated_and_legacy_markers_are_idempotent_pinned_links(self):
        tools, one, two = self.tools()
        answer = self.answer(one, two, f'Compare [{one}, {two}]. Again [{one}]. Legacy ({one},{two}).')
        answer.claims[0].text += f' [{one}]'
        tools.validate_model_answer(answer)
        expanded = answer.answer
        self.assertEqual(expanded.count('[source]('), 5)
        self.assertEqual(unquote(expanded).count('#shot=one:speech:0&t=0.0'), 3)
        self.assertIn('[source](', answer.claims[0].text)
        tools.validate_model_answer(answer)
        self.assertEqual(answer.answer, expanded)
        tools.commit_answer(answer)
        self.assertEqual({r['id'] for r in tools.shown}, {'one:speech:0', 'two:speech:0'})

    def test_unknown_unread_and_unclaimed_markers_fail_closed(self):
        for bad in ('e99999', 'unread', 'unclaimed'):
            with self.subTest(bad=bad):
                tools, one, two = self.tools()
                if bad == 'unread':
                    bad = tools.model_passage(tools.by_id['one:speech:1'])['id']
                elif bad == 'unclaimed':
                    bad = tools.read_passages(['one:speech:1'], 0)['passages'][0]['id']
                answer = self.answer(one, two, f'Compare [{one},{two}]. Bad [{bad}].')
                with self.assertRaisesRegex(ValueError, 'ungrounded'):
                    tools.validate_model_answer(answer)

    def test_claim_marker_cannot_borrow_a_different_claims_evidence(self):
        tools, one, two = self.tools()
        answer = self.answer(one, two, f'Compare [{one},{two}].')
        answer.claims[0].text += f' [{two}]'
        with self.assertRaisesRegex(ValueError, 'ungrounded'):
            tools.validate_model_answer(answer)

    def test_code_escapes_existing_links_plain_ids_and_ranges_are_not_autolinked(self):
        tools, one, two = self.tools()
        unchanged = (f'Inline `[{one}]` and ``({two})``. Escaped \\[{one}] and \\({two}).\n'
                     f'```text\n[{one},{two}]\n```\n'
                     f'    [{one}]\n\n'
                     f'Plain {one}; range [{one}-{two}].\n'
                     f'[label [{one}]](https://example.invalid/)\n')
        answer = self.answer(one, two, unchanged+f'Actual [{one},{two}].')
        tools.validate_model_answer(answer)
        self.assertTrue(answer.answer.startswith(unchanged))
        self.assertEqual(answer.answer.count('[source]('), 2)

    def test_simple_marker_before_existing_link_is_not_swallowed_by_link_guard(self):
        tools, one, two = self.tools()
        answer = self.answer(one, two, f'First [{one}], [external](https://example.invalid/), second [{two}].')
        tools.validate_model_answer(answer)
        self.assertEqual(answer.answer.count('[source]('), 2)
        self.assertIn('[external](https://example.invalid/)', answer.answer)

    def test_code_and_comments_cannot_count_as_playable_inline_source_coverage(self):
        tools, one, two = self.tools()
        source = tools.open_sources([one, two])['sources']
        links = ' '.join('[source]('+row['citation_url']+')' for row in source)
        for text in ('`'+links+'`', '<!-- '+links+' -->', '```text\n'+links+'\n```'):
            with self.subTest(text=text):
                answer = self.answer(one, two, text)
                with self.assertRaisesRegex(ValueError, 'every interview'):
                    tools.validate_model_answer(answer)


if __name__ == '__main__':
    unittest.main()
