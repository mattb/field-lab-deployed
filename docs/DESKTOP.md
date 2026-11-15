# Desktop apps

After activating and configuring Field Lab, install its five local Desktop apps:

```sh
bash deploy/bootstrap.sh desktop
```

Use `--home "/your/application/home"` consistently for a nondefault installation. `--desktop "/chosen/existing/folder"` installs the shortcuts elsewhere; the folder must exist and be outside the application home. This command builds the apps locally and installs shortcuts. It does not open OffShoot, start a transfer, run AI or touch CCC during installation.

Double-click an app in Finder:

| App | Action |
| --- | --- |
| **Field Lab Ingest** | Checks the configured destination is mounted, opens OffShoot and starts/resumes AI processing. Select the card and review the destination/preset before adding transfers in OffShoot. |
| **Field Lab Library** | Starts/resumes AI, waits for the local library endpoint and opens it in the default browser. |
| **Field Lab AI Progress** | Starts/resumes AI and opens the native progress window. |
| **Field Lab Stop AI** | Stops AI safely; pending work reconciles on the next start. It does not stop OffShoot or CCC. |
| **Backup** | Starts the current CCC task set by the backup skill. With no task configured, shows a native setup error and starts nothing. |

The apps use native AppleScript applets and native failure alerts. They do not open Terminal. Icons are obtained from the Mac's installed system resources; no OffShoot icon or proprietary application binary is redistributed. They are compiled and ad hoc signed on the recipient Mac, not shipped as notarized downloadable applications.

Each of the four core shortcuts points to a managed app generation under `shared/runtime/desktop-apps`. Those apps call the stable application `run` launcher, which resolves the currently active release. Updating Field Lab does not require changing the shortcut's executable paths. Running `desktop` again refreshes managed app generations and restores a missing shortcut. Older generations are retained; no application cleanup is performed automatically.

The installer refuses to replace an existing unrelated file, app or symlink with one of these names. It checks the four core app names before publishing. Rename the conflicting unrelated item yourself or choose another shortcut folder; do not delete user content to force installation. Ownership records are persisted before shortcuts are created so an interrupted installation can resume safely.

A missing drive or service startup error produces a native alert. Reconnect the configured destination and retry, or inspect AI Progress/status with Codex. Opening Ingest does not call Add Transfers, reset preferences, automatically select a source card or authorize card erasure. CCC remains a separately configured/validated task.

Development tests compile, sign, verify and decompile all four real app bundles in a temporary Desktop folder, including a home path with spaces, Unicode, quotes and shell characters. They also cover ownership conflicts, interruption/retry, release switching and dispatcher behavior with mocked apps. They never launch those applets or change the actual Desktop. The first-install acceptance still needs a real double-click test on the recipient Mac.

## Backup

The **Backup.app** installed by the `desktop` command starts the CCC task designated current by the date-filter rotation skill. It has no built-in task or dataset. Install with `bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME install`; after actual task setup, bind with `backup-shortcut --home APPLICATION_HOME bind --task-uuid ACTUAL_UUID --mode start` through the same bootstrap command. Read back with `status`.

The skill changes the private pointer only after a completed handover. The Desktop icon never needs repointing. It reads the pointer once, confirms the task still exists and CCC is idle, opens CCC and starts that task via its documented CLI. It waits for CCC, then asks the operator to review the result; it does not create an independent verification receipt. An unconfigured shortcut shows a native error. Installation never starts a task and refuses an unrelated Backup.app. No CCC configuration is changed. The app reads shared runtime state directly, so it needs no Python process when clicked. Existing four Field Lab launchers are unchanged.

## When the ingest primary fills

Use Codex with the repository's `field-lab-cutover` skill for the infrequent primary change; see [multi-primary procedure](MULTI-PRIMARY.md). There is no automatic disk choice or cutover-on-connect Desktop action. The same five icons remain. Once accepted, Ingest/Library follow the committed primary; Backup starts that primary's current CCC set. Pending cutover blocks ordinary ingest/backup startup and requires the skill's explicit validation start.

To back up a reconnected older primary, open Field Lab Library, choose its volume, expand **Backups**, and run the chosen old pair. This leaves Desktop Backup and active ingest unchanged. Stop metadata writers before copying live project metadata and inspect CCC verification. Offline profiles remain browseable from laptop metadata/still/index caches; source edits and playback require reconnecting their own volume.
