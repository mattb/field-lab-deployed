# Withdrawn backup-rotation draft

The earlier backup-rotation implementation is withdrawn from the current source. Do not use its former commands or infer production acceptance from its development tests.

The current plan uses CCC tasks reading the original ingest folders with date/card inclusion and exclusion filters. See [date-filter rotation](BACKUP-ROTATION.md) and the [backup skill](../.agents/skills/field-lab-backup/SKILL.md).

Preserve existing backup files, task configurations and private ledgers. Withdrawal of software support is not permission to delete data, alter live CCC tasks or reset an installation.
