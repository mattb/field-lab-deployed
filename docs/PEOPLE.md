# People

A project-global Person owns a name, notes and reviewed face/audio evidence. Face and voice embeddings remain separate spaces; a voice match is never treated as proof that someone is visible. Files, tags, People and the global search use the same project registry and Orama projection.

Open **People** in the left navigation or an asset's People tab. Select someone to name them, inspect image samples, play bounded audio samples and open appearances. Visible and heard are separate facts; the “both” filter means both occur somewhere in that file, not necessarily in the same shot.

Review suggested connections using **Compare with**. **Same person — combine** joins the selected evidence under one Person. **Different people — keep separate** records a rejection. **Separate these samples** detaches an evidence group from a combined Person. It does not delete its original observations or embeddings. Co-occurrence and cosine suggestions require review; neither proves identity. Audio over a still image or voiceover is especially ambiguous.

Names and person notes save atomically with assignments and audit events. Conflicting historical labels require a reviewed name; all original labels remain in Name history. Undo last change restores the prior unified state only if later changes have not made that undo unsafe. Concurrent edits are rejected with a refresh message while the browser keeps the draft.

## Existing projects

The first People/navigation request creates an integrity-checked SQLite backup and migration receipt in `_DIT_NOTES/backups/`. Migration adds unified tables and a version marker in a transaction. It preserves every legacy face/voice row, embedding, profile, label, observation, annotation and existing explicit link. Same names alone do not merge people. Existing explicit face/voice links become shared People; existing rejection decisions carry forward. Interrupting the transaction rolls it back, so startup can retry.

For an explicit maintenance migration, with the service stopped, use:

```sh
python3 deploy/field.py --help
python3 deploy/field.py people --help
```

The installed command delegates to its active release's `deploy/people.py`. Inspect its receipt and restart through the normal deployment commands. Do not restore an old database over new operator edits. Releases declare `unified_people: 1` in `deploy/data-capabilities.json`; activation refuses to downgrade to a writer without this capability. Manually launching old software bypasses that guard and is unsupported.

Existing AI outputs are reused. Only the shared metadata/search projection needs refreshing; migration does not rerun transcription, visual inference or biometric extraction. Original media and Premiere timelines are not rewritten by this migration.

## Navigation

Drag the narrow divider at the sidebar's right edge to resize it. Width is remembered in this browser, bounded to 160–480 pixels. The divider also supports Left/Right arrows and Home/End when keyboard-focused. Collapse/expand remains independent of saved width.

## Cross-validating a collection of interviews

`ditnotes.av_validation` supports an explicitly reviewed interview collection without rerunning ASR or deleting biometric evidence. `common_voice` discovers a recurring voice by cross-recording support, then validates each selection against a centroid built from other recordings. It requires at least three independent recordings, compatible embedding revisions/dimensions, a cosine of at least 0.72 and a within-recording competing-voice margin of at least 0.25. These are conservative defaults for this validation pass, not calibrated identity probabilities or universal guarantees. Local diarizer speaker numbers are not identities. Multiple samples from one recording cannot supply independent support.

A recurring voice must still be identified from independent evidence, such as reviewed interviewer questions and operator context. The other participant can be associated with a named face only when an explicit operator prior establishes that this interview has exactly that one person on camera. Do not use that rule for group shots, voiceovers, static portraits or unknown interview formats. Image motion can corroborate a proposal; it does not certify lip sync. A static interviewer portrait must never acquire the interviewees' identities merely because their speech overlaps its display.

A private JSON plan contains `project_id`, `base_revision`, exact sealed analysis `sources` (relative path and SHA-256), reviewed `links` (`face_id`, `voice_id`, `name`, `basis`) and optional `voice_labels` (`voice_id`, `name`, `basis`). Include the operator priors, validation scores and any limitations in the plan. Never ship a real project's plan or names in the repository. The apply command runs entirely offline:

```sh
python -m ditnotes.av_validation apply --project /path/to/project --plan /path/to/reviewed-plan.json
```

Apply at a quiet point. It creates an integrity-checked SQLite backup, checks source hashes and the metadata revision, then writes all People assignments, names, provenance and the applied receipt in one transaction. Existing name conflicts, rejected associations and simultaneously distinct voices block unsafe combinations. A pre-commit interruption rolls back; rerunning after a committed but unacknowledged application repairs its receipt without duplicating changes. Search projection refresh carries the names into the shared Orama indexes; originals and raw face/voice evidence stay intact. This procedure does not certify every diarized turn: overlapping speech or diarization errors may still need review.
