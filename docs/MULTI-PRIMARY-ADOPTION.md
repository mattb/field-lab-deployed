# Adopt multi-primary capabilities without changing today's workflow

This is the required `multi-primary-preserve-v1` migration for v0.1.0-alpha.11. The usual repository-URL update request includes this procedure. **It is a software adoption, not a primary cutover or a request to configure backups.** It supersedes historical update instructions that authorized CCC date-filter changes. Read prior changelog entries to understand compatibility, but do not execute an older manifest's task/filter migration during this update.

## Preservation contract

Preserve the current ingest volume UUID, mount, relative ingest folder, root, all unknown machine settings, every existing CCC task/filter/schedule/deletion policy, date-assignment records and current Backup task binding. Do not run `configure`, `configure-offshoot`, `primary init`, `primary register`, cutover, backup-register/current/bind, or any CCC task/filter setup merely to complete this update. Do not start a backup as a smoke test. Missing backup setup remains as it was; report it separately without making it an update blocker or selecting a default.

If `shared/shoot.json` already exists, retain it byte-for-byte, including active primary, per-primary sets, history and any pending intent. If it does not exist, **leave it absent**: the updated software supports the existing single-primary installation and existing Desktop Backup binding. Multi-primary ownership will be enrolled when the operator explicitly requests a future cutover; it is not necessary to cut over, choose another drive or enroll CCC endpoints to install these capabilities.

## Capture, update, compare

Use the actual managed application home and the selected immutable release checkout. The helper is stdlib-only and may run with the prepared candidate's Python before activation. No machine defaults are supplied here.

```sh
python3 deploy/update_preservation.py --home ACTUAL_APPLICATION_HOME capture
```

Keep the returned receipt ID. The helper snapshots exact protected-state hashes and the read-only published CCC `--uuids` inventory in a private atomic receipt under `shared/runtime/update-preservation/`. It never modifies protected state or invokes a backup/configuration command. If CCC is unavailable or protected state cannot be read, complete candidate preparation but keep adoption pending; do not fabricate a successful comparison. Task inventory does not expose or independently certify CCC filter/endpoints; the installer never changes them. Supported UI may be inspected read-only if further evidence is needed.

Prepare/check/activate the selected release with the standard software update procedure. Observe OffShoot idle and stop AI cleanly only for activation. If the configured primary is unavailable, prepare the candidate and reconnect that original drive before activation; do not rewrite its destination or create a replacement mount. Refresh owned Desktop apps; this preserves the existing Backup pointer and does not start CCC.

After activation/Desktop refresh and before ordinary service startup:

```sh
python3 deploy/update_preservation.py --home ACTUAL_APPLICATION_HOME check --id CAPTURED_ID
```

A mismatch leaves the captured evidence and reports drift. Stop and reconcile the actual difference; do not overwrite changed settings using the snapshot. Only a successful comparison can be reported as preservation checked. Check the configured root/UUID in the output and the current task in the protected pointer and private receipt. Hash equality covers Field Lab's configuration, registry and pointer; task UUID/name equality covers inventory. Neither proves independent verification of CCC's private task settings or a media copy.

Restart the existing library/service normally only if no cutover intent is pending. Verify an existing clip, saved operator metadata and current destination display; do not clear/reindex footage solely to test the UI update. If a pending cutover already exists, preserve it and report that its normal startup guard still applies; continue it only under its separately authorized recovery procedure. Inspect candidate/source provenance and model readiness. No new models are introduced by this release; cached analysis and compatible search vectors are reused. Local metadata/index/preview caches are allowed; originals remain on their owning volumes.

Record migration `multi-primary-preserve-v1`, selected tag/commit, capture receipt ID, comparison result, software readiness and exact deferred checks in the private `release-migrations.json` using atomic writes while preserving other entries/history. Complete means the software is adopted with the prior workflow preserved, **not** that a new primary or backup set has been configured. Preserve any earlier pending backup-migration record rather than silently marking its unperformed acceptance complete.

## Future explicit changes

When the operator actually asks to switch the ingest primary, use the [cutover skill](../.agents/skills/field-lab-cutover/SKILL.md) and [multi-primary procedure](MULTI-PRIMARY.md). Only that request authorizes enrolling ownership, configuring the newly selected primary's dedicated CCC pairs and changing the active ingest/Backup pointer. Existing old pairs remain retained. Ordinary reconnecting or browsing never changes active ingest.
