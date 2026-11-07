"""Transcript research tools over the existing sealed catalog and Orama index.

Generated answers are never added to the retrieval corpus. Every tool instance
pins one catalog/vector generation, including its speaker-label snapshot.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import subprocess
import threading
from typing import Literal
from urllib.parse import quote, unquote

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from .catalog_index import read_snapshot
from .chat_tools import FootageTools


class ResearchClaim(BaseModel):
    text: str
    evidence_ids: list[str]
    quotes: list[str] = Field(default_factory=list, description='Exact transcript excerpts only; preserve wording and uncertainty.')


class ResearchAnswer(BaseModel):
    outcome: Literal['answered', 'partial', 'not_supported']
    answer: str
    claims: list[ResearchClaim] = Field(default_factory=list)
    result_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    scope: Literal['focused', 'corpus'] = 'focused'
    limitations: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)


def _normalized(value):
    return ' '.join(value.split())


def _citation_marker_spans(text):
    """Conservative citation syntax outside Markdown code, escapes and links.

    Keep the original Markdown byte-for-byte apart from explicit markers. The
    installed CommonMark parser identifies block code; exact backtick runs
    protect inline code without rendering/reformatting the document.
    """
    from markdown_it import MarkdownIt
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    protected = []
    for token in MarkdownIt('commonmark').parse(text):
        if token.type in ('fence', 'code_block') and token.map:
            protected.append((offsets[token.map[0]], offsets[token.map[1]]))
    cursor = 0
    for match in re.finditer(r'`+', text):
        if match.start() < cursor or any(a <= match.start() < b for a, b in protected): continue
        # An escaped opening delimiter is ordinary text, not code.
        before = text[:match.start()]
        if (len(before)-len(before.rstrip('\\'))) % 2: continue
        close = re.search(r'(?<!`)'+re.escape(match[0])+r'(?!`)', text[match.end():])
        if close:
            cursor = match.end()+close.end()
            protected.append((match.start(), cursor))
    # Existing links are not citation markers, including their labels/destinations.
    for match in re.finditer(r'!?\[(?:\\.|[^\[\]\n]|\[[^\[\]\n]*\])*\](?:\([^()\n]*\)|\[[^\[\]\n]*\])|<[^\n>]+>', text):
        protected.append(match.span())
    pattern = r'\[(e\d+(?:\s*,\s*e\d+)*)\]|\((e\d+(?:\s*,\s*e\d+)*)\)'
    for match in re.finditer(pattern, text):
        before = text[:match.start()]
        if (len(before)-len(before.rstrip('\\'))) % 2: continue
        if any(a <= match.start() < b for a, b in protected): continue
        yield match, re.split(r'\s*,\s*', match[1] or match[2])


def catalog_coverage(catalog, snapshot_revision=None):
    """CPU-only inventory for polling; creates no process, model or DB writer."""
    rows = {}
    for row in catalog.get('search_projection', {}).get('documents', []):
        if row.get('kind') == 'speech': rows.setdefault(row.get('assetId'), []).append(row)
    statuses = {}
    interviews = []
    for asset in catalog.get('assets', []):
        status = asset.get('status', 'unknown')
        statuses[status] = statuses.get(status, 0) + 1
        speech = rows.get(asset['id'], []) if status == 'ready' else []
        path = asset.get('relative_path') or asset.get('name', '')
        label = str(Path(path).parent) if str(Path(path).parent) != '.' else asset.get('name', '')
        interviews.append({'id': asset['id'], 'asset_id': asset['id'], 'label': label, 'status': status,
            'transcript_available': bool(speech), 'passage_count': len(speech), 'duration_seconds': asset.get('duration', 0),
            'opening_passage_ids': [r['id'] for r in sorted(speech, key=lambda r: (float(r.get('seconds', 0)), r['id']))[:2]]})
    ready = statuses.get('ready', 0)
    return {'snapshot_revision': snapshot_revision or catalog.get('catalog_revision'), 'ready': ready,
        'total': len(interviews), 'pending': statuses.get('pending', 0)+statuses.get('processing', 0),
        'failed': statuses.get('error', 0)+statuses.get('failed', 0), 'ready_interviews': ready,
        'total_interviews': len(interviews), 'transcribed_interviews': sum(r['transcript_available'] for r in interviews),
        'statuses': statuses, 'interviews': interviews, 'searched_interview_ids': [], 'read_interview_ids': [],
        'scope_note': 'Counts describe this pinned published snapshot. Search coverage is not proof that every word has been reviewed or that absent search hits prove absence.'}


class ResearchTools(FootageTools):
    MAX_READ_WORDS = 12000
    MAX_READ_PASSAGES = 800

    def __init__(self, server, notify=lambda *_: None):
        self.server = server
        self.notify = notify
        self.pool = {}
        self.shown = []
        self.lock = threading.Lock()
        self._alias_lock = threading.RLock()
        self._read_ledger_lock = threading.RLock()
        self.node = None
        self.evidence_checks = 0
        self.searched_interviews = set()
        self.read_interviews = set()
        self.read_ids = set()
        self.requires_diversity = False
        self.requires_corpus = False
        self.minimum_interviews = 1
        pair = read_snapshot(server.project)
        if not pair:
            raise ValueError('The project search index is not ready')
        self.catalog = pair['catalog']
        self.snapshot_revision = pair['vectors']['catalog_revision']
        self.assets = {a['id']: a for a in self.catalog.get('assets', [])}
        self.documents = self.catalog.get('search_projection', {}).get('documents', [])
        self.speech = {}
        for row in self.documents:
            if row.get('kind') == 'speech' and self.assets.get(row.get('assetId'), {}).get('status') == 'ready':
                self.speech.setdefault(row['assetId'], []).append(row)
        for rows in self.speech.values():
            rows.sort(key=lambda r: (float(r.get('seconds', 0)), r['id']))
        self.by_id = {row['id']: row for rows in self.speech.values() for row in rows}
        self.node = subprocess.Popen(
            [str(server.tool_root/'bin/node'), str(server.tool_root/'search/chat-search.mjs')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1)
        try:
            self.call(action='init', documents=self.documents, vectors=pair['vectors']['vectors'])
        except BaseException:
            self.close()
            raise

    def configure_request(self, prompt):
        self.requires_diversity = bool(re.search(
            r'\bacross\b|\bcompar\w*|\bcommon\b|\brecurr\w*|\bthemes?\b|\bpatterns?\b|\binterviews\b|\bperspectives\b', prompt, re.I))
        self.requires_corpus = bool(re.search(r'\ball (?:the )?(?:interviews|accounts|participants|scientists|researchers)\b|\b(?:entire|whole|full) corpus\b|\bevery interview\b|\bacross (?:the |these |our )interviews\b|\bcorpus[- ]wide\b', prompt, re.I))
        numbers = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
        noun = r'(?:interviews|accounts|participants|researchers|voices)'
        modifiers = r'(?:(?:different|separate|available|completed|ready|published)\s+)*'
        matches = re.findall(r'\b(?:at least|all)\s+(\d+|'+ '|'.join(numbers) +r')\s+'+modifiers+noun+r'\b', prompt, re.I)
        requested = [int(n) if n.isdigit() else numbers[n.lower()] for n in matches]
        if re.search(r'\bboth\s+'+modifiers+noun+r'\b', prompt, re.I): requested.append(2)
        self.minimum_interviews = max([2 if self.requires_diversity else 1, *requested])
        # A numbered "all" becomes corpus scope when it names the complete
        # published interview inventory, without treating "all four" in a
        # larger project as a request to review every other interview.
        all_counts = re.findall(r'\ball\s+(\d+|'+ '|'.join(numbers) +r')\s+'+modifiers+noun+r'\b', prompt, re.I)
        if any((int(n) if n.isdigit() else numbers[n.lower()]) >= len(self.speech) for n in all_counts):
            self.requires_corpus = True

    def _label(self, asset):
        path = asset.get('relative_path') or asset.get('name', '')
        return str(Path(path).parent) if str(Path(path).parent) != '.' else asset.get('name', '')

    def compact(self, row):
        asset = self.assets[row['assetId']]
        names = row.get('personNames') or []
        people = row.get('speakerIds') or []
        attribution = 'saved_voice_label' if len(people) == len(names) == 1 else 'unresolved_or_multiple_speakers'
        return {
            'id': row['id'], 'asset_id': row['assetId'], 'filename': asset.get('name', row.get('title', '')),
            'content_id': row.get('contentId') or asset.get('content_id'),
            'interview_label': self._label(asset), 'seconds': float(row.get('seconds', 0)),
            'end': float(row.get('end', row.get('seconds', 0))), 'text': row['text'],
            'kind': 'speech', 'text_source': 'ai_transcript', 'stream': row.get('stream'),
            'speaker_ids': people, 'speaker_names': names, 'speaker_attribution': attribution,
            'attribution_note': 'Interview labels describe the source recording, not who spoke every sentence. Questions are not testimony; unknown or overlapping speakers remain unresolved.',
            'snapshot_revision': self.snapshot_revision,
        }

    def interview_coverage(self):
        value = catalog_coverage(self.catalog, self.snapshot_revision)
        for rows in self.speech.values():
            for row in rows[:2]: self.pool.setdefault(row['id'], row)
        self.evidence_checks += 1
        value.update(searched_interview_ids=sorted(self.searched_interviews), read_interview_ids=sorted(self.read_interviews),
            read_passage_ids=sorted(self.read_ids),read_passages=len(self.read_ids),available_passages=len(self.by_id))
        value['read_budget'] = self.read_budget()
        return value

    def model_coverage(self, inventory=True):
        """Lean tool context; full immutable provenance stays in saved results."""
        value = self.interview_coverage()
        result = {key: value[key] for key in ('ready', 'total', 'pending', 'failed', 'searched_interview_ids', 'read_interview_ids','read_passages','available_passages')}
        result['read_budget'] = value['read_budget']
        for key in ('searched_interview_ids', 'read_interview_ids'):
            result[key] = [self._alias('interviews', source) for source in result[key]]
        if inventory:
            result['snapshot_revision'] = self.snapshot_revision
            result['interviews'] = [{'id': self._alias('interviews', row['id']), 'label': row['label'],
                                     'opening_passage_ids': [self._alias('evidence', key) for key in row['opening_passage_ids']]}
                                    for row in value['interviews'] if row['transcript_available']]
            result['note'] = 'Recording labels are not speaker identities. Inventory/search coverage is not full transcript review.'
        return result

    def _alias(self, kind, canonical):
        """Request-local presentation only; canonical source identities never change."""
        if not hasattr(self, '_alias_lock'): self._alias_lock = threading.RLock()
        with self._alias_lock:
            if not hasattr(self, '_aliases'):
                self._aliases = {'evidence': {}, 'interviews': {}}
                self._canonical_aliases = {'evidence': {}, 'interviews': {}}
            prefixes = {'evidence':'e', 'interviews':'i', 'voices':'v'}
            if kind not in prefixes:
                raise ValueError('Unknown research reference kind')
            forward = self._canonical_aliases.setdefault(kind, {})
            self._aliases.setdefault(kind, {})
            if canonical not in forward:
                short = prefixes[kind] + str(len(forward)+1)
                forward[canonical] = short
                self._aliases[kind][short] = canonical
            return forward[canonical]

    def _source_id(self, kind, value):
        if kind == 'voices':
            known = {person for row in self.by_id.values() for person in (row.get('speakerIds') or [])}
        else:
            known = self.by_id if kind == 'evidence' else self.speech
        if isinstance(value, str) and value in known:
            return value  # Internal callers and older API clients remain valid.
        mapped = getattr(self, '_aliases', {}).get(kind, {}).get(value) if isinstance(value, str) else None
        if mapped is None:
            raise ValueError('Unknown research '+kind+' reference; use an ID returned in this investigation')
        return mapped

    def provenance_aliases(self):
        """Immutable-copy diagnostics for interpreting this run's raw messages."""
        return {'snapshot_revision': self.snapshot_revision,
                **{kind: dict(rows) for kind, rows in getattr(self, '_aliases', {'evidence': {}, 'interviews': {}}).items()}}

    def normalize_answer(self, answer):
        """Expand model aliases before canonical evidence validation/persistence."""
        if not isinstance(answer, ResearchAnswer):
            answer = ResearchAnswer.model_validate(answer)
        citation_issues = []
        def links(text, allowed, location):
            def checked_reference(reference):
                try:key = self._source_id('evidence', reference)
                except ValueError:key = None
                category = ('unknown' if key is None else 'unread' if key not in self.read_ids
                    else 'read_but_not_in_this_claim' if key not in allowed and location.startswith('claim ')
                    else 'read_but_not_in_claim_union' if key not in allowed else None)
                if category:
                    marker = self._alias('evidence', key) if key is not None else reference if re.fullmatch(r'e\d{1,8}', reference) else 'unknown reference'
                    issue = {'marker': marker, 'category': category, 'location': location}
                    if len(citation_issues) < 4 and issue not in citation_issues:citation_issues.append(issue)
                    return None
                return key
            def source_link(key):
                seconds = float(self.by_id[key].get('seconds', 0))
                return '#shot='+quote(key, safe='')+'&t='+str(seconds)
            parts = []
            cursor = 0
            for match, aliases in _citation_marker_spans(text):
                parts.append(text[cursor:match.start()])
                keys = [checked_reference(alias) for alias in aliases]
                parts.append(', '.join('[source]('+source_link(key)+')' for key in keys) if all(keys) else match[0])
                cursor = match.end()
            parts.append(text[cursor:])
            text = ''.join(parts)
            def canonical_link(match):
                key = checked_reference(unquote(match[1]))
                # A citation points to this pinned passage, never a timestamp
                # invented by the model or supplied in a stale URL suffix.
                return source_link(key) if key is not None else match[0]
            return re.sub(r'#shot=([^&\s)]+)(?:&[^\s)]*)?', canonical_link, text)
        result_ids = [self._source_id('evidence', key) for key in answer.result_ids]
        claims = []
        for index, claim in enumerate(answer.claims):
            ids = [self._source_id('evidence', key) for key in claim.evidence_ids]
            claims.append((claim, ids, links(claim.text, set(ids), 'claim '+str(index))))
        text = links(answer.answer, set(result_ids), 'answer')
        limitations = [links(text, set(result_ids), 'limitations') for text in answer.limitations]
        followups = [links(text, set(result_ids), 'followups') for text in answer.followups]
        if citation_issues:
            raise ValueError('The answer contains an ungrounded source link. Citation issues: '
                +json.dumps(citation_issues, ensure_ascii=False)
                +'. Remove unknown or unread markers. For an already-read passage that supports the same point, add its exact marker ID to the relevant structured claim evidence_ids; otherwise remove that marker. Do not add unrelated evidence merely to pass validation.')
        answer.result_ids = result_ids
        answer.answer = text
        answer.limitations = limitations
        answer.followups = followups
        for claim, ids, text in claims:
            claim.evidence_ids = ids
            claim.text = text
        return answer

    def model_passage(self, row):
        # Never shorten the source text: save tokens by removing repeated
        # provenance, not by silently dropping testimony or its qualifications.
        # Recording labels are already mapped once in model_coverage. Stream and
        # end times stay visible so separate excerpts cannot imply one exchange.
        value = {'id': self._alias('evidence', row['id']), 'asset_id': self._alias('interviews', row['assetId']),
                 'seconds': row.get('seconds', 0), 'end': row.get('end', row.get('seconds', 0)),
                 'stream': row.get('stream'), 'text': row['text'],
                 'voice_groups': [self._alias('voices', person) for person in (row.get('speakerIds') or [])]}
        # Saved voice groups are fallible whole-sentence time associations, not
        # word attribution or identity/role assertions. Multiple groups can be
        # sequential turns or overlap; projection association can include other
        # audio streams. Keep their stored equality without inventing a person.
        if row.get('personNames') and len(row.get('speakerIds', [])) == len(row['personNames']) == 1:
            value['speaker_name'] = row['personNames'][0]
        return value

    @staticmethod
    def _select_context_hits(scored_rows, count):
        """Prefer distinct original dialogue windows after relevance gating.

        Keep the best hit first. Additional slots prefer non-overlapping
        +/-45-second windows in the same source/stream before near duplicates.
        If no distinct windows remain, preserve the ranked overlapping hits.
        This selects candidates only; original reads and their budgets remain
        separate, and temporal diversity does not establish different speakers.
        """
        ranked = sorted(scored_rows, key=lambda pair: -pair[0])
        if count <= 1:
            return ranked[:count]
        selected = []
        for pair in ranked:
            row = pair[1]
            start = float(row.get('seconds', 0))-45
            end = float(row.get('end', row.get('seconds', 0)))+45
            overlaps = any(
                row['assetId'] == other['assetId']
                and row.get('stream') == other.get('stream')
                and start <= float(other.get('end', other.get('seconds', 0)))+45
                and end >= float(other.get('seconds', 0))-45
                for _, other in selected)
            if not overlaps:
                selected.append(pair)
                if len(selected) == count:
                    return selected
        selected_ids = {row['id'] for _, row in selected}
        selected.extend(pair for pair in ranked if pair[1]['id'] not in selected_ids)
        return selected[:count]

    def search_interviews(self, query, interview_ids=None, sources_per_interview=2, cohort_cross_check=False, read_context=False):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError('Use a concise research search query')
        if isinstance(sources_per_interview, bool) or not isinstance(sources_per_interview, int) or not 1 <= sources_per_interview <= 4:
            raise ValueError('Return one to four sources per interview')
        if interview_ids is not None and not isinstance(interview_ids, list):
            raise ValueError('Choose published interviews with transcripts')
        if not isinstance(cohort_cross_check, bool):
            raise ValueError('Cohort cross-check must be enabled explicitly')
        if not isinstance(read_context, bool):
            raise ValueError('Context reading must be enabled explicitly')
        ids = list(self.speech) if interview_ids is None else list(dict.fromkeys(self._source_id('interviews', key) for key in interview_ids))
        if not ids or any(not isinstance(i, str) or i not in self.speech for i in ids):
            raise ValueError('Choose published interviews with transcripts')
        # Opt-in spike: lateral candidates stay within explicitly opened context,
        # never a new corpus, model, index, or an implied independent testimony.
        cross_ids = [key for key in self.speech if key not in ids and key in getattr(self, '_opening_cohort', set())][:3] \
            if cohort_cross_check and self.requires_diversity else []
        searched = ids+cross_ids
        self.notify('Searching interviews', {'query': query, 'interviews': len(searched)})
        vector = self.infer('embed_text', [query])['vectors'][0]
        packet = self.call(action='research_search', query=query, asset_ids=searched, vector=vector,
                           per_interview=12)  # Retrieval recall is independent of displayed/source count.
        candidates = [self.by_id[r['id']] for r in packet['rows'] if r.get('id') in self.by_id]
        self.searched_interviews.update(searched)
        self.evidence_checks += 1
        sentence_groups = self._original_sentence_groups()
        ranking_texts, candidate_texts = {}, []
        for row in candidates:
            texts = [row['text']]
            context = self._ranking_context_text(row, sentence_groups)
            if context is not None: texts.append(context)
            candidate_texts.append(texts)
            for text in texts: ranking_texts.setdefault(text, len(ranking_texts))
        # One batched inference, with duplicate original strings scored once.
        # Ranking context is not a read and never enters published source text.
        scores = self.infer('rerank', list(ranking_texts), query)['scores'] if ranking_texts else []
        if len(scores) != len(ranking_texts):
            raise ValueError('Reranker returned an incorrect score count')
        if any(not isinstance(score, (int, float)) or not math.isfinite(score) for score in scores):
            raise ValueError('Reranker returned an invalid score')
        groups = {i: [] for i in searched}
        for row, texts in zip(candidates, candidate_texts):
            score = max(scores[ranking_texts[text]] for text in texts)
            if score >= -6:
                groups[row['assetId']].append((score, row))
        results, cross_checks = [], []
        for aid in searched:
            selected = self._select_context_hits(groups[aid], 1 if aid in cross_ids else sources_per_interview)
            for score, row in selected:
                self.pool[row['id']] = row
                (cross_checks if aid in cross_ids else results).append(self.model_passage(row))
        response = {'query': query, 'results': results,
                **({'cross_checks': cross_checks, 'cross_check_note': 'At most three other opened cohort interviews were searched. These are read-required candidates, not proof of agreement or absence.'} if cohort_cross_check else {}),
                'note': 'Candidates need surrounding-context reading before they can support a final answer.'}
        if read_context:
            hits = [self.by_id[self._source_id('evidence', row['id'])] for row in results+cross_checks]
            windows, words, capped = {}, 0, False
            sentence_groups = self._original_sentence_groups()
            # A source sentence can span several projection chunks. Admit the
            # complete sibling union or omit it honestly under the search cap.
            for row in hits:
                unit = sentence_groups[self._original_sentence_key(row)]
                fresh = [part for part in unit if part['id'] not in windows]
                size = sum(len(part['text'].split()) for part in fresh)
                if sum(len(part['text'].split()) for part in unit) > 1200 or words+size > 8000 or len(windows)+len(fresh) > 240:
                    capped = True
                    continue
                windows.update({part['id']:part for part in fresh})
                words += size
            for hit in hits:
                start, end = float(hit.get('seconds', 0)), float(hit.get('end', hit.get('seconds', 0)))
                eligible = [row for row in self.speech[hit['assetId']] if row.get('stream') == hit.get('stream')
                    and float(row.get('seconds', 0)) <= end+45 and float(row.get('end', row.get('seconds', 0))) >= start-45]
                nearby = sorted(eligible, key=lambda row: min(abs(float(row.get('seconds', 0))-start), abs(float(row.get('end', row.get('seconds', 0)))-end)))
                window_words, seen_sentences = 0, set()
                for row in nearby:
                    sentence = self._original_sentence_key(row)
                    if sentence in seen_sentences: continue
                    seen_sentences.add(sentence)
                    unit = sentence_groups[sentence]
                    size = sum(len(part['text'].split()) for part in unit)
                    if window_words+size > 1200:
                        capped = True
                        continue
                    window_words += size
                    fresh = [part for part in unit if part['id'] not in windows]
                    fresh_words = sum(len(part['text'].split()) for part in fresh)
                    if words+fresh_words > 8000 or len(windows)+len(fresh) > 240:
                        capped = True
                        continue
                    windows.update({part['id']:part for part in fresh})
                    words += fresh_words
            admitted = self._read_context_batches(windows)
            response.update(context=admitted['passages'], previously_read_ids=admitted['previously_read_ids'])
            # All newly read words are returned in context once, not repeated
            # in the primary/cross-check routing lists as well.
            for rows in (results, cross_checks):
                for row in rows: row.pop('text', None)
            response['note'] = 'Original neighboring context is included below. Previously-read IDs refer to text returned earlier; candidates are not proof of agreement.'
            response['context_window_seconds'] = {'before': 45, 'after': 45, 'same_stream': True}
            if capped:
                response['context_limit_note'] = 'Context was limited to complete original ASR sentences: at most 1,200 words per hit window and 8,000 words/240 chunks per search. Some original context was omitted, and a candidate may remain unread if its whole sentence exceeded these limits. read_passages can request its complete original sentence under the run budget without text truncation.'
            if cohort_cross_check:
                response['cross_check_note'] = 'At most three other opened cohort interviews were searched; original context is included, not proof of agreement or absence.'
            if admitted.get('budget_exhausted'):
                response.update(budget_exhausted=True, read_budget=admitted['read_budget'], note=admitted['note'])
        response['coverage'] = self.model_coverage(inventory=False)
        return response

    def read_passages(self, ids, surrounding_context=1):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 30:
            raise ValueError('Read one to thirty retrieved passage IDs')
        try:
            ids = [self._source_id('evidence', key) for key in ids]
        except ValueError as error:
            raise ValueError('Read a passage returned by search or interview coverage') from error
        if isinstance(surrounding_context, bool) or not isinstance(surrounding_context, int) or not 0 <= surrounding_context <= 4:
            raise ValueError('Surrounding context must be zero to four neighboring passages')
        selected = {}
        for key in ids:
            row = self.pool.get(key)
            if not row or key not in self.by_id:
                raise ValueError('Read a passage returned by search or interview coverage')
            rows = [candidate for candidate in self.speech[row['assetId']]
                    if candidate.get('stream') == row.get('stream')]
            index = next(i for i, candidate in enumerate(rows) if candidate['id'] == key)
            for candidate in rows[max(0, index-surrounding_context):index+surrounding_context+1]:
                selected[candidate['id']] = candidate
        return self._read_original_passages(selected, 'Reading transcript evidence')

    def read_interview_openings(self, interview_ids):
        """Read bounded original opening context, never a generated overview."""
        if not isinstance(interview_ids, list) or not 1 <= len(interview_ids) <= 12:
            raise ValueError('Choose one to twelve published interviews with transcripts')
        ids = list(dict.fromkeys(self._source_id('interviews', key) for key in interview_ids))
        selected = {}
        summaries = []
        sentence_groups = self._original_sentence_groups()
        for key in ids:
            if self.assets.get(key, {}).get('status') != 'ready' or not self.speech.get(key):
                raise ValueError('Choose published interviews with nonempty transcripts')
            opening = {}
            words = 0
            for row in self.speech[key]:
                if row['id'] in opening: continue
                if not row['text'].split(): continue
                unit = sentence_groups[self._original_sentence_key(row)]
                opening.update({part['id']:part for part in unit})
                words = sum(len(part['text'].split()) for part in opening.values())
                # Orientation target is soft; never stop halfway through the
                # original ASR sentence. The global read budget remains hard.
                if words >= 350: break
            if not opening:
                raise ValueError('Choose published interviews with nonempty transcripts')
            selected.update(opening)
            summaries.append({'id': self._alias('interviews', key), 'passage_count': len(opening), 'word_count': words})
        result = self._read_original_passages(selected, 'Reading interview context')
        if result.get('budget_exhausted'): return result
        self._opening_cohort = getattr(self, '_opening_cohort', set()) | set(ids)
        result['interviews'] = summaries
        result['note'] += ' Opening passages orient the recording, not a verified speaker identity or an exhaustive account of the interview.'
        return result

    @staticmethod
    def _original_sentence_key(row):
        match = re.fullmatch(r'(.+):speech:(\d+):(\d+)', row['id'])
        if match and match[1] == row['assetId']:
            return ('sentence', row['assetId'], row.get('stream'), match[2])
        # Legacy IDs carry no trustworthy sentence grouping information.
        return ('row', row['id'])

    def _original_sentence_groups(self):
        groups = {}
        for row in self.by_id.values():
            groups.setdefault(self._original_sentence_key(row), []).append(row)
        for key, rows in groups.items():
            if key[0] == 'sentence':
                rows.sort(key=lambda row:int(row['id'].rsplit(':', 1)[1]))
        return groups

    def _ranking_context_text(self, hit, sentence_groups):
        """Bounded original context for ranking only, never evidence admission.

        Select whole same-stream sentences, closest first within +/-45 seconds.
        An oversized anchor keeps its bare-fragment scoring path; its sentence
        is not shortened to fit. The existing cross-encoder tokenizer retains
        its own 512-token pair limit independently of this 350-word bound.
        """
        anchor_key = self._original_sentence_key(hit)
        anchor = sentence_groups[anchor_key]
        selected = {row['id']:row for row in anchor}
        words = sum(len(row['text'].split()) for row in anchor)
        if words > 350: return None
        start, end = float(hit.get('seconds', 0)), float(hit.get('end', hit.get('seconds', 0)))
        nearby = [row for row in self.speech[hit['assetId']]
            if row.get('stream') == hit.get('stream') and float(row.get('seconds', 0)) <= end+45
            and float(row.get('end', row.get('seconds', 0))) >= start-45]
        nearby.sort(key=lambda row:min(abs(float(row.get('seconds', 0))-start),
                                      abs(float(row.get('end', row.get('seconds', 0)))-end)))
        seen = {anchor_key}
        for row in nearby:
            key = self._original_sentence_key(row)
            if key in seen: continue
            seen.add(key)
            unit = sentence_groups[key]
            size = sum(len(part['text'].split()) for part in unit)
            if words+size > 350: continue
            selected.update({part['id']:part for part in unit})
            words += size
        ordered = sorted(selected.values(), key=lambda row:(float(row.get('seconds', 0)),
            self._original_sentence_key(row), int(row['id'].rsplit(':', 1)[1])
            if self._original_sentence_key(row)[0] == 'sentence' else 0))
        return ' '.join(row['text'] for row in ordered)

    def _expand_original_sentences(self, selected):
        groups = self._original_sentence_groups()
        expanded = {}
        for row in selected.values():
            for part in groups[self._original_sentence_key(row)]:
                expanded[part['id']] = part
        return expanded

    def _read_original_passages(self, selected, stage):
        with self._ledger_lock():
            selected = self._expand_original_sentences(selected)
            refusal = self._read_budget_refusal(selected)
            if refusal: return refusal
            before = (dict(self.pool), set(self.read_ids), set(self.read_interviews),
                      set(getattr(self, '_model_read_ids', set())), self.evidence_checks)
            try:return self._publish_original_passages(selected, stage)
            except BaseException:
                self.pool, self.read_ids, self.read_interviews, self._model_read_ids, self.evidence_checks = before
                raise

    def _ledger_lock(self):
        if not hasattr(self, '_read_ledger_lock'): self._read_ledger_lock = threading.RLock()
        return self._read_ledger_lock

    def read_budget(self):
        """Canonical unique sources are the counters; rereads consume nothing."""
        with self._ledger_lock():
            ids = self.read_ids & self.by_id.keys()
            words = sum(len(self.by_id[key]['text'].split()) for key in ids)
            value = {'words': words, 'passages': len(ids), 'max_words': self.MAX_READ_WORDS,
                'max_passages': self.MAX_READ_PASSAGES, 'remaining_words': max(0, self.MAX_READ_WORDS-words),
                'remaining_passages': max(0, self.MAX_READ_PASSAGES-len(ids)),
                'exhausted': getattr(self, '_read_budget_exhausted', False)}
            if value['exhausted']:
                value['note'] = 'Some context requests were refused in full at this investigation\'s evidence budget. Refusal is not evidence of absence; all admitted original passages remain intact.'
            return value

    def _read_budget_refusal(self, selected):
        used = self.read_budget()
        fresh = set(selected)-self.read_ids
        words = sum(len(self.by_id[key]['text'].split()) for key in fresh)
        if used['words']+words <= self.MAX_READ_WORDS and used['passages']+len(fresh) <= self.MAX_READ_PASSAGES:
            return None
        self._read_budget_exhausted = True
        return {'passages': [], 'previously_read_ids': [self._alias('evidence', key) for key in sorted(set(selected)&self.read_ids)],
            'budget_exhausted': True, 'read_budget': self.read_budget(),
            'refused_interview_ids': [self._alias('interviews', key) for key in sorted({row['assetId'] for row in selected.values()})],
            'note': 'This entire context request was not admitted because the run-level evidence budget would be exceeded. No new passages were read. This is a processing limit, not evidence that a source lacks relevant information.'}

    def _read_context_batches(self, selected):
        with self._ledger_lock():
            selected = self._expand_original_sentences(selected)
            # Admit the complete union BEFORE the first batch so a qualifier in
            # a later batch cannot be omitted after earlier chunks were read.
            refusal = self._read_budget_refusal(selected)
            if refusal: return refusal
            before = (dict(self.pool), set(self.read_ids), set(self.read_interviews),
                      set(getattr(self, '_model_read_ids', set())), self.evidence_checks)
            context, previous = {}, set()
            try:
                self.pool.update(selected)
                references = [self._alias('evidence', key) for key in selected]
                for offset in range(0, len(references), 30):
                    batch = self.read_passages(references[offset:offset+30], surrounding_context=0)
                    if batch.get('budget_exhausted'):
                        self.pool, self.read_ids, self.read_interviews, self._model_read_ids, self.evidence_checks = before
                        return batch
                    context.update({row['id']: row for row in batch['passages']})
                    previous.update(batch['previously_read_ids'])
            except BaseException:
                self.pool, self.read_ids, self.read_interviews, self._model_read_ids, self.evidence_checks = before
                raise
            return {'passages': list(context.values()), 'previously_read_ids': sorted(previous-set(context))}

    def _publish_original_passages(self, selected, stage):
        for key, row in selected.items():
            self.pool[key] = row
            self.read_ids.add(key)
            self.read_interviews.add(row['assetId'])
        self.evidence_checks += 1
        self.notify(stage, {'passages': len(selected), 'interviews': len({r['assetId'] for r in selected.values()})})
        already_read = getattr(self, '_model_read_ids', set())
        fresh = {key: row for key, row in selected.items() if key not in already_read}
        self._model_read_ids = already_read | set(fresh)
        return {'passages': [self.model_passage(r) for r in fresh.values()],
                'previously_read_ids': [self._alias('evidence', key) for key in sorted(set(selected)-set(fresh))],
                'note': 'These are machine transcripts. Preserve uncertainty and distinguish the interviewer from the interviewee. Exact quotes should be checked against playback for editorial use.'}

    def open_sources(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 40:
            raise ValueError('Open one to forty read transcript references')
        ids = [self._source_id('evidence', key) for key in ids]
        selected = []
        for key in dict.fromkeys(ids):
            if key not in self.read_ids:
                raise ValueError('Read original transcript evidence before opening a source')
            row = self.pool[key]
            selected.append({**self.compact(row), 'assetId': row['assetId'], 'image': row.get('image', ''),
                'citation_url': '#shot='+quote(key, safe='')+'&t='+str(row.get('seconds', 0))})
        self.shown = selected
        return {'sources': [{'id': self._alias('evidence', row['id']),
                 'citation_url': '#shot='+self._alias('evidence', row['id'])+'&t='+str(row['seconds'])} for row in selected]}

    @staticmethod
    def _publish_validated_answer(original, candidate):
        if isinstance(original, ResearchAnswer):
            for field in ResearchAnswer.model_fields:
                setattr(original, field, getattr(candidate, field))
            return original
        return candidate

    def validate_answer(self, answer):
        candidate = ResearchAnswer.model_validate(answer).model_copy(deep=True)
        candidate = self.normalize_answer(candidate)
        self._validate_canonical_answer(candidate)
        return self._publish_validated_answer(answer, candidate)

    def _validate_canonical_answer(self, answer):
        if not answer.answer.strip():
            raise ValueError('Provide an answer or explain the missing evidence')
        # Reject fabricated links even in abstentions, before their early return.
        for key in re.findall(r'#shot=([^&\s)]+)', answer.answer):
            if unquote(key) not in answer.result_ids or unquote(key) not in self.read_ids:
                raise ValueError('The answer contains an ungrounded source link')
        if answer.outcome == 'not_supported':
            if answer.claims or answer.result_ids:
                raise ValueError('An unsupported answer must not publish unverified claims or sources')
            return
        if not answer.claims:
            raise ValueError('Supported research answers need explicit evidence-linked claims')
        cited = set();quote_errors=[]
        for claim_index,claim in enumerate(answer.claims):
            if not claim.text.strip() or not claim.evidence_ids:
                raise ValueError('Each research claim needs original transcript evidence')
            for key in claim.evidence_ids:
                if key not in self.read_ids or key not in self.by_id:
                    raise ValueError('Each claim must cite retrieved and read transcript evidence')
                cited.add(key)
            texts = [_normalized(self.pool[key]['text']) for key in claim.evidence_ids]
            for exact_quote in claim.quotes:
                # A quote must occur in one original chunk. Joining chunks can
                # fabricate speech across times, speakers or entire recordings.
                if not exact_quote.strip() or not any(_normalized(exact_quote) in text for text in texts):
                    # Give a precise correction route using evidence the model
                    # already read; never search, correct, or invent testimony.
                    normalized = _normalized(exact_quote)
                    elsewhere = [key for key in sorted(self.read_ids)
                                 if key not in claim.evidence_ids and key in self.by_id
                                 and normalized and normalized in _normalized(self.by_id[key]['text'])]
                    hint = {'claim_index':claim_index,'quote_excerpt': exact_quote[:160],
                            'cited_passage_ids': [self._alias('evidence', key) for key in claim.evidence_ids[:8]],
                            'exact_match_already_read_passage_ids': [self._alias('evidence', key) for key in elsewhere[:5]]}
                    if len(quote_errors)<4:quote_errors.append(hint)
        if quote_errors:
            raise ValueError('A quotation does not match its original transcript evidence. '
                +json.dumps(quote_errors, ensure_ascii=False)
                +'. Cite the already-read passages containing these exact quotes, or omit the quotations. No text correction was attempted.')
        if set(answer.result_ids) != cited:
            raise ValueError('Final result IDs must match the original sources cited by claims')
        for key in re.findall(r'#shot=([^&\s)]+)', answer.answer):
            if unquote(key) not in cited:
                raise ValueError('The answer contains an ungrounded source link')
        required = getattr(self, 'minimum_interviews', 2 if self.requires_diversity else 1)
        limited = answer.outcome == 'partial' and bool(answer.limitations)
        if len({self.by_id[k]['assetId'] for k in cited}) < required and not limited:
            raise ValueError(f'A cross-interview comparison needs evidence from at least {required} interviews; otherwise return partial with limitations or not_supported')
        corpus = answer.scope == 'corpus' or getattr(self, 'requires_corpus', False)
        if corpus and (set(self.speech)-self.searched_interviews or set(self.speech)-self.read_interviews) and not limited:
            raise ValueError('A corpus-wide answer requires searching and reading context from every published transcribed interview; otherwise return partial with limitations')
        if corpus and len(self.speech) < len(self.assets) and not answer.limitations:
            raise ValueError('A corpus-wide answer must state that pending or unavailable interviews were not reviewed')

    def validate_model_answer(self, answer):
        """Additional presentation gates for synthesized model output.

        Canonical storage/tool callers keep the narrower validate_answer
        contract; these checks ask the model to make its support inspectable.
        Semantic claim support still requires the independent evaluations.
        """
        original = answer
        answer = ResearchAnswer.model_validate(answer).model_copy(deep=True)
        # The model supplies claims once. Source cards are a deterministic
        # union, avoiding a second error-prone selection of the same IDs.
        answer.result_ids = list(dict.fromkeys(key for claim in answer.claims for key in claim.evidence_ids))
        answer = self.normalize_answer(answer)
        self._validate_canonical_answer(answer)
        self._validate_model_presentation(answer)
        return self._publish_validated_answer(original, answer)

    def _validate_model_presentation(self, answer):
        if answer.outcome == 'not_supported':
            return
        if len(answer.result_ids) > 40:
            raise ValueError('Publish at most forty original sources; select the supporting passages before returning the answer')
        if answer.outcome == 'answered' and getattr(self, 'minimum_interviews', 1) >= 3:
            distinct = {_normalized(claim.text).casefold() for claim in answer.claims}
            if len(distinct) < 3:
                raise ValueError('A supported multi-interview synthesis needs at least three distinct evidence-linked claims, rather than one omnibus claim; otherwise return partial with limitations')
        cited_interviews = {self.by_id[key]['assetId'] for claim in answer.claims for key in claim.evidence_ids}
        from markdown_it import MarkdownIt
        linked_ids = set()
        for token in MarkdownIt('commonmark').parse(answer.answer):
            if token.type != 'inline': continue
            for child in token.children or []:
                if child.type != 'link_open': continue
                match = re.match(r'#shot=([^&\s)]+)', child.attrGet('href') or '')
                if match: linked_ids.add(unquote(match[1]))
        linked_interviews = {self.by_id[key]['assetId'] for key in linked_ids}
        if cited_interviews - linked_interviews:
            raise ValueError('Include playable inline source links in the answer for every interview used in its claims; obtain them with open_sources')

    def commit_answer(self, answer):
        answer = self.validate_answer(answer)
        self.shown = []
        if answer.result_ids:
            self.open_sources(answer.result_ids)
        return {'coverage': self.interview_coverage(), 'results': self.shown,
                'snapshot_revision': self.snapshot_revision}
