# Entry point for every Field Lab update

The complete user request is:

```text
Update this laptop’s Field Lab installation from https://github.com/mattb/field-lab-deployed
```

Do the work in this turn. The user need not supply a version, install another skill, paste internal prompts, mention backup adoption, or know this repository's filenames. This document is an executable agent procedure, not a prompt the user must copy.

## Resolve and pin the release

1. Locate the actual managed installation/home and a source checkout with the correct Git origin. Preserve local changes. If a checkout is absent, obtain a new source checkout outside media. Never pull into an active installed release. Inspect the repository's current README and AGENTS directly even if a globally installed skill is older.
2. With no explicit user ref, resolve the newest **published** GitHub release, including prereleases, by `published_at`. Do not use GitHub `/releases/latest`, which omits this project's alpha releases; do not assume the moving default branch is a release. The read-only `deploy/resolve_release.py` implements selection with stdlib Python. Alternatively use `gh api --paginate repos/mattb/field-lab-deployed/releases` and the same published/non-draft selection. If discovery fails, preserve the working install and report the failure; never guess a tag.
3. Fetch that exact tag and resolve its commit in a clean separate checkout/worktree. Record the chosen tag and commit. Read **that tag's** `AGENTS.md`, `FIELD-LAB-RELEASE.json`, `docs/UPDATE.md`, main skill and compatibility notes. Require the manifest tag to equal the selected tag and referenced procedure files to exist inside that checkout; `python3 deploy/resolve_release.py --checkout SELECTED_CHECKOUT --tag SELECTED_TAG` validates those properties without network or application changes. Follow the immutable selected release, not uncommitted/default-branch instructions. Source hashes prove integrity, not publisher identity.
4. A user-specified tag or a request for software-only scope takes precedence. Preserve that limit and report any workflow adoption deferred. If the selected historical release predates this manifest, report its legacy update procedure and compatibility rather than claiming it implements a newer migration.

## Deploy the complete release

Use `docs/prompts/FIELD-UPDATE.md` as internal operating instructions, substituting the selected immutable ref; do not ask the user to paste it. Prepare the candidate, check pinned assets and offline readiness, inspect model/reindex requirements, and activate only when OffShoot is confirmed idle and AI work is stopped cleanly. Preserve source media, shared state, project data and previous code. Don't fabricate idle/ready evidence or reset data to make an update pass. Unsupported, damaged or future layouts require an identified recovery step, not a guessed migration.

Refresh recipient Desktop apps using `bash deploy/bootstrap.sh desktop --home ACTUAL_APPLICATION_HOME` after activation. This installs Backup as well as the four core apps and preserves an existing task binding. Do not install apps on a maintainer's Mac merely while editing source. Readiness checks do not themselves prove model inference, a real OffShoot copy or CCC acceptance.

## Apply the release's workflow migrations

A normal unqualified Field Lab update includes the workflow changes listed in `FIELD-LAB-RELEASE.json`, not just source replacement. The user does not need to know their names. In this release, automatically continue with `docs/BACKUP-ADOPTION.md` and the backup skill for `ccc-date-filter-v1` after activation. The older standalone prompts' software-only restrictions apply only when that narrower scope is requested; this entry point supplies the complete update scope.

Inventory the actual project and existing Field Lab CCC setup. Configure only dedicated Field Lab tasks using the original project source: prior tasks Include assigned dates, current Excludes all prior assignments. Use supported CCC UI only. Do not infer destination identity from a mounted name, choose an unrelated task, reorganize originals or introduce another copy engine. The migration authorizes these scoped changes; it does not authorize formatting disks, erasing cards, destination deletions, unrelated task changes or an unrequested production copy.

Ask only for missing decisions/evidence, such as which independent disk is intended when it cannot be established, app-control permissions or an unavailable disk. Finish independent work meanwhile. Missing CCC computer-use capability means a pending UI step; no file/API workaround is implied. Fully configured daily backups work offline; the agent's CCC configuration may need internet and app-control access.

Check existing migration records and actual settings before changing them. If the date-filter tasks, assignment ledger and binding already match, validate and retain them instead of creating duplicate tasks or rerunning a copy. Resume a pending handover by reconciling both task configurations and the pointer. A mere receipt is insufficient if the current settings contradict it.

Use the adoption guide's private atomic records for task exports, assignments, pending handovers and verification evidence. Record the migration ID, selected release, observed state, completed steps and blockers in `APPLICATION_HOME/shared/runtime/release-migrations.json`, preserving unknown fields, other migrations and earlier history. Write a private temporary file, flush/fsync, atomically replace and fsync the directory; never put machine records in Git. Set status `complete` only after actual CCC configuration/Preview, copy/restore acceptance and current-task binding/readback pass. Otherwise record `pending` and the precise remaining step. Never report a whole update as fully adopted while the migration is pending.

## Finish

Report old/new software version, model/offline readiness, Desktop status and each required migration as complete/pending with its evidence. State whether CCC/OffShoot settings changed. A backup click starts the current task; the shortcut does not configure filters, detect an ingest/metadata writer or independently certify a copy. An unknown older installation cannot be guaranteed safe without inspecting it. Preserve the previous release and all operator data; code rollback does not undo workflow changes.
