#!/usr/bin/env python3
"""Apply an explicitly reviewed local editorial plan, additively and atomically."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ditnotes.project_store import ProjectStore, normalized_tag


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def apply_plan(project, plan):
    project = Path(project).resolve(strict=True)
    catalog = json.loads((project / '_DIT_NOTES/catalog.json').read_text())
    if plan.get('schema_version') != 1 or plan.get('project_id') != catalog['project']['id']:
        raise ValueError('Plan and project identities differ')
    if plan.get('origin') != 'ai_suggested' or plan.get('review_state') != 'reviewed_for_trial':
        raise ValueError('An explicitly reviewed AI plan is required')
    for key in ('model', 'prompt_version'):
        if not isinstance(plan.get(key), str) or not plan[key].strip() or len(plan[key]) > 200:
            raise ValueError('Invalid model or prompt provenance')
    vocabulary_hash = plan.get('vocabulary_sha256')
    if vocabulary_hash is not None and (not isinstance(vocabulary_hash, str) or not re.fullmatch('[0-9a-f]{64}', vocabulary_hash)):
        raise ValueError('Invalid vocabulary provenance')
    if not isinstance(plan.get('assets'), list) or not 1 <= len(plan['assets']) <= 10000:
        raise ValueError('Invalid plan asset count')
    digest = hashlib.sha256(canonical(plan).encode()).hexdigest()
    assets = {a['id']: a for a in catalog['assets']}
    seen = set()
    for row in plan['assets']:
        asset = assets.get(row['asset_id'])
        if not asset or row['asset_id'] in seen:
            raise ValueError('Unknown or repeated plan asset')
        seen.add(row['asset_id'])
        if any(row[k] != asset[k] for k in ('content_id', 'relative_path')):
            raise ValueError('Plan media identity changed')
        sentences = asset['transcript']
        actual_hash = hashlib.sha256(json.dumps(sentences, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if row['transcript_sha256'] != actual_hash:
            raise ValueError('Saved transcript changed; review a new plan')
        if not isinstance(row.get('tags'), list) or not 1 <= len(row['tags']) <= 64:
            raise ValueError('Invalid plan tags')
        for tag in row['tags']:
            normalized_tag(tag)
        if not isinstance(row.get('summary'), str) or not row['summary'].strip():
            raise ValueError('A clip summary is required')
        # Reviewed legacy plans predate vocabulary binding. New generated plans
        # bind the vocabulary hash into the stable annotation identity as well.
        key_input = plan['project_id'] + ':' + row['content_id'] + ':' + plan['prompt_version']
        if vocabulary_hash is not None:
            key_input += ':' + vocabulary_hash
        expected_key = hashlib.sha256(key_input.encode()).hexdigest()
        if row.get('annotation_key') != expected_key or row.get('origin') != 'ai_suggested':
            raise ValueError('Invalid annotation identity')
        if not isinstance(row.get('passages'), list) or not 1 <= len(row['passages']) <= 20:
            raise ValueError('Invalid passage count')
        for passage in row['passages']:
            first, last = passage['sentence_start'], passage['sentence_end']
            if type(first) is not int or type(last) is not int or not 0 <= first <= last < len(sentences):
                raise ValueError('Invalid sentence range')
            start, end = sentences[first]['start'], sentences[last]['end']
            quote = ' '.join(s['text'] for s in sentences[first:last + 1])
            if (passage['start'], passage['end'], passage['quote']) != (start, end, quote):
                raise ValueError('Passage differs from saved evidence')
            if not 0 <= start < end <= asset['duration'] or end-start > 120.1:
                raise ValueError('Passage exceeds media bounds')
            if not isinstance(passage.get('note'), str) or not passage['note'].strip():
                raise ValueError('A passage note is required')
    with ProjectStore(project / '_DIT_NOTES/project.sqlite3', project_id=catalog['project']['id']) as store:
        with store._transaction():
            receipt_key = 'editorial_plan:' + digest
            existing = store.db.execute('SELECT value FROM meta WHERE key=?', (receipt_key,)).fetchone()
            if existing:
                return json.loads(existing[0]) | {'replayed': True}
            annotation_ids = []
            for row in plan['assets']:
                asset_digest = hashlib.sha256(canonical({'asset': row, 'model': plan['model'], 'prompt': plan['prompt_version']}).encode()).hexdigest()
                asset_receipt_key = 'editorial_asset:' + asset_digest
                prior_asset = store.db.execute('SELECT value FROM meta WHERE key=?', (asset_receipt_key,)).fetchone()
                if prior_asset:
                    annotation_ids.extend(json.loads(prior_asset[0]))
                    continue
                provenance = {'origin': 'ai_suggested', 'model': plan['model'],
                              'prompt_version': plan['prompt_version'], 'plan_asset_sha256': asset_digest,
                              'transcript_sha256': row['transcript_sha256']}
                notes = [(None, None, row['summary'], None)] + [
                    (p['start'], p['end'], p['note'], p['quote']) for p in row['passages']]
                asset_annotation_ids = []
                for index, (start, end, text, quote) in enumerate(notes):
                    metadata = provenance | ({'quote': quote} if quote is not None else {})
                    asset_annotation_ids.append(store.add_annotation(row['content_id'], 'AI editorial suggestion: ' + text,
                        start=start, end=end, metadata=metadata, source='editorial_suggestion',
                        source_id=row['annotation_key'] + ':' + str(index)))
                current = store.asset_tags(row['content_id'])
                union = {}
                for name in [t['name'] for t in current['tags']] + row['tags']:
                    key, spelling = normalized_tag(name)
                    union.setdefault(key, spelling)
                store.set_asset_tags(row['content_id'], list(union.values()), current['revision'])
                store.db.execute('INSERT INTO meta(key,value) VALUES(?,?)', (asset_receipt_key, canonical(asset_annotation_ids)))
                annotation_ids.extend(asset_annotation_ids)
            result = {'plan_sha256': digest, 'assets': len(plan['assets']), 'annotation_ids': annotation_ids}
            store._audit('editorial_plan_applied', result | {'origin': 'ai_suggested', 'model': plan['model'], 'plan': plan})
            store.db.execute('INSERT INTO meta(key,value) VALUES(?,?)', (receipt_key, canonical(result)))
            return result | {'replayed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--plan', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(apply_plan(args.project, json.loads(args.plan.read_text())), indent=2))
