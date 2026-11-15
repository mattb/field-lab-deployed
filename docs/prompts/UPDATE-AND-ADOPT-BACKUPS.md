# Update and adopt date-filter backups

Paste this into Codex on the recipient Field Lab Mac. It authorizes the new backup setup in addition to a software update; the ordinary update prompt deliberately does not change CCC tasks.

```text
Update this Mac's Field Lab from https://github.com/mattb/field-lab-deployed to v0.1.0-alpha.9, then adopt its date-filter CCC backup workflow. Read the NEW tag's AGENTS.md, docs/prompts/FIELD-UPDATE.md, docs/BACKUP-ADOPTION.md and .agents/skills/field-lab-backup/SKILL.md directly; don't rely on a previously installed skill's instructions or automatic discovery.

Inspect the actual installed version/home, configured project, mounted primary/backup disks and CCC tasks. Preserve existing media, project databases, metadata, backup copies, private records and unrelated CCC tasks. Use the same application home. Prepare and activate the new candidate using the documented quiet-time process before running its backup-shortcut commands. Refresh the Desktop apps on this recipient Mac.

Use the original project as every CCC task's source. Configure previous disks to Include their reviewed assigned date folders and the current disk to Exclude the union of previous assignments. Don't guess assignments from calendar dates, disk names or an old task's success status. Reconcile existing copies, or explicitly leave their verification pending. Create/reuse only dedicated tasks whose source, destination UUID, physical independence, filters and verification settings have been checked. Keep the assignment ledger and handover receipts outside Git.

Use CCC's supported UI for configuration. Do not reverse engineer CCC, edit private settings or manufacture filter/task files. If computer-use tools, permissions, a disk or identity evidence are missing, finish the independent update work and report the exact pending setup step; never mark the task configured or bind Backup prematurely. With no selected destination, ask which real disk to use.

After task configuration, Preview and a disposable copy/verification/restore acceptance check succeed, bind the Desktop Backup shortcut to the actual current CCC task UUID. The shortcut must start that task, with no repository default. Preserve the binding during later software updates and change it only after a completed rotation. Do not run a real production backup just to test the Desktop shortcut. Report software updated, CCC configured, Backup bound and copy/restore acceptance separately, with any gaps and the next command/action. Do not format disks or erase cards. Do not publish a release.
```
