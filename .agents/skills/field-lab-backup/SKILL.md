---
name: field-lab-backup
description: Plan and configure Carbon Copy Cloner date-filter backup rotation using supported CCC UI. Use for allocating dated ingest folders across smaller disks, creating the next backup set or reviewing backup coverage. CCC alone copies original media.
---

# Date-filter backup rotation

Read the checkout's `AGENTS.md` and [rotation procedure](../../../docs/BACKUP-ROTATION.md). Preserve unrelated tasks, originals, operator metadata and existing backup records. CCC alone reads original media and makes backup copies. Do not introduce another copy engine or reorganize the primary to match disks. Never reverse engineer CCC or edit its private preferences/databases/task files.

For first setup or adoption from an older install, read [adoption and migration](../../../docs/BACKUP-ADOPTION.md) before planning a rotation. Activate the new release first; its backup-shortcut commands dispatch through the active installed code. Find the verified source checkout as described by the main Field Lab skill if this skill was globally installed; documentation links are relative to the repository copy.

## Model

All tasks read the same original project root. Keep one permanent task per physical backup disk. Closed tasks use **Include** mode for their explicit assigned dated folders; the current task uses **Exclude** mode for the union of EVERY closed task's dated folders. New dated folders enter current automatically. Use actual folder names, not file modification dates or a guessed future switch date.

This requires supported CCC UI configuration at each rotation. Bombich's published [CLI](https://support.bombich.com/hc/en-us/articles/20686443072535-Using-the-ccc-Command-Line-Tool-to-Start-Stop-and-Monitor-CCC-Backup-Tasks) starts/monitors existing tasks but does not document filter/task configuration. [Filter import](https://support.bombich.com/hc/en-us/articles/20686509510167-Configure-the-task-filter-to-exclude-files-and-folders-from-a-task) is a UI operation, without a published generation schema. Do not claim a headless/offline app can apply filters. If Codex has supported computer-use access it may operate the authorized CCC tasks; otherwise report the exact remaining UI step. Agent execution may need internet; CCC's configured daily tasks do not.

## Plan a handover

Inspect actual source/destination UUIDs, independent physical backing, CCC idle status, outgoing task/filter configuration and free space. Keep task configuration exports and the assignment ledger locally outside Git. Use only explicitly authorized dedicated tasks; do not change unrelated schedules, filters or endpoints. Finish active transfers normally.

A reviewed ledger has this form (example names only):

```json
{"schema":1,"project":"/ABSOLUTE/PROJECT","closed_sets":[{"label":"B001","dates":["20261115_D01"]}],"current_label":"B002"}
```

For the first rotation, `closed_sets` is empty and `current_label` is B001. The ledger records configuration assignments, not checksum verification. Do not seed a real ledger from these examples.

Run the script using a local Python, supplying the actual project, reviewed ledger and an unused next label:

```sh
APPLICATION_HOME/current/.venv/bin/python APPLICATION_HOME/current/.agents/skills/field-lab-backup/scripts/plan_rotation.py --project PROJECT --ledger PRIVATE_LEDGER.json --next-label B003 --output NEW_PRIVATE_PROPOSAL.json
```

For temporary-folder tests on macOS, use the canonical existing path (for example `Path(...).resolve()`); the planner deliberately refuses symlink source paths.

It reads the root folders and emits rule expressions and a proposed next ledger. It never copies files, invokes CCC, applies filters or marks a backup verified. Keep the output outside the source project; it refuses replacement. Known dated formats are YYYYMMDD and YYYYMMDD_DNN. Review other root items explicitly; do not silently drop project media in other layouts. If naming differs or a day must split across disks, prepare an explicit reviewed card-folder plan instead of guessing or editing the script's output into a false receipt.

Review the outgoing date list with the operator: only complete, stable ingests can close. Late additions within an older included day belong to its old disk and require reconnecting it. A newly created backdated folder with no prior assignment still enters current; don't silently move it by calendar comparison. Existing moved/renamed assigned folders need reconciliation. Date-only allocation cannot automatically split a large day; stop and propose an explicit card-folder boundary.

## Apply in CCC

After outgoing footage is independently verified, use the supported CCC UI to configure its Include list and the new disk's Exclude list from the proposal. Include complete folder trees. Conventional folder checkboxes can avoid ambiguity in expressions. Always read back the actual result and Preview source membership; the proposal is not authoritative about CCC parser behavior.

Confirm original source root, correct disk UUID and dedicated destination, no destination deletions, intended metadata scope and re-verification. Keep tasks manual until accepted. No format/erase, privilege reset or unrelated task edits. Save exported configurations and a handover receipt before promoting the proposed ledger. An interruption leaves handover pending; inspect BOTH tasks before retrying. Keep the previous ledger until configuration and Preview pass. Repeated requests must not create duplicate tasks or reassign dates.

Live SQLite metadata needs a consistent backup policy: stop AI cleanly and prevent operator metadata edits during a direct file-level CCC copy. Don't claim that a moving live database was safely copied. Agree whether each older disk also receives refreshed project metadata when reconnected; the date-folder proposal intentionally leaves non-date items for review.

## Verify and report

Task completion is separate from independent data verification. Use CCC post-copy re-verification and documented health checks/restore checks. Preserve failure diagnostics and existing copies; do not clear indexes or rewrite ledger checksums to force success. Do not infer verification from green UI alone or release source cards automatically.

Report the current disk, closed assignments, actual CCC changes, filter Preview coverage, copy/verification evidence, missing disks and unresolved steps. A local-folder roleplay proves planning only; recipient-machine CCC configuration and a disposable real copy/restore are separate acceptance gates.

## Stable Desktop Backup action

The operator wants double-click **Backup** to START the current CCC task, not merely open the app. There is no task default in source control. After first setup, install the native shortcut using the active release:

```sh
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME install
```

After actual CCC configuration, Preview, endpoint/disk identity and acceptance checks succeed, resolve that task's UUID from CCC's published `--uuids` inventory and bind it:

```sh
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME bind --task-uuid ACTUAL_CURRENT_TASK_UUID --mode start
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME status
```

Update this binding as the LAST handover step, only when the new current task is fully configured; leave it unchanged on failed/pending rotation. Record the previous UUID with the private handover receipt. Do not bind a proposal label or an unrelated existing task as a default. The pointer lives in application shared runtime storage; task UUIDs/names never belong in Git. Preserve it during updates. Desktop **Backup.app** stays the same across rotations; it reads the pointer at launch, checks that task exists and CCC is idle, then uses documented `--start=UUID --watch`. It never changes filters/endpoints or determines the rotation boundary. An unbound or missing task produces a native error and no start. CCC completion still requires review of its result/verification. This action does not start or stop AI/OffShoot; finish ingests and stop metadata writers before a direct file-level CCC metadata backup.
