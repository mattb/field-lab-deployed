# Work queue

## Completed: global search and navigation

- Collapsible Files hierarchy, flat Tags and Faces lists, per-entry file counts and alphabetical/newest/most-files sorting.
- One project-wide search over text, metadata, identities and sampled images using the existing Orama indexes.
- Keep idle Premiere checks silent; display only known pending changes, errors and confirmed saves.

## Implemented: unified People model and review

Implemented as an additive shared Person registry, 2026-11-12. See [People](PEOPLE.md) for migration safety, review controls and limitations.

- A project-global Person owns a name, multiple face exemplars/clusters, voice exemplars/clusters, operator notes and appearances. Keep both embedding spaces independent internally.
- One People navigation list and review screen. Display face samples beside playable voice windows; support audio-only people with a placeholder until linked. Remove separate face/voice navigation and naming workflows.
- Names belong to the person and carry to reviewed face and voice evidence. Preserve existing IDs/labels and migrate with atomic transactions, audit history and interruption recovery; surface conflicting existing labels for review.
- Use repeated clean co-occurrence as a proposed face–voice association, with explicit evidence and provenance. Do not infer identity from voiceover, one shared clip, an offscreen voice, or an ambiguous group shot.
- Distinguish visible, heard and both in appearances and search tools. A voice match alone must not satisfy a request to see a person in a shot.
- Review/accept/reject links, merge people and detach incorrect evidence. Make decisions reversible without deleting embeddings or original observations. Automatically proposed links stay separate from reviewed assertions.
- Share the current Orama search architecture and durable project storage; avoid parallel identity/search silos. Preserve offline operation and benchmark overhead.
- Buddy review plus migration, interruption, wrong-match, audio-only and cross-clip acceptance tests before rollout. Included in v0.1.0-alpha.8.
