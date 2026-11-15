"""Deterministic saved-Parakeet to Adobe timed-word transcript projection.

Reads only the current sealed result selected by the catalog. Parakeet subword
pieces are joined at their real whitespace boundaries; each word keeps the union
of the recorded token spans. No ASR, alignment inference or face-to-voice naming
is performed. Missing token timing is reported rather than fabricated.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import re
import uuid
from urllib.parse import unquote

VERSION = 1
MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_WORDS = 500_000
_NAMESPACE = uuid.UUID('f52c9fb3-c466-4bcf-9184-ae25079802e8')


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid transcript ' + label)
    return float(value)


def _span(row, duration):
    start = _number(row.get('start'), 'start')
    if 'end' not in row and 'duration' not in row:
        raise ValueError('Transcript end or duration must be explicitly recorded')
    end = _number(row['end'], 'end') if 'end' in row else start + _number(row['duration'], 'duration')
    if start < 0 or end < start or end > duration + .001:
        raise ValueError('Transcript timing lies outside source media')
    return start, end


def _read(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('Saved transcript exceeds the bounded JSON limit')
    with path.open('rb') as handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError('Saved transcript exceeds the bounded JSON limit')
    return json.loads(data)


def _signature(path):
    info = path.stat()
    return str(path), info.st_mtime_ns, info.st_size, info.st_ino


def _person_map(people):
    rows = people.values() if isinstance(people, dict) else people
    return {row['id']: row for row in rows}


def _canonical(person_id, people):
    seen = set()
    while person_id in people:
        if person_id in seen:
            raise ValueError('Transcript speaker identity merge cycle')
        seen.add(person_id)
        target = people[person_id].get('merged_into')
        if not target:
            return person_id
        person_id = target
    return None


def _uses_default_language(track):
    value = track.get('language') or track.get('detected_language') or track.get('tags', {}).get('language')
    return not isinstance(value, str) or value.lower() in ('und', 'unknown', '')


def _language(track):
    # The indexer currently uses multilingual Parakeet. An unspecified stream tag
    # is not evidence for a language other than the saved ASR's language.
    language = track.get('language') or track.get('detected_language') or track.get('tags', {}).get('language')
    if not isinstance(language, str) or language in ('und', 'unknown', ''):
        return 'en-us'
    return {'en': 'en-us', 'eng': 'en-us'}.get(language.lower(), language.lower())


def _local_people(track, observations, people):
    stream = track.get('stream_index', 0)
    revision = track.get('diarization', {}).get('model', {}).get('revision', '')
    prefix = f'stream-{stream}:diar-{revision[:12]}:' if revision else None
    candidates = {}
    for row in observations:
        local = row['local_speaker']
        if prefix and local.startswith(prefix):
            person = _canonical(row['person_id'], people)
            if person:
                candidates.setdefault(local[len(prefix):], set()).add(person)
    result = {local: next(iter(ids)) if len(ids) == 1 else None for local, ids in candidates.items()}
    for row in track.get('speaker_assignments', []):
        local = str(row['local_speaker'])
        if local not in result:
            result[local] = _canonical(row.get('person_id'), people)
    return result


def _word_speaker(parts):
    # A subword spanning an overlap or unresolved voice remains unassigned.
    # A partial word is never a new speaker identity solely because its BPE
    # boundary happened to fall inside a diarization gap.
    speech_parts = [part for part in parts if any(character.isalnum() for character in part['text'])]
    timed_parts = [part for part in speech_parts if part['end'] > part['start']]
    speakers = {part['speaker'] for part in (timed_parts or speech_parts)}
    return next(iter(speakers)) if len(speakers) == 1 else None


def convert_tracks(tracks, duration, *, content_id, people=(), observations=()):
    """Return Adobe JSON and coverage from source-relative saved token spans."""
    duration = _number(duration, 'source duration')
    people = _person_map(people)
    segments, speaker_rows = [], {}
    token_count = word_count = sentence_count = unresolved_words = 0
    for track_order, track in enumerate(tracks):
        language = _language(track)
        local_people = _local_people(track, observations, people)
        diar = track.get('diarization', {})
        token_speakers = {(row['sentence_index'], row['token_index']): row for row in diar.get('token_speakers', [])}
        for sentence_index, sentence in enumerate(track.get('sentences', [])):
            if not str(sentence.get('text', '')).strip():
                continue
            sentence_count += 1
            tokens = sentence.get('tokens')
            if not isinstance(tokens, list) or not tokens:
                return None, {'state': 'unavailable', 'reason': 'word_timing_unavailable', 'sentences': sentence_count}
            if ' '.join(''.join(token.get('text', '') for token in tokens).split()) != ' '.join(sentence['text'].split()):
                raise ValueError('Parakeet token text does not cover its saved sentence')
            parts, words = [], []
            previous_start = -1.
            for token_index, token in enumerate(tokens):
                text = token.get('text')
                if not isinstance(text, str) or len(text.strip().split()) > 1:
                    raise ValueError('A saved token has no precise single-word boundary')
                start, end = _span(token, duration)
                if start < previous_start - .001:
                    raise ValueError('Parakeet token timestamps are not ordered')
                previous_start = start
                token_count += 1
                if token_count > MAX_WORDS * 10:
                    raise ValueError('Saved transcript token count exceeds the limit')
                assignment = token_speakers.get((sentence_index, token_index), {})
                local = assignment.get('local_speaker')
                speaker = None
                if local is not None and not assignment.get('overlap'):
                    local = str(local)
                    person = local_people.get(local)
                    speaker = ('person:' + person) if person else f'local:{content_id}:{track.get("stream_index", track_order)}:{local}'
                confidence = _number(token.get('confidence', 1.), 'confidence')
                if not 0 <= confidence <= 1:
                    raise ValueError('Transcript confidence is outside [0,1]')
                if text[:1].isspace() and parts:
                    words.append(parts)
                    parts = []
                if text.strip() or parts:
                    parts.append({'text': text, 'start': start, 'end': end, 'confidence': confidence, 'speaker': speaker})
            if parts:
                words.append(parts)
            current_segment = None
            for pieces in words:
                text = ''.join(piece['text'] for piece in pieces).strip()
                if not text:
                    continue
                start = min(piece['start'] for piece in pieces)
                end = max(piece['end'] for piece in pieces)
                speaker_key = _word_speaker(pieces)
                if speaker_key is None:
                    unresolved_words += 1
                    speaker_key = f'unassigned:{content_id}:{track.get("stream_index", track_order)}'
                speaker_id = str(uuid.uuid5(_NAMESPACE, speaker_key))
                if speaker_id not in speaker_rows:
                    person_id = speaker_key.removeprefix('person:') if speaker_key.startswith('person:') else None
                    if person_id:
                        name = people[person_id].get('name') or ('Voice ' + person_id.removeprefix('person_')[:8])
                    elif speaker_key.startswith('local:'):
                        name = 'Voice ' + speaker_key.rsplit(':', 1)[-1] + ' (unlinked)'
                    else:
                        name = 'Unassigned voice'
                    speaker_rows[speaker_id] = {'id': speaker_id, 'name': name}
                word = {'text': text, 'start': start, 'duration': end - start,
                        'confidence': min(piece['confidence'] for piece in pieces),
                        'eos': bool(re.search(r'[.!?][\"\'\u2019\u201d)]*$', text)), 'type': 'word', 'tags': []}
                if current_segment is None or current_segment['speaker'] != speaker_id:
                    current_segment = {'start': start, 'duration': end - start, 'language': language,
                                       'speaker': speaker_id, 'words': [], '_order': (track_order, sentence_index)}
                    segments.append(current_segment)
                current_segment['words'].append(word)
                current_segment['duration'] = max(current_segment['duration'], end - current_segment['start'])
                word_count += 1
                if word_count > MAX_WORDS:
                    raise ValueError('Transcript word count exceeds the limit')
    if not word_count:
        return None, {'state': 'empty', 'reason': 'no_speech', 'sentences': sentence_count, 'words': 0}
    segments.sort(key=lambda row: (row['start'], row['_order']))
    for row in segments:
        del row['_order']
    result = {'language': segments[0]['language'], 'segments': segments,
              'speakers': sorted(speaker_rows.values(), key=lambda row: row['id'])}
    validate_adobe(result, duration)
    return result, {'state': 'ready', 'source': 'saved_parakeet_tokens', 'version': VERSION,
                    'sentences': sentence_count, 'tokens': token_count, 'words': word_count,
                    'unassigned_words': unresolved_words,
                    'language_default_used': any(_uses_default_language(track) for track in tracks),
                    'timestamp_basis': 'source_media_seconds'}


def validate_adobe(document, duration):
    """Reject invalid native input before it can enter the durable outbox."""
    if not isinstance(document, dict) or not isinstance(document.get('segments'), list) or not isinstance(document.get('speakers'), list):
        raise ValueError('Invalid Adobe transcript schema')
    speakers = {row['id'] for row in document['speakers']}
    if len(speakers) != len(document['speakers']):
        raise ValueError('Duplicate native transcript speaker')
    count = 0
    for segment in document['segments']:
        segment_start, segment_end = _span(segment, duration)
        previous_start = -1.
        if segment.get('speaker') not in speakers or not isinstance(segment.get('words'), list):
            raise ValueError('Invalid native transcript segment')
        for word in segment['words']:
            start, end = _span(word, duration)
            if start < segment_start - .001 or end > segment_end + .001 or start < previous_start - .001:
                raise ValueError('Native transcript word lies outside or out of order in its segment')
            previous_start = start
            if word.get('type') != 'word' or not isinstance(word.get('text'), str) or not word['text'].strip():
                raise ValueError('Invalid native transcript word')
            count += 1
    if not count or count > MAX_WORDS:
        raise ValueError('Invalid native transcript word count')
    json.dumps(document, allow_nan=False)
    return document


@lru_cache(maxsize=64)
def _project_cached(result_sig, alignment_sig, content_id, profile_id, duration, identity_json):
    people, observations = json.loads(identity_json)
    result = _read(Path(result_sig[0]))
    tracks = result.get('tracks', [])
    if alignment_sig:
        alignment = _read(Path(alignment_sig[0]))
        if alignment.get('content_id') == content_id and alignment.get('profile_id') == profile_id:
            tracks = alignment.get('tracks', tracks)
    return convert_tracks(tracks, duration, content_id=content_id, people=people, observations=observations)


def project_transcript(project, asset, *, people=(), observations=()):
    """Read the selected sealed ASR result and current identity names, without writes."""
    duration = _number(asset['duration'], 'source duration')
    explicit = asset.get('transcript_json')
    if explicit is not None:
        return validate_adobe(explicit, duration), {'state': 'ready', 'source': 'provided_timed_words'}
    url = asset.get('result_url')
    if not url:
        return None, {'state': 'unavailable', 'reason': 'word_timing_unavailable'}
    if not isinstance(url, str) or not url.startswith('/notes/.objects/'):
        raise ValueError('Transcript result must be the catalog-selected local object')
    relative = Path(unquote(url.removeprefix('/notes/')))
    content_id = str(asset.get('content_id') or asset['id'])
    if relative.is_absolute() or '..' in relative.parts or len(relative.parts) != 4 or relative.parts[1] != content_id or relative.name != 'result.json':
        raise ValueError('Transcript object does not belong to this media content')
    notes = Path(project).resolve(strict=True) / '_DIT_NOTES'
    result_path = (notes / relative).resolve(strict=True)
    if not result_path.is_relative_to(notes.resolve()) or not result_path.is_file():
        raise ValueError('Transcript result escapes project storage')
    alignment_path = notes / (content_id + '-identity-alignment.json')
    alignment_sig = None
    if alignment_path.is_file():
        aligned = alignment_path.resolve(strict=True)
        if not aligned.is_relative_to(notes.resolve()):
            raise ValueError('Identity alignment escapes project storage')
        alignment_sig = _signature(aligned)
    person_rows = sorted(_person_map(people).values(), key=lambda row: row['id'])
    person_rows = [{key: row.get(key) for key in ('id', 'name', 'merged_into')} for row in person_rows]
    observations = sorted({(row['local_speaker'], row['person_id']) for row in observations})
    identity_json = json.dumps([person_rows, [{'local_speaker': local, 'person_id': person} for local, person in observations]], sort_keys=True)
    return _project_cached(_signature(result_path), alignment_sig, content_id, relative.parts[2], duration, identity_json)


def transcript_summary(document):
    words = [word for segment in document['segments'] for word in segment['words']]
    return {'word_count': len(words), 'first_start': words[0]['start'],
            'last_end': words[-1]['start'] + words[-1]['duration'],
            'first_text': words[0]['text'], 'last_text': words[-1]['text']}
