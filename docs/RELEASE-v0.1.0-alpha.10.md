# v0.1.0-alpha.10 — one-prompt updates

The complete user request is now:

```text
Update this laptop’s Field Lab installation from https://github.com/mattb/field-lab-deployed
```

README, AGENTS and the main skill route it to `docs/UPDATE.md`. The resolver selects the latest published release including prereleases (GitHub's stable-only `/releases/latest` is not used). `FIELD-LAB-RELEASE.json` pins this release's update procedure and required workflow migrations. Codex deploys the selected immutable tag, then follows its migration procedures; no long prompt, version number or separate “adopt backups” request is required.

The required CCC date-filter migration is scoped to dedicated Field Lab tasks and established/operator-selected disks. Existing correct configuration is retained; pending handovers resume. Missing disk identity, app-control permission, unsupported layout or actual acceptance evidence is reported as pending rather than guessed through. CCC remains the copy engine, using original folders and supported configuration UI. Explicit software-only requests override default migration scope.

No models, requirements, inference recipe or project metadata schema changed. No AI reindex is required. Preserve all media, shared state, private backup records, bindings and previous releases. Updating code does not certify actual CCC filters, drive hardware or copy/restore success. The short prompt removes the need to carry maintainer instructions, not the need for missing local information or supported app-control access.

The manifest also names the [per-release changelog](../CHANGELOG.md). Update agents read every intervening published entry from the actual installed baseline. Developer rules in AGENTS require accurate commit-backed changes, compatibility/reindex/rollback guidance, self-contained manifests/procedures, resumable migrations and exact-commit validation before publication.
