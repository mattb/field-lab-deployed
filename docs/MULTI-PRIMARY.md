# Change the ingest primary and keep previous volumes

This is an infrequent, explicit cutover when the current ingest drive fills. Each primary has its own original project root, date-filter assignments and permanent CCC task pairs. Field Lab never moves media between primaries or makes backup copies. OffShoot makes the verified ingest; CCC makes backups directly from the original folders. No intermediate folder hierarchy is required.

A volume label helps the operator choose a disk. Ownership uses a stable primary ID and volume UUID, not its label alone. Connecting an older volume, browsing it, refreshing its cache or starting one of its backups does **not** change the ingest destination or Desktop Backup binding.

Available in v0.1.0-alpha.11. Ordinary updates follow [preservation-only adoption](MULTI-PRIMARY-ADOPTION.md); the cutover operations below require an explicit request to change the ingest primary. Updating alone does not initialize ownership, change destinations or reconfigure existing CCC tasks.

## Persistent ownership

`APPLICATION_HOME/shared/shoot.json` is the atomic shoot registry. Profiles retain the primary's UUID, mount, relative ingest folder, project root and separate backup sets. Set names such as B001 can repeat on different primaries; task UUIDs cannot. Backup destinations must be independent physical storage and cannot overlap another registered destination tree. Old profiles/tasks remain registered after cutover.

Use `primary ledger-path --primary PRIMARY_ID` to obtain that primary's private assignment ledger path under `shared/runtime/backup-date-rotation/PRIMARY_ID/assignments.json`. Do not share one dates ledger across primaries: the same shoot date may legitimately occur on both. Keep assignment proposals, supported CCC readbacks and verification records private, outside Git.

Laptop cache storage under `shared/runtime/shoot-library/SHOOT_ID/PRIMARY_ID/` contains atomic generations of derived catalogues, vector indices, exported metadata and referenced stills. SQLite’s backup API is used transiently to extract a consistent metadata view; published caches retain no SQLite database copies. It does not contain original media. This is allowed to consume laptop storage; preserve the cache and registry across updates. A connected-source refresh failure retains the last complete generation and is shown as saved metadata with a refresh error. Cached metadata may be stale and is never proof of source availability or backup verification.

The multi-volume capability is `multi_primary: 1`. Older writers that do not support it must not be activated against this state. Code rollback does not reverse registry adoption, metadata edits, CCC settings or a completed cutover. Never restore an old registry over later operator work.

## Set up ownership before the first cutover

Run commands from the reviewed source checkout with the same actual application home throughout. Examples use placeholders, never machine defaults:

```sh
bash deploy/bootstrap.sh primary --home APPLICATION_HOME init --name SHOOT_NAME
bash deploy/bootstrap.sh primary --home APPLICATION_HOME status
bash deploy/bootstrap.sh primary --home APPLICATION_HOME register --volume NEW_MOUNT --ingest-folder RELATIVE_PROJECT_FOLDER --label DISPLAY_LABEL
```

`init` adopts the configured mounted primary; it does not guess or change the destination. Its backup registry starts empty: enroll the existing active primary’s actual dedicated CCC pairs and current set from supported-UI readback before relying on Desktop Backup again. Preserve and reconcile its existing assignment ledger into the path returned by `ledger-path`; do not seed an empty ledger over prior assignments or manufacture missing verification. Bind the accepted active pair with its primary/set IDs. `register` creates an inactive profile and prints its stable ID. Read actual status before every operation. Registration is not a cutover. Do not recreate missing `/Volumes` mount directories or select a same-named replacement disk.

If the new project root does not exist yet, finish transfers, stop AI and run the guarded `cutover prepare` command below first. It creates the relative ingest directory only after verifying the mounted target UUID; repeat prepare safely when continuing. Do not create a missing mount directory. Configure the new primary's first dedicated CCC task through the supported CCC UI using [backup rotation](BACKUP-ROTATION.md). The task's source is **this new primary's original project root**. Its current Exclude filter begins with no closed assignments for this primary. Read back the task UUID, endpoints, filters, deletion policy and Preview membership. Retain prior tasks unchanged. The CLI starts/monitors existing tasks; it does not configure filters. Do not edit private CCC files or invent an import format.

Register the observed pair with `primary backup-register --primary PRIMARY_ID --label B001 --readback PRIVATE_READBACK.json`, then `primary backup-current --primary PRIMARY_ID --label B001`, through the same bootstrap command. The readback contains `task_uuid`, `source`, `destination_mount`, `destination_uuid`, `destination_relative`, and `configuration_evidence` with matching `task_uuid`, `source`, absolute `destination`, `observed_at` and `method: "supported-ui"`. A JSON assertion is not independent proof of CCC configuration: actually inspect its supported UI and preserve evidence. The schema also accepts supported API readback if an author-published endpoint actually provides it; do not infer that CCC currently offers one.

## Perform a cutover

Use the [cutover skill](../.agents/skills/field-lab-cutover/SKILL.md). Save operator work, finish OffShoot transfers normally, observe no active/queued transfers, wait for CCC idle, and stop AI. Do not interrupt transfers to make the guards pass.

```sh
bash deploy/bootstrap.sh stop --home APPLICATION_HOME
bash deploy/bootstrap.sh cutover --home APPLICATION_HOME prepare --primary NEW_PRIMARY_ID --offshoot-idle
bash deploy/bootstrap.sh cutover --home APPLICATION_HOME apply --offshoot-idle
bash deploy/bootstrap.sh cutover --home APPLICATION_HOME status
```

Prepare caches the old library before disconnecting it, creates the verified target’s ingest directory and records a durable intent. Configure/register the target CCC pair after prepare if its source directory was previously absent; only then run apply. Apply changes Field Lab configuration, configures OffShoot through the existing supported setup path, reads back settings/preset and binds Backup to the registered target task. It leaves the service stopped and cutover pending. The registry's active-primary commit has not happened yet; normal ingest/Backup starts are blocked. OffShoot disk/dropzone observation and a real transfer still need acceptance. Do not bypass the pending guard by editing JSON or recording invented steps.

**Run the special validation service while the intent remains pending:**

```sh
bash deploy/bootstrap.sh cutover --home APPLICATION_HOME validate
```

This starts the target indexer using the exact pending intent. It does not commit the active primary. Observe OffShoot's destination/dropzones, then transfer an operator-selected disposable card/clip through OffShoot, require verified receipt admission, inspect the indexed clip and saved/reopened metadata in the library. Preserve actual evidence.

Stop AI and prevent browser/operator metadata writes before CCC directly copies live project SQLite files. Run the target CCC task for validation with the intent ID printed by status:

```sh
bash deploy/bootstrap.sh stop --home APPLICATION_HOME
bash deploy/bootstrap.sh backup-shortcut --home APPLICATION_HOME run --primary NEW_PRIMARY_ID --set-label B001 --validation-intent PENDING_INTENT_ID
```

Review CCC's actual copy and post-copy verification results and perform a restore/read check. A successful process exit is not a checksum/restore receipt. Source cards remain retained until the operator authorizes reuse.

**The validation service must be stopped before acceptance and finish.** Prepare a private acceptance JSON with the exact target `primary_id`, `volume_uuid`, `task_uuid`, fresh numeric `observed_at`, `method: "operator-observed-acceptance"`, a nonempty `artifacts` list referring to actual checks, and true values for `offshoot_destination_observed`, `verified_ingest`, `ai_index_checked`, `backup_copy_verified`, `restore_checked`. These flags record human attestation; they do not make unperformed checks true or cryptographically prove them.

```sh
bash deploy/bootstrap.sh cutover --home APPLICATION_HOME accept --evidence PRIVATE_ACCEPTANCE.json
bash deploy/bootstrap.sh cutover --home APPLICATION_HOME finish
bash deploy/bootstrap.sh start --home APPLICATION_HOME
```

Finish checks target configuration and Backup ownership, commits active-primary atomically, and leaves service restart explicit. Verify status and an ordinary Desktop launch afterwards. Do not change the target backup selection between acceptance and finish.

## Reconnect and back up an older primary

Connect its registered source and paired backup disk. The multi-volume library lists connected and offline profiles. Select the older volume to browse or edit it; choose its backup set in **Backups** and **Run backup**. This is an explicit run, not a cutover. Stop metadata writers before a file-level metadata copy and review CCC verification afterwards. The Desktop Backup action remains bound to the active ingest primary's current set.

The equivalent command is `backup-shortcut --home APPLICATION_HOME run --primary OLD_PRIMARY_ID --set-label OLD_SET_LABEL`. It validates source/destination UUIDs, physical independence and registered task existence before invoking CCC. It cannot independently detect that someone manually changed that existing task's endpoints: inspect actual CCC settings/Preview again after such changes.

For a changed inactive mount path, `primary --home APPLICATION_HOME refresh-mount --primary OLD_PRIMARY_ID --volume ACTUAL_MOUNT` verifies UUID and marks its tasks for source-path review. Reconfigure/read back those dedicated tasks through supported CCC UI and re-register before running. Active-primary root changes require coordinated reconciliation; they are rejected by this helper. A same-named wrong disk is never accepted.

Manual Finder reorganization is allowed but affects CCC membership and file identity. Reconcile renamed/moved dates/card folders and review both current and closed filters; no automatic coverage guarantee follows a reorganization. Never remove originals or old copies to make a plan pass.

## Library scope

One existing Orama search architecture covers namespaced documents and vectors across the shoot. Offline profiles support cached transcripts, tags, notes, People evidence and stills. They cannot play original media or accept source annotations; connect the owning volume. A volume chooser is a view filter, not an ingest selector. Agentic chat and Premiere actions are scoped to a selected connected primary; Premiere timed selects cannot mix primaries. Person evidence remains separately owned across primaries: equal names or IDs do not cause automatic cross-volume merges.

## Recover after interruption

Read `primary status`, `cutover status`, service status and actual external app state first. Reuse the same pending target/intent. Repeat prepare/apply only after reconciling what succeeded; atomic records preserve completed steps and prior state. Apply may have changed configuration before a crash even though active-primary is still old. Do not simply restart normal ingest, switch disks, delete pending state or pick a different target.

A backup launch interrupted before its result is known remains unknown. Inspect fresh matching CCC task history, then use `backup-shortcut reconcile-attempt --primary PRIMARY_ID --set-label SET_LABEL --readback PRIVATE_HISTORY.json` through bootstrap. Readback requires `method: "supported-ui-history"`, matching `task_uuid`, fresh `observed_at` and `result` of completed, failed, cancelled or not-started. Reconciliation records the observed outcome; it does not mark content verified and does not automatically retry a copy.

Cancellation is not exposed as a convenience CLI. If abandoning a partially applied cutover, preserve the journal and reconcile both OffShoot and Backup to their recorded before-state through supported interfaces before any deliberate recovery. The registry's internal cancellation guard requires fresh matching external readback; deleting the intent is unsafe.

Automated tests use disposable local folders, injected volume identities and fake external commands. They cover ownership collisions, reconnects, interrupted publication/cutover and guarded launches; they do not certify recipient hardware, real CCC filters/copies or OffShoot transfer acceptance.
