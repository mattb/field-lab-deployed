# Configure and accept the second-copy task

CCC is optional until a second physical destination is chosen. The installer leaves existing CCC tasks unchanged. This guide is for Codex operating CCC on the field Mac after the operator identifies drive 1 and drive 2; it is not a claim that a task was configured or tested there.

This document describes a single-destination mirror. When backup drives are smaller than the primary, use [backup rotation](BACKUP-ROTATION.md): separate APFS clone sources, new dedicated tasks per drive, registered endpoints/guards and resumable whole-card allocation. Its partitioned archival behavior differs from the mirror's Finder-move propagation below. Installing either workflow never changes existing tasks automatically.

## Supported automation and its boundary

CCC's documented command-line interface can inspect task UUIDs/status/history and start, stop or watch an existing task. Its documented task-transfer route is Export Task(s) in CCC, then opening the exported `.ccctask` on the other Mac. Neither is a documented general task-creation/configuration CLI. Use the UI for task creation and setting endpoints; do not synthesize `.ccctask` XML, edit private preference keys or decrypt/modify CCC databases. An imported task still needs its actual machine-specific source and destination checked. [CLI manual](https://support.bombich.com/hc/en-us/articles/20686443072535-Using-the-ccc-Command-Line-Tool-to-Start-Stop-and-Monitor-CCC-Backup-Tasks), [task import/export](https://support.bombich.com/hc/en-us/articles/20686444541975-Task-Organization).

`deploy/ccc.py` is deliberately read-only. Run it with the installed Python to inspect this machine; task names in its output are private local diagnostics. It never starts a backup, imports a task or changes settings:

```sh
"$HOME/Library/Application Support/Field Lab/current/.venv/bin/python" deploy/ccc.py
```

The script returns raw CLI output and an explicit `idle_confirmed` flag. An error, timeout or unavailable command is unknown, not evidence that the app is idle. Inventory is not proof that task endpoints or permissions are correct.

Development evidence on 2026-11-05: this read-only interface was exercised against CCC 7.2.2 (8450), CLI v10, and reported task inventory/status without changing tasks. Those are development-machine observations, not a promised version or configuration on the field laptop. Check the installed version/help again before using a different release.

## Codex setup prompt

Paste this when both intended drives are connected, replacing the two paths with actual operator-selected folders:

```text
Configure one new CCC second-copy task for Field Lab using docs/CCC-SETUP.md. Source is DRIVE_1/PROJECT and destination is DRIVE_2/PROJECT. Confirm these are different physical drives using diskutil information, not just different folder names or volumes on one device. Do not choose my camera card as either backup destination or a source to modify. If either selection is ambiguous, stop before changing CCC.

Inspect the installed CCC version and read-only task inventory first. Preserve existing tasks and export their configurations through CCC's Task menu before changing anything. Create a new uniquely named task using the visible UI and supported controls; do not rewrite private settings or invent task XML. Show/read back the final source, destination, deletion behavior, filters, verification and automation settings. Keep automation manual until the acceptance test passes. Do not format, partition or erase either drive; if CCC proposes doing so, stop and explain.

The source project includes originals and _DIT_NOTES. The destination folder is dedicated to this task. We want its current hierarchy to follow drive 1, including intentional Finder moves. Explain any proposed destination-only deletions before the first real copy; use Preview and a disposable test folder first. Do not change SafetyNet or snapshot retention merely to clear space. Do not enable a broad delete policy on an existing mixed-use destination.

Keep OffShoot idle and stop the AI service cleanly during the file-level backup so SQLite metadata is not being written. Never force-kill an OffShoot transfer or fabricate a verification receipt. Run the disposable rename/metadata/restore acceptance test in this document. Use post-copy verification, inspect the final task history, and independently open restored footage and metadata. Leave any untested automation off. Report what you actually validated and what still needs operator interaction or permission.
```

The operator may need to authorize CCC's helper and grant Full Disk Access through CCC's own assistant in macOS System Settings. Codex must wait for that interaction rather than bypass TCC or silently assume the permission exists. Install CCC in `/Applications` so its helper is available at system startup. [Full Disk Access](https://support.bombich.com/hc/en-us/articles/20686443528727-Granting-Full-Disk-Access-to-CCC), [privileged helper](https://support.bombich.com/hc/en-us/articles/20686388957719-What-is-CCC-s-Privileged-Helper-Tool).

## UI task specification

Use a dedicated destination folder or dedicated volume. For example, choose source `/Volumes/WORK/Project/Ingests` and destination `/Volumes/BACKUP/Project/Ingests`. CCC folder-to-folder tasks copy the source folder's contents into the chosen destination folder; do not accidentally select the destination's parent and lose the intended root pairing. Include the entire project `_DIT_NOTES` and card hierarchy in the task filter. [Folder-to-folder backups](https://support.bombich.com/hc/en-us/articles/20686432643735-Folder-to-Folder-Backups).

A current mirror must remove obsolete paths after a Finder move; an archival “Don't delete anything” setting leaves those old paths behind. SafetyNet temporarily retains replaced/removed content; it is not permanent versioned backup. Choose the policy deliberately for a dedicated target and inspect Preview before running it. Do not change an existing mixed-use target's policy just to obtain a mirror. Retained snapshots/SafetyNet may occupy additional space even when the current file hierarchy agrees. [SafetyNet behavior](https://support.bombich.com/hc/en-us/articles/20686475366935-CCC-SafetyNet-Temporary-protection-for-destination-content), [Preview](https://support.bombich.com/hc/en-us/articles/20686447772183-Preview-See-what-changes-CCC-will-make-to-the-destination).

Enable Postflight **Re-verify files that were copied** for this media-copy task. It rereads newly written destination files against the copy's source-data checksum. For existing/copied-before content, use the appropriate Backup Health Check or task verification as well. The Compare window is primarily a size comparison, not checksum verification. [Verification options](https://support.bombich.com/hc/en-us/articles/20686511433623-How-to-verify-a-backup), [Compare limitations](https://support.bombich.com/hc/en-us/articles/20686430989207-Comparing-the-source-and-destination).

Keep the task manually started initially. A generic “volume connected” schedule can overlap an OffShoot transfer or AI metadata save. The currently shipped installer does not wire automatic AI-completion → CCC execution. Starting an existing task from `ccc --start=TASK_UUID --watch` is supported by the installed CCC v10 help, but only use it after its exact task/endpoints and the idle sequence have passed acceptance. A task start/CLI exit alone is not the card-release decision.

## Acceptance before relying on it

1. Record both physical-drive identities, volume UUIDs, chosen folders, task UUID and all settings. Confirm sufficient free storage and that the source/destination are not the same physical device. Keep these records locally, outside Git.
2. Use a new disposable source subfolder with a short own-media clip and an indexed `_DIT_NOTES` containing a test note. Keep production folders outside this first test task. With OffShoot idle and AI stopped, use Preview, run the task with post-copy verification and inspect its final history for errors.
3. Open the copy's media and metadata in a separate test/recovery location. Check SQLite `integrity_check` and reopen the saved note in the library. This validates metadata usability, not just file presence.
4. In the disposable source only, move the clip into a new bucket using Finder. Reconcile its project, confirm the note follows the content, stop AI, then Preview and run CCC again. Confirm the destination's current hierarchy matches the intended move and that stale paths behave exactly as the selected retention policy describes.
5. Stop after any permission error, unsupported setting, space failure, missing drive or verification error. Keep the source card and previous independent copy. Do not delete snapshots, reset tasks, format a disk or weaken verification as a workaround.
6. Once the disposable test passes, create/confirm the actual project task, Preview it and perform a real small ingest → AI → clean stop → CCC → verification sequence. Restart AI afterward. Independently restore/open a sample before declaring the machine accepted.

A fully unattended “install on any Mac” guarantee is not possible from development tests alone. Actual app activation, permissions, supported macOS/runtime, two known physical disks, visible UI state and a successful physical ingest/restore test are recipient-machine acceptance conditions. The prompts are designed to stop with a precise unresolved step instead of asserting success.

If Codex does not have a native app-control capability or the necessary macOS permission on this laptop, it can prepare the exact task specification and read-only checks but needs the operator to make the supported CCC UI selections. Do not replace that missing capability with undocumented file edits or assume that installing Codex grants Accessibility permission.
