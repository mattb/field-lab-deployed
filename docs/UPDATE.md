# Entry point for every Field Lab update

The complete user request is:

```text
Update this laptop’s Field Lab installation from https://github.com/mattb/field-lab-deployed
```

Continue in this turn. The user need not supply a version, install another skill, paste internal prompts or know the migration name. This document is the operating procedure, not an additional prompt to request.

## Resolve and pin the release

1. Locate the actual managed installation/home and source checkout with the correct Git origin. Preserve local changes. If no checkout exists, obtain a new one outside media. Never pull into an active installed release. Inspect the current repository README and AGENTS even when an installed skill is stale.
2. Without an explicit ref, select the newest **published** GitHub release, including prereleases, by `published_at`. Use read-only `deploy/resolve_release.py` or `gh api --paginate repos/mattb/field-lab-deployed/releases` with the same non-draft rule. Do not use the stable-only `/releases/latest`, infer a tag from its number or substitute the moving branch after discovery failure.
3. Fetch that exact tag into a clean separate checkout/worktree. Record tag and commit. Read **that tag's** `AGENTS.md`, `FIELD-LAB-RELEASE.json`, update entry point, main skill and compatibility notes. Validate manifest tag and local referenced paths using `python3 deploy/resolve_release.py --checkout SELECTED_CHECKOUT --tag SELECTED_TAG`. A source checksum proves integrity, not publisher identity.
4. Determine the installed baseline from actual provenance and read all intervening changelog entries through the selected target. For an unknown baseline read the full history and report uncertainty. **History explains changes; it does not authorize executing old releases' migrations.** Apply only the selected target's manifest/procedures. In alpha.11, do not run alpha.10's CCC setup migration even if its old record is missing or pending.
5. A user-specified ref or narrower software-only request takes precedence. If a selected historical release predates this manifest, report its legacy procedure rather than claiming newer behavior.

## Preserve the current workflow before deployment

For alpha.11 follow [preservation-only adoption](MULTI-PRIMARY-ADOPTION.md). Use `python3 deploy/update_preservation.py --home ACTUAL_APPLICATION_HOME capture` before activation/Desktop refresh and retain its printed receipt ID. Afterward use `python3 deploy/update_preservation.py --home ACTUAL_APPLICATION_HOME check --id RECEIPT_ID` before ordinary restart. This captures/verifies the current ingest configuration, Backup binding, existing shoot registry if any, and read-only CCC inventory; follow that procedure for the full evidence/recovery rules. Keep private receipts in the actual application home. Preserve pending work and unknown fields.

The normal alpha.11 update **does not change the ingest destination or existing CCC tasks**. Do not run `configure`, `configure-offshoot`, `primary init`, a cutover, `backup-current` or `backup-shortcut bind`. Do not create/edit CCC tasks, filters, endpoints, schedules or deletion settings. Do not start a production backup as an update test. A missing old migration receipt or unbound Backup icon does not expand this scope. Preserve it and report it.

If no shoot registry exists, retain the established single-primary workflow. Installing this release makes the new capabilities available; enrolling primary ownership and configuring an Annex happen only on a later explicit cutover request. If a registry exists, retain it and its active primary, CCC pairs, assignment ledgers, cache and pending intent. Reconnecting a drive never elects it active. Do not finish a pending cutover as part of an ordinary update.

## Deploy the complete release

Use `docs/prompts/FIELD-UPDATE.md` as internal instructions with the selected immutable ref. Prepare a separate candidate, check pinned assets/offline readiness, inspect compatibility/reindex requirements and activate only at an observed quiet moment with OffShoot idle and AI stopped cleanly. Preserve originals, project metadata, shared configuration/models/queue, private backup records and prior code. Do not fabricate readiness or reset state to make an update pass. Unsupported/damaged/future layouts require a concrete recovery step.

Refresh recipient Desktop apps using `bash deploy/bootstrap.sh desktop --home ACTUAL_APPLICATION_HOME` after activation. This refreshes Backup and four core apps while preserving the existing binding. It does not start a backup or choose a task. Do not run recipient installation on the maintainer's Mac merely while developing source.

This release adds `multi_primary: 1` to the data-capability guard. Incompatible older writers must not be activated after adoption; code rollback cannot reverse registry/metadata changes. There are no new model weights or ingest recipe changes relative to alpha.10, so reuse compatible AI outputs and vectors. Do not clear or blanket-reindex a project. Older baselines may need their documented additive metadata migration/readiness work, while retaining the preservation scope above.

A pending cutover blocks ordinary startup intentionally. Report that recovery condition and preserve the journal; do not erase it, pick a new destination or bypass the guard. A disconnected configured primary is not permission to change its destination or create a missing mount directory. Follow actual pre-update backup/readiness checks and report deferred checks accurately.

## Apply the selected release's migration

Alpha.11 lists `multi-primary-preserve-v1`, implemented by [MULTI-PRIMARY-ADOPTION.md](MULTI-PRIMARY-ADOPTION.md). It records preservation/readiness; it does not enroll a primary or alter external app configuration. Verify after deployment that the captured configuration/binding/registry and CCC inventory are unchanged. If a preservation check differs, preserve evidence and report the update as needing review; never silently “repair” it by rewriting tasks or pointing at a convenient drive.

Use the procedure's private atomic records. Preserve earlier migration records/history; alpha.10's missing or pending backup adoption remains historical state and does not require CCC edits in this release. Record the selected release, checks and concrete blockers. Complete means this preservation migration's actual checks passed, not that an Annex cutover or real copy/restore was tested.

## Finish

Report old/new software version, model/offline readiness, Desktop status and preservation checks. Explicitly state whether the ingest destination and CCC tasks were unchanged. Distinguish software availability from any deferred primary enrollment, pending cutover recovery and recipient hardware acceptance. A backup click starts the existing current task; it does not configure filters, freeze metadata writers or independently verify a copy. Preserve all operator data and previous code.
