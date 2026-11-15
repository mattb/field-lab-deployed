"""Independent boundary checks: no guessed timing or silently lost word breaks."""
import unittest
from ditnotes.premiere_transcript import convert_tracks, validate_adobe
from tests.test_premiere_transcript import track


class TranscriptReviewTests(unittest.TestCase):
    def test_start_without_an_actual_end_or_duration_is_not_a_timed_word(self):
        source = track()
        del source['sentences'][0]['tokens'][0]['end']
        with self.assertRaises(ValueError):
            convert_tracks([source], 20, content_id='invented')

    def test_unknown_token_word_boundary_format_fails_instead_of_joining_words(self):
        source = track()
        source['sentences'][0]['tokens'] = [
            {'text': 'Sea', 'start': 10, 'end': 10.4},
            {'text': 'ice,', 'start': 10.6, 'end': 11},
            {'text': 'changes.', 'start': 11.2, 'end': 12},
        ]
        with self.assertRaises(ValueError):
            convert_tracks([source], 20, content_id='invented')

    def test_explicit_adobe_word_must_be_contained_in_its_declared_segment(self):
        source = track()
        document, _ = convert_tracks([source], 20, content_id='invented')
        document['segments'][0]['duration'] = .1
        with self.assertRaises(ValueError):
            validate_adobe(document, 20)


if __name__ == '__main__':
    unittest.main()
