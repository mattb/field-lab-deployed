# Ariel's backup actions

CCC copies directly from the unchanged ThunderBlade project hierarchy. Each disk has a permanent task. Previous tasks include their assigned dated folders; current excludes all those folders and automatically receives new ones.

**Every day:** double-click **Field Lab Ingest**, complete OffShoot's verified ingest, stop metadata writers when backing up live SQLite files, then double-click **Backup** to start the current CCC task. Review CCC's result and verification before releasing any source cards. **Field Lab Library** is for AI search and annotation; AI completion is not backup verification.

**At disk rotation:** ask Codex to use the repository's `field-lab-backup` skill. It plans the dated-folder boundary, configures the dedicated tasks through supported CCC UI when computer-use access is available, checks Preview and records the assignments. Codex must report any step it could not actually perform.

The optional **Backup** Desktop shortcut is installed by the skill and reads its private current-task pointer. At rotation the skill updates that pointer after configuring and checking the new task. There is no fixed default. It cannot configure filters; it can start an already configured task offline through CCC's published CLI. See [Desktop installation](DESKTOP.md#backup).

Daily backups run offline once configured. Rotation requires CCC UI configuration; Codex may need internet and appropriate app-control capability. A disk never receives a guessed date cutoff during advance setup. The cutoff is established when the operator actually rotates.

See [the skill](../.agents/skills/field-lab-backup/SKILL.md) and [the detailed procedure](BACKUP-ROTATION.md). Existing backup data and CCC tasks are preserved; withdrawing software support is not permission to delete prior copies or operational records.
