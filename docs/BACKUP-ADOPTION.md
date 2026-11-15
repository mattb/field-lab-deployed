# Adopt date-filter CCC backups on an existing Field Lab laptop

This procedure lives in the release so Codex needs no maintainer chat history. [Update and adopt prompt](prompts/UPDATE-AND-ADOPT-BACKUPS.md) requests both software deployment and CCC setup. A plain software update installs the new code/skills/Desktop action and preserves configuration; it does not silently convert CCC tasks.

## 1. Discover the installation

Inspect the real `current` release, application home, shared configuration and project. Use the existing home's path on every command. Record old commit/release, CCC version/task UUID inventory, backup disk volume UUIDs/physical independence, task source/destination/filter settings and existing backup evidence privately. Export managed CCC task configurations through CCC UI before changing them. Names and examples in this repo are never task/disk defaults.

Managed older releases use the documented candidate/activation process. If the Mac has an unmanaged prototype, a missing/damaged current pointer, unknown schema or inconsistent configuration, stop adoption and describe the recovery needed; do not guess a home or turn an unrelated folder into a managed installation. A generic “whatever version” cannot guarantee compatibility with unknown or future formats.

If earlier backup automation is installed, establish its status and wait for active copies to finish. Preserve its state, task exports and backup files. Do not invoke withdrawn commands or assume its old assignment records prove the current date-folder coverage. Do not delete old data or stop unrelated CCC tasks. Inspect each candidate task’s Preflight/Postflight controls in CCC: older endpoint-specific preflight scripts may reject the new original-source path. Preserve their exported configuration and script files; do not delete or bypass a guard. Prefer a new dedicated task if an older one cannot be confidently reconciled through supported UI. Inventory old task schedules and desktop/launch automation as well as the running process; retained old releases may still contain withdrawn commands. Explicitly identify any retired managed schedule/process that conflicts with the new tasks; explain and disable only the identified obsolete automation as part of the requested adoption, after preserving its configuration. If its ownership/outcome cannot be established, leave adoption pending.

## 2. Update first

Read the NEW release's FIELD-UPDATE prompt and compatibility notes. Fetch that exact tag into a reviewed source checkout, preserving local modifications, prepare a candidate, check offline readiness and activate only when OffShoot/AI work is idle. Never edit an active release. Preserve project/shared metadata and previous code. Capability guards must pass. No new inference/model profile is required by this backup change.

Then run from that checkout with the actual application home:

```sh
bash deploy/bootstrap.sh desktop --home APPLICATION_HOME
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME status
```

The first command installs/refreshes the recipient's five Desktop apps. It does not configure CCC, bind a task or start a copy. Existing binding is preserved. The second reports whether a binding exists. These commands use the active release: do not run the new backup-shortcut command before activating a release that contains it. A stale existing binding is evidence to inspect, not permission to run it.

## 3. Establish the first current set or reconcile prior sets

Identify the real original source project and dedicated destination folder on an independent physical backup disk. Leave disk formatting and other data alone. If no prior rotation exists, start with B001 and no closed assignments. Its CCC Exclude filter initially has no earlier date folders to exclude, so future dated ingests are included automatically. Existing required source-wide exclusions and metadata scope must still be reviewed.

If previous disks already contain footage, record each explicit date assignment only after comparing source/destination scope and reviewing verification evidence. Missing disks or uncertain coverage remain unverified. Never allocate “everything before today” just because a disk is called Backup 1. An older task can be reused only when it is an explicitly reviewed dedicated task with the correct original source, destination identity, filter mode and settings. Otherwise create a NEW dedicated task and preserve the old task/copy until its replacement has been accepted. The planner does not migrate task configuration or old ledger schemas.

Use a private directory such as `APPLICATION_HOME/shared/runtime/backup-date-rotation/` (create a new empty directory if none exists). Keep all records here across updates, outside source media and Git. A reviewed `assignments.json` starts as:

```json
{"schema":1,"project":"ACTUAL_ABSOLUTE_PROJECT","closed_sets":[],"current_label":"B001","tasks":{},"verification_records":[],"handover_history":[]}
```

Replace the project and label from actual discovery. For each set, `tasks` maps its label to `task_uuid`, `source`, `destination`, `volume_uuid`, `physical_identity`, `configuration_export`, `configuration_checked_at` and `metadata_scope`. `closed_sets` contains each previous set's `label` and exact `dates` names. `verification_records` identifies reviewed date/card scope, task history/verification evidence, observed result and time; it is separate from configuration. `handover_history` retains old/new labels and task UUIDs, proposal, exports, Preview and acceptance results. Do not invent evidence for missing disks or tests not run.

Write records with a private temporary file, flush/fsync, atomic replacement and directory fsync; keep previous revisions. Before any task change, persist `pending_rotation` with the intended handover and paths to preserved prior records. On restart reconcile the actual CCC tasks with that intent; never plan another rotation while one is pending. Promote the proposed assignment fields only after the task changes/Preview succeed, retaining task/verification/history fields. Clear pending only after final pointer binding/readback and record publication succeed. A crash between those steps requires readback reconciliation, not a reset.

## 4. Configure and accept CCC

Follow [rotation operations](BACKUP-ROTATION.md) and the backup skill. Use supported CCC UI, including conventional folder checkboxes or documented filter rules. Keep tasks manually scheduled initially. Confirm the original project root, destination UUID/capacity, no destination deletions and postflight re-verification. Stop metadata writers/prevent edits during a direct file-level CCC backup of live project databases. Define metadata inclusion for all disks explicitly.

Read back settings, Preview actual membership, and use disposable own-media files for copy/verification/restore acceptance. A generated proposal, imported configuration or task exit is not verification. If app-control tools or permissions are unavailable, report the exact UI step remaining; do not bypass that limitation. This setup cannot be certified by merely installing code.

## 5. Bind Backup last

After the current task is fully configured and accepted:

```sh
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME bind --task-uuid ACTUAL_CURRENT_TASK_UUID --mode start
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME status
```

Confirm its UUID matches `tasks[current_label]`. The atomic private binding is stored at `shared/runtime/backup-shortcut/current.plist`; it is separate from the assignments ledger. Preserve the previous UUID in the handover record. The native Desktop Backup app reads this pointer and starts only that existing task via CCC's published CLI. It makes no filter changes or rotation decision and does not verify source readiness. Never test a production copy by double-clicking without the operator's intended backup request.

At later rotations use the installed Python, not a development venv or arbitrary system interpreter:

```sh
APPLICATION_HOME/current/.venv/bin/python APPLICATION_HOME/current/.agents/skills/field-lab-backup/scripts/plan_rotation.py --project ACTUAL_PROJECT --ledger PRIVATE_ASSIGNMENTS --next-label NEXT_UNUSED_LABEL --output NEW_PRIVATE_PROPOSAL
```

Quote all actual paths containing spaces. Apply/check filters using CCC UI, retain records atomically, bind current last and reconcile any pending handover. Daily Backup clicks then run offline without Codex; configuration at rotation still requires supported CCC UI interaction. Codex may require internet and computer-use permissions.

## Acceptance report

Report independently: old/new software version; offline readiness; Desktop apps installed; task/disk identities and filter configuration checked; assignment coverage; copy/restore verification; current binding; pending/missing-disk work. Model downloads/readiness, source seals, local-folder roleplays and simulated upgrade tests do not prove real CCC/physical acceptance on this laptop.
