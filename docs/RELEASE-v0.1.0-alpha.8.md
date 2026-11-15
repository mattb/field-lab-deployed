# v0.1.0-alpha.8 — Premiere, People and static review

This prerelease brings together the work developed since alpha.6. It contains reusable software and installation instructions, without footage, models, project databases, operator labels or cloud credentials.

- Premiere bridge: sealed Adobe plugin packaging/install/update, source transcripts, exact tag Search Bins, clip metadata and markers, previews and selects. Editor changes produce conflicts rather than silent replacement. See [installation](PREMIERE-INSTALL.md) and [validation boundaries](PREMIERE-PRODUCTION-VALIDATION.md).
- Separate asset tags with corpus autocomplete, resizable folder navigation and one global semantic search using local embeddings, reranking and CLIP.
- Unified People registry with face and voice evidence, reversible review and visible/heard distinctions. Cross-recording voice analysis remains evidence, not proof of lip synchronization. See [People migration](PEOPLE.md).
- Backup-rotation support from this prerelease has been withdrawn in the current source. The replacement [date-filter plan](BACKUP-ROTATION.md) keeps CCC reading originals directly and requires supported UI configuration at handover. Existing backup data and CCC tasks must be preserved.
- Read-only static project export with browser-local inference and precomputed compatible search vectors; optional private S3/CloudFront hosting with expiring access links. See [static sharing](STATIC-SHARING.md).

## Update and compatibility

Use the [field update prompt](prompts/FIELD-UPDATE.md) with this exact tag. Prepare a separate candidate; activate only when OffShoot and AI work are idle. This release does not authorize changing OffShoot destinations, configuring CCC, installing Premiere packages or publishing project media during an ordinary update.

People migration is additive and creates a SQLite backup/receipt before migration. Preserve all project state and review feedback. The release data capability `unified_people: 1` prevents activation of older code that cannot safely write that state. Do not restore an older database over subsequent edits to force rollback. Tags and Premiere journals are durable project metadata. UI/projection changes reuse existing analysis; use the reindex plan before deliberately scheduling new inference. Model weights remain external downloads governed by the existing asset lock.

## Validation limits

Local automated suites cover migration, replay, wrong matches, interrupted writes, transcript conversion, native host contracts, install/update packaging and static access signatures. Actual Premiere 26.2.2 integration was exercised on one development Mac; see the detailed validation documents. Private S3/CloudFront delivery was exercised with 28 videos, including authenticated requests, anonymous denial and HTTP Range playback. Full hosted-browser acceptance, fresh-machine installation, unplugged-network certification and physical CCC acceptance are separate checks; automated tests do not establish them.
