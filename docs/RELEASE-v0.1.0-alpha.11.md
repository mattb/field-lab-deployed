# v0.1.0-alpha.11 — multiple ingest primaries, preserving today's workflow

The usual request still suffices:

```text
Update this laptop’s Field Lab installation from https://github.com/mattb/field-lab-deployed
```

This release installs multi-primary capabilities **without changing the current ingest destination or existing CCC tasks**. Its manifest replaces alpha.10's configuration migration with `multi-primary-preserve-v1`. Follow [preservation-only adoption](MULTI-PRIMARY-ADOPTION.md): capture the actual configuration/binding/registry and read-only CCC inventory, deploy and refresh Desktop apps, then verify those values are unchanged. Missing or pending historical backup adoption does not authorize configuring CCC now. Changelog history is informational; execute only this release's current migration.

An installation without a shoot registry retains its established single-primary workflow. Do not run `primary init`, choose a new disk, rebind Backup or create/edit tasks merely to adopt this update. Existing registry ownership, paired tasks, ledgers, caches and any pending cutover are retained. An incomplete cutover still needs explicit recovery and blocks ordinary startup; the update must not finish or erase it automatically. A disconnected configured primary may defer pre-update metadata checks; retain its destination and reconnect it rather than creating a substitute mount.

## New capabilities

For a later explicit request to move future ingests to another primary, the [cutover skill/procedure](MULTI-PRIMARY.md) journals preparation, supported OffShoot setup, target validation and operator acceptance before committing active ownership. Each primary keeps independent CCC sets and date-assignment ledgers. Reconnecting or backing up an old primary never selects it as the ingest destination or changes the active Desktop Backup binding.

The library combines namespaced metadata and vectors through the existing Orama search architecture. Atomic laptop caches retain indices, transcripts, notes, tags, People evidence and previews for disconnected volumes. Original media playback and edits require the owning drive. Chat/Premiere operations remain primary-scoped; People are not silently merged across volumes. Source volumes retain originals; CCC alone performs backup copies.

Interrupted cache publication retains the last complete generation. Queue jobs retain their primary ownership across cutover and wait for offline sources. Interrupted CCC invocations retain unknown outcomes and require supported task-history reconciliation before retry, including after server restart. Neither a successful subprocess nor an acceptance JSON proves a content/restore check.

## Compatibility and adoption

No new model weights, dependency lock or ingest analysis recipe is introduced relative to alpha.10. Reuse compatible AI outputs/vectors; no blanket reindex is needed. Laptop caches consume additional local space. New code declares `multi_primary: 1`; unsupported older writers are blocked by the capability guard. Code rollback does not undo registry/metadata edits or external app configuration from a separately requested cutover.

From alpha.8/9/10, preserve the actual current setup and private records. From unknown/older baselines, read all intervening changelog entries and inspect readiness/data compatibility; do not infer that historical tasks or retired automation are safe to run. Do not remove copies, reset metadata or execute old backup-configuration procedures as an upgrade workaround. A fresh install still follows first-install destination selection and acceptance; it does not import a developer's disks or tasks.

## Validation boundary

Development validation includes disposable-volume ownership/queue/HTTP tests, interrupted operation and source-sealed candidate checks, viewer tests/build, and independent document-led roleplay with intercepted app commands. See [validation details](TESTING-MULTI-PRIMARY.md). Final exact-commit CI results are recorded with the GitHub release. These tests do not certify Ariel's hardware, her OffShoot selected destination, real CCC filters/copies, or a restore. The preservation migration verifies retained state; future Annex enrollment and physical cutover acceptance remain separate operations.

Local release-candidate checks: 598 Python tests completed successfully (five skips), 55 viewer tests, 31 Premiere bridge tests, viewer production build, skill validation and 76 focused checks from a source-sealed candidate passed. Independent update tests exercised alpha.8/9/10 and unknown baselines with unchanged config/Backup/registry bytes and intercepted app commands. No model weights or production media were copied into the release, and no maintainer OffShoot/CCC configuration was changed.
