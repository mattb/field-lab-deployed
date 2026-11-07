"""Anonymous saved voice context must preserve groups without inventing roles."""
import unittest

from test_research_tools import fixture


class ResearchVoiceContextTests(unittest.TestCase):
    def test_request_global_aliases_preserve_saved_group_equality(self):
        tools = fixture()
        one, two = tools.speech['one'][0], tools.speech['two'][0]
        one.update(speakerIds=['saved-group-a'], personNames=[])
        two.update(speakerIds=['saved-group-a', 'saved-group-b'], personNames=[])
        first, second = tools.model_passage(one), tools.model_passage(two)
        self.assertEqual(first['voice_groups'], ['v1'])
        self.assertEqual(second['voice_groups'], ['v1', 'v2'])
        self.assertEqual(tools.provenance_aliases()['voices'],
                         {'v1':'saved-group-a', 'v2':'saved-group-b'})
        self.assertEqual(tools._source_id('voices', 'v2'), 'saved-group-b')
        self.assertNotIn('saved-group-a', str(first))
        self.assertNotIn('speaker_name', first)
        self.assertFalse({'role', 'speaker_role', 'overlap', 'verified_person'} & first.keys())

    def test_unknown_voice_context_and_saved_name_policy_remain_separate(self):
        tools = fixture()
        row = tools.speech['one'][0]
        row.update(speakerIds=[], personNames=[])
        self.assertEqual(tools.model_passage(row)['voice_groups'], [])
        row.update(speakerIds=['named-group'], personNames=['Saved label'])
        self.assertEqual(tools.model_passage(row)['speaker_name'], 'Saved label')
        row['speakerIds'].append('another-group')
        multiple = tools.model_passage(row)
        self.assertEqual(len(multiple['voice_groups']), 2)
        self.assertNotIn('speaker_name', multiple)
        self.assertNotIn('overlap', multiple)  # Two groups can be successive turns.

    def test_voice_aliases_do_not_modify_original_text_time_or_stream(self):
        tools = fixture()
        row = tools.speech['one'][0]
        row.update(speakerIds=['group-z'], stream=3, seconds=10, end=100,
                   text='A question? An answer with its qualification.')
        original = dict(row)
        packet = tools.model_passage(row)
        self.assertEqual(row, original)
        for field in ('text', 'stream', 'seconds', 'end'):
            self.assertEqual(packet[field], row[field])
        # New requests get their own numbering; these are not canonical IDs.
        other = fixture()
        other.speech['one'][0]['speakerIds'] = ['different-saved-group']
        self.assertEqual(other.model_passage(other.speech['one'][0])['voice_groups'], ['v1'])
        self.assertEqual(other.provenance_aliases()['voices']['v1'], 'different-saved-group')


if __name__ == '__main__':
    unittest.main()
