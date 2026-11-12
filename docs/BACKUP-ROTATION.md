# Rotate smaller CCC backup drives

Opt-in workflow for a large APFS ingest volume and several smaller backup drives. The manager allocates whole verified card offloads, preserves their relative paths, requests the next registered drive before a card will not fit, and verifies the destination bytes. It never creates, edits, disables, stops or deletes a CCC task. Installation/update alone does not enable rotation.

## Daily operation

Open the local backup UI and press **Back up pending footage**. Leave it running while it works. When asked, connect the indicated labeled backup drive and press the same button again. A full closed drive may be disconnected after copying has finished; never disconnect during a copy. New ingests can wait on the primary drive while their backup drive is absent. The interface reports pending/assigned/copying/verified per card, and metadata status per drive. Do not treat AI completion or a drive label as backup verification.

A normal run discovers OffShoot's accepted verified-ingest queue, allocates each card, makes a sealed APFS copy-on-write projection, starts only its registered CCC task, waits for CCC to be idle, and independently SHA-256-checks the expected destination files. A 10% capacity reserve is the initial policy; whole cards and the current metadata snapshot must fit before allocation. A single oversized card requires a larger drive; it is not split. Space is checked again immediately before copying. A partly copied card remains assigned to the same drive and resumes there.

## Layout and fixed tasks

The primary date/card hierarchy stays unchanged:

```text
PRIMARY/
  Ingests/
    20261111/A001/           # original verified offload + all companions
    20261111/B001/
    _DIT_NOTES/
      backup-rotation/state.json
  FieldLabBackupSources/    # separate, same APFS volume; outside Ingests
    B001/payload/
      20261111/A001/         # sealed COW clones, NOT symlinks/hardlinks
      FieldLabMetadata/REVISION/_DIT_NOTES/
    B002/payload/
      20261111/B001/
```

Each new dedicated CCC task has a permanent source `FieldLabBackupSources/B00N/payload` and destination `BACKUP_DRIVE/Shoot`. Drive B001 therefore contains `Shoot/20261111/A001/...`; B002 contains `Shoot/20261111/B001/...`. One date can span drives, one card cannot. Here B001 in a card path is an example camera/card label; the outer B001 source-set label identifies a physical backup drive.

APFS clones initially share storage blocks with originals but are independent files: editing an original does not edit its clone. They are not an independent backup until copied to another physical device. Removing/editing originals later may increase the space used by retained clones. No hardlinks, symlink farms, source moves or reconfiguration of OffShoot are needed. Original in-place edits or path moves detected before an unfinished backup stop that backup rather than silently changing its recorded meaning.

This is a **partitioned archive**, not the old whole-project mirror. Verified card manifests and backup paths remain the recorded ingest hierarchy. Arbitrary later Finder moves are not automatically propagated across offline drives; preserve the ledger for reconstruction. Do not move/edit pending card scopes. If you need a different archival layout, plan and verify a separate migration. Existing CCC mirror tasks are unchanged.

## One-time setup with Codex

First install/activate the release using the normal deployment skill. Supply the actual project and a new empty spool folder outside it on the same APFS volume. Commands below use the active installed interpreter through the supported deployment wrapper. Replace all uppercase paths/UUIDs; these are not production defaults.

```sh
bash deploy/bootstrap.sh backup init --project "PRIMARY/Ingests" --spool "PRIMARY/FieldLabBackupSources"
```

The default state folder is `PROJECT/_DIT_NOTES/backup-rotation`. A custom state folder must be outside the project; other nested locations are rejected so rotation state cannot enter a metadata snapshot. A repeat matching init preserves the same state; it does not reset assignments. Keep the primary/spool mounted and never create a fake directory at a missing `/Volumes` mount. Physical backing is resolved through diskutil; APFS sibling volumes on the same hardware do not count as independent disks. SoftRAID support follows an exact driver UUID/provider mapping; unknown topology fails closed.

Before creating each task, prepare its empty fixed source:

```sh
bash deploy/bootstrap.sh backup prepare-set --state "PROJECT/_DIT_NOTES/backup-rotation" --label B001
```

For each backup drive, have Codex/operator create **a NEW dedicated CCC task in the supported UI**, leaving all existing tasks alone. Its destination must be an empty dedicated subfolder on that backup drive, not the volume root. Its source is the matching spool payload above. Use the source path returned by `prepare-set`; registration accepts only this manager-owned empty folder. Neither command creates the destination or the CCC task. Use an explicit label such as B001, B002, B003 and distinct physical drives. Select:

- Manual scheduling; no task-group membership or automatic eject.
- Copy the entire payload without exclusions. Select **Don't delete anything** for this archival task; inspect Preview. Do not change an existing task's deletion/retention policy or clear snapshots to make room.
- **Re-verify files that were copied** under Postflight. The manager adds its own destination hashing; neither just a green task icon nor CLI exit alone is accepted.
- Disable source snapshotting if it changes the path passed to preflight; the guard intentionally refuses unexpected snapshot aliases.
- A secured, task-specific preflight guard, installed below. Do not attach a global preflight to existing tasks.

Use CCC's supported `--uuids` inventory to obtain that new task's UUID. Register it and the intended guard path while its backup drive is connected:

```sh
bash deploy/bootstrap.sh backup add-drive --state "PROJECT/_DIT_NOTES/backup-rotation" --label B001 --mount "BACKUP_MOUNT" --destination-relative Shoot --task-uuid NEW_TASK_UUID --dedicated-task-reviewed --guard-path "/Library/Application Support/Field Lab CCC Guards/B001.sh"
bash deploy/bootstrap.sh backup guard --state "PROJECT/_DIT_NOTES/backup-rotation" --label B001 --output "NEW_GUARD_FILE"
```

Guard generation creates only a new file; no task, privilege or app setting is changed. The generated standalone Bash script uses fixed endpoints and Apple system binaries to check actual CCC `$1`/`$2` and both volume UUIDs. A nonzero preflight exit aborts CCC before copying. It does not import Python, the venv or the release's `current` pointer as root.

Review the generated script, then explicitly install it as a new **root-owned mode-755 file** under the root-owned directory above, with no group/world-writable parents. For example, an operator/admin can create the new directory with `sudo install -d -o root -g wheel -m 755 ...` and install the new reviewed file with `sudo install -o root -g wheel -m 755 ...`. Inspect first: do not overwrite an existing script or alter unrelated directory permissions. Select this script in the NEW task's Preflight controls. The manager checks exact script bytes, ownership, mode and parent permissions before any production CCC launch.

The CLI cannot read back all CCC task settings. Registration's `--dedicated-task-reviewed` records the operator's explicit review, not automatic attestation. Confirm the correct preflight is selected and enabled in that task; removing it or changing the task later invalidates acceptance. Independently inspect endpoints, filters, deletion, snapshots and verification before reuse after such changes. Never register an existing unrelated task as a shortcut. Task setup and root-script installation remain recipient-machine acceptance steps, not something a software update silently performs.

## Open the UI

```sh
bash deploy/bootstrap.sh backup ui --state "PROJECT/_DIT_NOTES/backup-rotation" --queue-db "APP_HOME/shared/runtime/queue.sqlite3" --open
```

The default port is 8778; `--port 0` selects an available port. It binds only to `127.0.0.1`, prints its URL, and opening it does not start a backup. The run button discovers verified ingests from the explicitly supplied queue; it never guesses card folders from filenames or browses an arbitrary USB device. For a terminal run replace `ui` with `run` and omit `--open`. `status` is read-only. Without `--queue-db`, only previously registered card jobs are considered; it does not discover new footage.

The server runs in the foreground; stop the UI with Ctrl-C between runs. During a copy CCC may continue after its manager exits. Restart the same command and press the button to reconcile. Keep the UI open for progress; no background launch agent, global CCC automation or Desktop icon is installed by this release. Stop the rotation UI before a software update/rollback. The UI has no arbitrary path/command/task editing API.

## Atomicity, metadata and recovery

The checksummed state file is serialized by a cross-process lock, written through an existing directory file descriptor, fsynced and atomically replaced. It cannot recreate a missing state mount. Allocation commits before cloning/copying. Staging directories publish only after manifest verification and fsync. Before a CCC launch the manager commits `copying`; a crash leaves an unknown attempt rather than a false success.

On the next run, the manager first checks whether CCC is idle. It never stops an existing task. For an unknown attempt it independently verifies the sealed destination bytes: a completed copy becomes verified without rerunning CCC; missing/damaged bytes cause a retry from the sealed source. Wrong UUIDs, insufficient capacity, source changes, damaged state, unexpected symlinks and lock conflicts stop or leave work pending. Do not delete locks or edit state JSON to force success. Preserve diagnostics and restore a known ledger only through a reviewed recovery plan.

A recovery ledger is atomically refreshed in `.FieldLab-PROJECTID-B00N/ledger.json` at each connected backup volume's root, outside CCC's managed destination. Publication is retried even if media was already verified. Offline disks retain the ledger as of their last connection; the primary state is authoritative for later work. Ledger revisions increase monotonically, so recovery can identify the newest surviving state for the same project ID without trusting filesystem dates. Keep an independent backup of the manager state/app shared configuration too. Disk-side ledgers contain private paths and checksums; never add them to Git.

Each run snapshots project `_DIT_NOTES` under the analysis/publication lock. SQLite uses its backup API, including committed WAL edits; database snapshots are integrity checked. Other sidecars are COW-cloned. Rotation state, locks and temporary files are excluded. Metadata generations are content-addressed: unchanged snapshots do not cause another CCC copy; new operator notes/names/chat produce a new generation. Connected sets with assigned footage receive the current snapshot even after all media jobs are verified. An offline set keeps its older revision until reconnected; the UI displays the backed-up revision/pending state.

Operator edits may commit concurrently with that snapshot: the backed-up database is transactionally consistent at its backup point, not a promise to include edits saved afterward. Search projections can lag the database and should be reconciled on restore. Retained metadata generations consume secondary capacity and are included in space checks; this release never silently prunes them. Low-space metadata updates remain pending while previously verified footage stays recorded. To restore, merge each drive's recorded date/card trees into a recovery project, select the newest verified metadata generation, copy its `_DIT_NOTES` into that project and reconcile paths/search using the normal recovery tools. Never overwrite newer operator edits with an older snapshot automatically.

Verification makes additional full reads of new source/projection/destination files. This can dominate backup time for terabytes of footage; AI's processing-time target is separate. The implementation favors independent evidence over speed. Existing verified cards are not rehashed on every click; periodic CCC health checks/restore tests remain useful.

## Acceptance and disaster coverage

Before field reliance, use **new disposable CCC tasks, folders and recordings**, not existing production tasks. Verify:

1. Actual mounted primary/spool APFS identity, independent physical backup drive, exact new task UUID/endpoints/settings and secured preflight; wrong endpoint/UUID must abort before copying.
2. Two differently sized secondary drives, whole cards/companions, one date spanning both, low-space swap prompt, and the ability to continue after a closed older drive is disconnected.
3. Full real CCC copy plus manager hashes, media playback, ledger, and restored SQLite note/name/chat. Test another metadata edit after footage is verified.
4. Interrupt the manager while CCC is running, restart while it is still busy, and reconcile after it ends. Also test unplug/reconnect on disposable data. A backup remains pending after every failure; never erase the card as part of the test.
5. A software update preserves all rotation state/assignments and a rollback does not downgrade state. Schema 1 is a separate JSON format; project SQLite schema/AI profiles are unchanged. Older releases do not understand rotation and must not operate its spool.

Automated tests use injected fake CCC copies, temporary drives and real process SIGKILL at several durable boundaries. Native tests exercise APFS COW and standalone guard checks. These are not physical power-loss or real CCC/drive acceptance. No existing CCC task was changed or started during development. The current ThunderBlade SoftRAID provider topology was read-only inspected, but its APFS volume was unavailable during validation; actual mounted-volume copying remains deferred.

## Vendor references

- [CCC does not span destinations](https://support.bombich.com/hc/en-us/articles/20686465518871-Glossary-of-Terms)
- [Start, watch and inspect existing CCC tasks](https://support.bombich.com/hc/en-us/articles/20686443072535-Using-the-ccc-Command-Line-Tool-to-Start-Stop-and-Monitor-CCC-Backup-Tasks)
- [Preflight parameters, abort behavior and root permissions](https://support.bombich.com/hc/en-us/articles/21608812482455-Running-shell-scripts-before-and-after-the-backup-task)
- [CCC verification](https://support.bombich.com/hc/en-us/articles/20686511433623-How-to-verify-a-backup)
