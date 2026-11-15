import copy
import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from development.premiere_annotation_plan import validate, load_vocabulary, load_plan, vocabulary_hash, PROMPT_VERSION


class EditorialSuggestionPlanTests(unittest.TestCase):
    def setUp(self):
        self.sentences = [{'start': i * 10, 'end': i * 10 + 5, 'text': f'Saved line {i}.'} for i in range(20)]
        self.vocabulary = ['Technique', 'Observation', 'Interview']
        self.plan = {'tags': ['Technique', 'Observation'], 'summary': 'Explains the recording method.',
                     'passages': [{'sentence_start': 0, 'sentence_end': 1, 'note': 'Defines the sampling method.'},
                                  {'sentence_start': 4, 'sentence_end': 5, 'note': 'Explains a limitation.'}]}

    def test_quotes_and_times_come_from_saved_sentences_not_generated_text(self):
        result = validate(self.plan, self.sentences, self.vocabulary)
        self.assertEqual(result['passages'][0]['quote'], 'Saved line 0. Saved line 1.')
        self.assertEqual((result['passages'][0]['start'], result['passages'][0]['end']), (0, 15))
        self.assertEqual(result['passages'][0]['origin'], 'ai_suggested')
        self.assertNotIn('quote', self.plan['passages'][0])

    def test_hallucinated_or_reversed_or_unbounded_ranges_are_rejected(self):
        for start, end in [(20, 21), (4, 1), (False, 1), (0, 15)]:
            plan = copy.deepcopy(self.plan); plan['passages'][0].update(sentence_start=start, sentence_end=end)
            with self.assertRaises(ValueError): validate(plan, self.sentences, self.vocabulary)

    def test_uncontrolled_new_tag_or_repeated_tag_is_rejected(self):
        for tags in [['Invented category', 'Observation'], ['Observation', 'Observation'], ['Observation']]:
            plan = copy.deepcopy(self.plan); plan['tags'] = tags
            with self.assertRaises(ValueError): validate(plan, self.sentences, self.vocabulary)

    def test_excessive_summary_or_missing_passage_is_rejected(self):
        plan = copy.deepcopy(self.plan); plan['summary'] = 'word ' * 41
        with self.assertRaises(ValueError): validate(plan, self.sentences, self.vocabulary)
        plan = copy.deepcopy(self.plan); plan['passages'].pop()
        with self.assertRaises(ValueError): validate(plan, self.sentences, self.vocabulary)


class VocabularyBindingTests(unittest.TestCase):
    vocabulary = ['Technique', 'Observation', 'Interview']

    def test_resume_requires_same_vocabulary_order_content_project_and_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'plan.json'
            plan = load_plan(path, 'project', 'model', self.vocabulary)
            self.assertEqual(plan['vocabulary_sha256'], vocabulary_hash(self.vocabulary))
            path.write_text(json.dumps(plan))
            self.assertEqual(load_plan(path, 'project', 'model', self.vocabulary), plan)
            for changed in [list(reversed(self.vocabulary)), ['Other topic', 'Interview']]:
                with self.assertRaisesRegex(ValueError, 'Vocabulary changed'):
                    load_plan(path, 'project', 'model', changed)
            with self.assertRaises(ValueError): load_plan(path, 'other-project', 'model', self.vocabulary)
            with self.assertRaises(ValueError): load_plan(path, 'project', 'other-model', self.vocabulary)

    def test_unbound_legacy_plan_requires_exact_detached_binding_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'plan.json'
            plan = load_plan(path, 'project', 'model', self.vocabulary)
            del plan['vocabulary_sha256']; path.write_text(json.dumps(plan)); before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'no vocabulary binding'):
                load_plan(path, 'project', 'model', self.vocabulary)
            binding = {'plan_sha256': hashlib.sha256(before).hexdigest(), 'project_id': 'project',
                       'prompt_version': PROMPT_VERSION, 'vocabulary_sha256': vocabulary_hash(self.vocabulary)}
            path.with_suffix('.vocabulary.json').write_text(json.dumps(binding))
            self.assertEqual(load_plan(path, 'project', 'model', self.vocabulary)['vocabulary_sha256'], vocabulary_hash(self.vocabulary))
            self.assertEqual(path.read_bytes(), before)
            path.write_text(json.dumps({**plan, 'changed': True}))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                load_plan(path, 'project', 'model', self.vocabulary)

    def test_reviewed_plan_cannot_acquire_unreviewed_suggestions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'plan.json'
            plan = load_plan(path, 'project', 'model', self.vocabulary); plan['review_state'] = 'reviewed'
            path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'reviewed plan'):
                load_plan(path, 'project', 'model', self.vocabulary)

    def test_vocabulary_file_is_external_generic_validated_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'tags.json'; path.write_text(json.dumps(self.vocabulary))
            self.assertEqual(load_vocabulary(path), self.vocabulary)
            for invalid in [[], ['Repeated', 'repeated'], ['Tag, delimiter', 'Other'], ['Tag\x00', 'Other']]:
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError): load_vocabulary(path)
