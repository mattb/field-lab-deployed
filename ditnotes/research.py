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
    result_ids: list[str] = Field(default_factory=list)
    scope: Literal['focused', 'corpus'] = 'focused'
    limitations: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)


def _normalized(value):
    return ' '.join(value.split())


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
    def __init__(self, server, notify=lambda *_: None):
        self.server = server
        self.notify = notify
        self.pool = {}
        self.shown = []
        self.lock = threading.Lock()
        self._alias_lock = threading.RLock()
        self._alias_lock = threading.RLock()
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
        value.update(searched_interview_ids=sorted(self.searched_interviews), read_interview_ids=sorted(self.read_interviews))
        return value

    def model_coverage(self, inventory=True):
        """Lean tool context; full immutable provenance stays in saved results."""
        value = self.interview_coverage()
        result = {key: value[key] for key in ('ready', 'total', 'pending', 'failed', 'searched_interview_ids', 'read_interview_ids')}
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
            forward = self._canonical_aliases[kind]
            if canonical not in forward:
                short = ('e' if kind == 'evidence' else 'i') + str(len(forward)+1)
                forward[canonical] = short
                self._aliases[kind][short] = canonical
            return forward[canonical]

    def _source_id(self, kind, value):
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
        def links(text):
            def canonical_link(match):
                key = self._source_id('evidence', unquote(match[1]))
                # A citation points to this pinned passage, never a timestamp
                # invented by the model or supplied in a stale URL suffix.
                seconds = float(self.by_id[key].get('seconds', 0))
                return '#shot='+quote(key, safe='')+'&t='+str(seconds)
            try:
                return re.sub(r'#shot=([^&\s)]+)(?:&[^\s)]*)?', canonical_link, text)
            except ValueError as error:
                raise ValueError('The answer contains an ungrounded source link') from error
        result_ids = [self._source_id('evidence', key) for key in answer.result_ids]
        claims = [(claim, [self._source_id('evidence', key) for key in claim.evidence_ids], links(claim.text))
                  for claim in answer.claims]
        text = links(answer.answer)
        answer.result_ids = result_ids
        answer.answer = text
        for claim, ids, text in claims:
            claim.evidence_ids = ids
            claim.text = text
        return answer

    def model_passage(self, row):
        # Never shorten the source text: save tokens by removing repeated
        # provenance, not by silently dropping testimony or its qualifications.
        value = {'id': self._alias('evidence', row['id']), 'asset_id': self._alias('interviews', row['assetId']), 'interview_label': self._label(self.assets[row['assetId']]),
                 'seconds': row.get('seconds', 0), 'end': row.get('end', row.get('seconds', 0)), 'text': row['text']}
        if row.get('personNames') and len(row.get('speakerIds', [])) == len(row['personNames']) == 1:
            value['speaker_name'] = row['personNames'][0]
        return value

    def search_interviews(self, query, interview_ids=None, sources_per_interview=2):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError('Use a concise research search query')
        if isinstance(sources_per_interview, bool) or not isinstance(sources_per_interview, int) or not 1 <= sources_per_interview <= 4:
            raise ValueError('Return one to four sources per interview')
        if interview_ids is not None and not isinstance(interview_ids, list):
            raise ValueError('Choose published interviews with transcripts')
        ids = list(self.speech) if interview_ids is None else list(dict.fromkeys(self._source_id('interviews', key) for key in interview_ids))
        if not ids or any(not isinstance(i, str) or i not in self.speech for i in ids):
            raise ValueError('Choose published interviews with transcripts')
        self.notify('Searching interviews', {'query': query, 'interviews': len(ids)})
        vector = self.infer('embed_text', [query])['vectors'][0]
        packet = self.call(action='research_search', query=query, asset_ids=ids, vector=vector,
                           per_interview=min(12, sources_per_interview*3))
        candidates = [r for r in packet['rows'] if r.get('id') in self.by_id]
        self.searched_interviews.update(ids)
        self.evidence_checks += 1
        scores = self.infer('rerank', [r['text'] for r in candidates], query)['scores'] if candidates else []
        if len(scores) != len(candidates):
            raise ValueError('Reranker returned an incorrect score count')
        groups = {i: [] for i in ids}
        for row, score in zip(candidates, scores):
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError('Reranker returned an invalid score')
            if score >= -6:
                groups[row['assetId']].append((score, row))
        results = []
        for aid in ids:
            selected = sorted(groups[aid], key=lambda pair: -pair[0])[:sources_per_interview]
            for score, row in selected:
                self.pool[row['id']] = row
                results.append(self.model_passage(row))
        return {'query': query, 'results': results, 'coverage': self.model_coverage(inventory=False),
                'note': 'Candidates need surrounding-context reading before they can support a final answer.'}

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
            rows = self.speech[row['assetId']]
            index = next(i for i, candidate in enumerate(rows) if candidate['id'] == key)
            for candidate in rows[max(0, index-surrounding_context):index+surrounding_context+1]:
                selected[candidate['id']] = candidate
        for key, row in selected.items():
            self.pool[key] = row
            self.read_ids.add(key)
            self.read_interviews.add(row['assetId'])
        self.evidence_checks += 1
        self.notify('Reading transcript evidence', {'passages': len(selected), 'interviews': len({r['assetId'] for r in selected.values()})})
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

    def validate_answer(self, answer):
        answer = self.normalize_answer(answer)
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
        cited = set()
        for claim in answer.claims:
            if not claim.text.strip() or not claim.evidence_ids:
                raise ValueError('Each research claim needs original transcript evidence')
            for key in claim.evidence_ids:
                if key not in self.read_ids or key not in self.by_id:
                    raise ValueError('Each claim must cite retrieved and read transcript evidence')
                cited.add(key)
            text = ' '.join(_normalized(self.pool[key]['text']) for key in claim.evidence_ids)
            for exact_quote in claim.quotes:
                if not exact_quote.strip() or _normalized(exact_quote) not in text:
                    raise ValueError('A quotation does not match its original transcript evidence')
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
        answer = self.normalize_answer(answer)
        self.validate_answer(answer)
        if answer.outcome == 'not_supported':
            return
        if answer.outcome == 'answered' and getattr(self, 'minimum_interviews', 1) >= 3:
            distinct = {_normalized(claim.text).casefold() for claim in answer.claims}
            if len(distinct) < 3:
                raise ValueError('A supported multi-interview synthesis needs at least three distinct evidence-linked claims, rather than one omnibus claim; otherwise return partial with limitations')
        cited_interviews = {self.by_id[key]['assetId'] for claim in answer.claims for key in claim.evidence_ids}
        linked_ids = {unquote(key) for key in re.findall(r'#shot=([^&\s)]+)', answer.answer)}
        linked_interviews = {self.by_id[key]['assetId'] for key in linked_ids}
        if cited_interviews - linked_interviews:
            raise ValueError('Include playable inline source links in the answer for every interview used in its claims; obtain them with open_sources')

    def commit_answer(self, answer):
        answer = self.normalize_answer(answer)
        self.validate_answer(answer)
        self.shown = []
        if answer.result_ids:
            self.open_sources(answer.result_ids)
        return {'coverage': self.interview_coverage(), 'results': self.shown,
                'snapshot_revision': self.snapshot_revision}
