# Rotate CCC backup disks with date filters

CCC reads the original ingest project directly and performs every backup copy. The ThunderBlade's date/card folders stay where they are. Rotation uses one permanent dedicated CCC task per physical backup disk, plus a private assignment ledger. No cutoff dates need to be guessed before the shoot.

## Selection model

All tasks use the same original project root as source, and a dedicated project folder on their respective backup disks as destination.

| Set | Task filter | Selected content |
| --- | --- | --- |
| Previous set 01 | Include | Its explicitly assigned completed date/card folders |
| Previous set 02 | Include | Its explicitly assigned completed date/card folders |
| Current set 03 | Exclude | Everything except the union of folders assigned to all previous sets |

CCC's Exclude mode automatically includes newly created source folders unless they fall under an excluded folder. Daily ingests therefore need no filter change. Include mode on previous disks keeps their scope explicit. Both behaviors are documented in [CCC's task-filter guide](https://support.bombich.com/hc/en-us/articles/20686509510167-Configure-the-task-filter-to-exclude-files-and-folders-from-a-task).

Rotate between completed shoot days where practical. If one day cannot fit, assign complete card folders instead. A late card added under a date owned by an earlier set belongs to that set; mark it pending and request that disk. Do not silently treat the current disk's exclusions as evidence that the late card is already backed up.

## Rotation with Codex

Use the repository's [field-lab-backup skill](../.agents/skills/field-lab-backup/SKILL.md). The skill plans and checks the handover; CCC configuration uses its published UI controls. Limit changes to explicitly identified Field Lab tasks and preserve unrelated tasks.

1. Inspect the project, outgoing task and actual destination disk UUID/physical identity. Check capacity and CCC status. Do not interrupt an active copy.
2. Finish and verify the outgoing set's assigned footage before closing it. Record exact relative folder assignments, destination identity, task UUID and verification evidence in the private ledger.
3. Change the outgoing task to Include mode and explicitly select its assigned folders. Changing CCC's default filter mode clears conventional rules, so build and inspect the complete new selection rather than assuming the former rules survived.
4. Create a new dedicated task for the next disk, using the same original project root. Set Exclude mode and exclude the union of every previous set's assignments. Keep new folders included by default.
5. Preview both tasks and check for gaps, unintended overlap, wrong endpoints and capacity shortfalls. Read back the complete filters and reviewed settings. Mark the new set current only after configuration is confirmed.
6. Run a disposable copy/verification/restore acceptance test before relying on a newly configured disk/task. A successful task exit alone is not sufficient backup evidence.

Retain a rotation-pending record until the handover is complete. An interrupted handover must resume from the actual CCC settings and ledger, not reset assignments or assume both task changes succeeded. Never mark a generated plan as applied configuration.

## Published-interface limitation

Bombich documents [starting and monitoring existing tasks by CLI](https://support.bombich.com/hc/en-us/articles/20686443072535-Using-the-ccc-Command-Line-Tool-to-Start-Stop-and-Monitor-CCC-Backup-Tasks), filter import through the UI, and [opening previously exported task configurations](https://support.bombich.com/hc/en-us/articles/20686444541975-Task-Organization). No published headless filter-generation/configuration API has been established.

Accordingly, do not promise fully automatic rotation from a Field Lab Desktop action. An agent with supported app-control tools can perform the authorized CCC UI changes; otherwise the operator must make them. Existing, configured CCC tasks can run offline without Codex or internet. Do not fabricate `.ccctask` or filter files, inspect private application internals, rewrite CCC preferences/databases, or substitute a different copying engine.

## Metadata and filesystem changes

Keep project metadata in the backup scope intentionally; document which metadata revision each set contains. Stop AI cleanly before CCC copies live SQLite files, or use a separately reviewed application-aware SQLite backup method. Do not copy a live database without its WAL and claim consistency. Keep private plans, labels and task exports out of Git.

Path-based filters depend on the original folder hierarchy. Finder moves and renames require assignment/filter reconciliation, including disconnected disks. Do not represent this as automatic mirroring across offline sets. Preserve backup data and existing ledgers during code updates; never delete or reset them as an upgrade workaround.

## Acceptance

Use disposable local folders and simulated task selections for skill roleplay. Cover new date folders, late footage in an old date, same-day card splits, capacity shortfalls, missing/wrong disk identities and interrupted handover. Those tests establish planning behavior only. Actual CCC UI configuration, real transfer verification, physical drive swaps and restore acceptance remain recipient-machine checks. No card erasure is automatic.
