# Install and maintain Field Lab

Field Lab runs locally on an Apple Silicon Mac. OffShoot Pro copies and verifies complete cards; Field Lab queues and indexes only verified ingests; CCC can later copy the media and `_DIT_NOTES` to a second drive. This repository includes no footage, face/voice labels, project database or prebuilt dataset. Use your own media for smoke tests.

First installation needs internet and at least 65 GB free for model downloads, installed generations, caches and build tools. [Model roles](MODELS.md) keep Qwen3.5-4B on the ingest path and load Qwen3.8-27B for agentic chat. The currently supported deployment target is Apple Silicon with macOS 27 or later, plus Xcode Command Line Tools (including Swift). Install and activate OffShoot Pro 26.3 or later separately. Install CCC separately if it will be used. The installer does not purchase apps or licenses. Older Macs/macOS versions have not been validated by this deployment.

Use the copy/paste [first-install prompt](prompts/FIRST-INSTALL.md) or [field-update prompt](prompts/FIELD-UPDATE.md) in Codex. They ask Codex to inspect the actual machine and run the checked-in scripts rather than infer settings from a development machine.

## Commands used by the prompts

Run these from the reviewed source checkout. All paths containing spaces must stay quoted. The shell entry point downloads a checksum-pinned Python first, so an existing development Python or virtualenv is not required.

```sh
bash deploy/bootstrap.sh status
bash deploy/bootstrap.sh install
```

`install` prepares and checks a candidate; it prints a `release_id` and does not activate automatically. At a quiet point, substitute that exact ID and the operator-selected destination:

```sh
bash deploy/bootstrap.sh activate --release RELEASE_ID --offshoot-idle
bash deploy/bootstrap.sh configure --volume "/Volumes/YOUR DRIVE" --ingest-folder Ingests
bash deploy/bootstrap.sh configure-offshoot --offshoot-idle
bash deploy/bootstrap.sh verify-offshoot
bash deploy/bootstrap.sh desktop
bash deploy/bootstrap.sh doctor
bash deploy/bootstrap.sh start
```

`--offshoot-idle` records a check you have actually made in OffShoot; it does not detect running transfers. Do not supply it while copying or merely because a timeout expired. Configuration backs up affected OffShoot settings and changes its destinations. Review the planned destination first. `verify-offshoot` reads back preferences, active preset and hook files; selected disk dropzones still need visible confirmation and a disposable verified transfer. Service startup is separate so settings can be reviewed before processing.

For updates, fetch/check out the maintainer's stated tag or commit in the source checkout, preserving local changes, then:

```sh
bash deploy/bootstrap.sh update
bash deploy/bootstrap.sh activate --release RELEASE_ID --offshoot-idle
bash deploy/bootstrap.sh doctor
bash deploy/bootstrap.sh start
```

The candidate ID is a source-tree hash, distinct from the Git commit; record both. `--home "/chosen/application/home"` selects another application home on every command. Use the same home consistently. `stop`, `reconcile`, and `status` are available through the same entry point. Roll back at a quiet point with `rollback --offshoot-idle`, check `doctor`, then explicitly `start`; review database compatibility first. No command here configures CCC. `ccc-status` inventories its supported CLI without changing tasks; see [CCC setup](CCC-SETUP.md). Read [analysis/UI upgrade behavior](UPGRADES.md) before requesting a reindex.

The optional [Desktop apps](DESKTOP.md) provide double-click entry points for ingest, library, native progress and stopping AI. They keep their paths across release switches.

For Image Capture/MTP devices without a mounted filesystem, see [MacDroid ingestion](MACDROID-INGEST.md). The adapter supplies an optional folder source to OffShoot; the verified physical destination, receipt gate, AI service and separately configured CCC remain the same. Native CloudStorage caching limits claims about independent camera rereading. This is not an automatic camera importer or a substitute for the adapter's physical acceptance test.

## What persists

The source checkout can be anywhere outside a media project. The installed application uses this layout by default:

```text
~/Library/Application Support/Field Lab/
  current -> releases/<release-id>
  previous -> releases/<previous-release-id>
  releases/<release-id>/       # isolated code, tools, virtualenv and built UI
  shared/
    config.json               # this machine's drive UUID, paths, port and switches
    model-generations/<lock-hash>/  # pinned local weights; reused for the same lock
    runtime/                  # durable queue, receipts, state and diagnostics
```

Release publication changes a small pointer after preparation succeeds. Each release links to its model generation; a changed asset lock prepares a separate generation. Downloaded models and saved work are not removed when a release changes. Keep the source checkout and application home out of media ingestion scopes.

A media project's layout is independent:

```text
chosen-drive/Ingests/
  YYYYMMDD/CARD-COUNTER/       # complete OffShoot card copy, untouched by AI
  _DIT_NOTES/                 # AI objects, catalog, index and project.sqlite3
```

The durable project database contains operator names, notes, identity review, links and chat. Preserve `_DIT_NOTES` with the media. Back up application `shared/` separately so transfer evidence and pending jobs survive a laptop replacement. Neither location belongs in GitHub.

## Preparation and activation

Download/build a candidate while the existing application remains usable. Model preparation may use the network; ordinary ingest/index/search/chat must use local assets. An interrupted or incomplete preparation is not an installed release. Re-run the same preparation to reuse valid cached downloads.

Before activation, finish or pause work at a quiet point. No OffShoot transfer may be in progress. Stop the AI service through the provided command, not by force-killing arbitrary processes. The command requests termination, waits up to eight seconds, then may kill its own remaining process group; completed seals and committed metadata survive, and unfinished clips reconcile on restart. It does not promise that every inference finishes gracefully. A code switch does not authorize changing OffShoot preferences, resetting its counter, discarding jobs or touching project metadata.

After activation, verify the actual service, UI, offline assets and existing operator data. Keep the previous working release. If the mounted ingest drive is absent, preparation may complete but live ingestion validation cannot. Do not create a fake directory with the expected drive name.

## OffShoot configuration

Configuration is a first-install or explicit integration action, not part of every update.

1. Inspect connected writable volumes. Show the chosen mount, hardware/volume identity and planned ingest folder. Record the actual volume UUID; do not reuse an example UUID or trust the name alone.
2. Back up the user's current OffShoot preferences and any existing preset being replaced. Check there are no transfers running and open OffShoot's Disks view, closing settings/dialogs so its supported URL API can respond.
3. Install the complete-card preset with source-and-destination verification, no source-file renaming, no flattening, no move, no automatic ejection and transfer logging enabled. Preserve or advance the existing counter; never reduce it merely because the new ingest directory is empty.
4. Point scripts at the stable installed application path. Successful verified-transfer receipts authorize queuing; a callback by itself does not establish complete verification. Failed, partial and unsupported receipts must not trigger AI.
5. Run the [smoke test](SMOKE-TEST.md), including a small real verified OffShoot transfer, before depending on the automation.

Card names are labels, not reliable camera identity. Give each camera/card a clear name before the shoot when useful; keep the entire copied card hierarchy. Do not infer a camera model or person identity from a volume name.

## CCC

CCC remains untouched by these install/update instructions. To add it later, choose the second physical destination explicitly. Configure one task from the actual ingest project to drive 2, including `_DIT_NOTES`; review what happens to deletions and reorganized folders before the first run. Do not enable a task that starts while OffShoot is copying or while Field Lab is writing the project. Stop AI cleanly before making a file-level clone of its metadata database; a live SQLite backup requires an application-aware snapshot/backup method.

Verify the second copy and check playable media before authorizing card reuse. A future Finder reorganization of drive 1 requires reconciliation and another CCC run; it is not propagated by OffShoot itself.

If the primary exceeds the size of each secondary drive, use the separately requested [backup rotation manager](BACKUP-ROTATION.md) rather than a whole-project mirror task. `backup ui` is opt-in, uses registered dedicated CCC tasks and APFS clone projections, and preserves the current ingest hierarchy. Its checksummed project ledger and spool survive code updates. Do not change existing CCC tasks during installation/update. Partitioned archive paths remain stable after a set closes; the whole-project Finder-mirror behavior above does not apply to rotation. Stop the rotation UI before a release switch; unknown CCC attempts reconcile on restart without stopping CCC.

## Interrupted work and recovery

After a power loss or interruption, mount the configured drive and start the service. It reconciles sealed AI outputs with work still required. Completed clips are reused; pending clips resume. Unknown ingest folders need valid OffShoot verification evidence. Do not fabricate a success receipt to bypass that requirement.

`Waiting for project` means another local writer currently holds the project lock. The browser remains available while the indexer waits. If a wait outlasts one retry window, the ingest job stays pending and retries automatically; it does not become a failed ingest. Completed clip analysis remains sealed and is reused if index publication must retry. Stopping the service interrupts this wait promptly and leaves the queue resumable.

For an absent/replaced drive, inspect mount and UUID; reconnect the original volume or deliberately reconfigure for the replacement. For a full disk, leave originals and metadata in place, free unrelated storage and retry. For a download failure, leave the old release active and retry preparation when internet returns. For readiness/test failure, preserve the failed candidate and diagnostics and use the old release.

A code rollback changes executable files, not model/queue/project data. If a new release changes database schemas or analysis formats, its release notes must state whether the old release can read them. Backup/restore decisions must account for operator edits made after the update. Do not restore an old project database automatically.

## Maintainer releases

Maintain source in Git and promote a reviewed commit as a release/tag only after the automated failure tests, local offline check and disposable own-media test pass. Record the tested platform, model lock, runtime versions and any data compatibility changes. Field machines should install a stated release/commit, not an unreviewed moving branch. A code update and a model change are separate review decisions; model changes may need internet, storage and fresh indexing.

Tests on a development Mac establish only their recorded coverage. A fresh-machine install and first physical card transfer still need validation on the field laptop.

`status` shows deployment pointers and provenance. For live queue/backlog errors use the running local `/api/status` or `shared/runtime/status.json`; a saved status file can be stale after an interrupted process.
