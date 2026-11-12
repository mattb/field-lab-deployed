# Field Lab

Offline media indexing for a filmmaker doing camera and data wrangling alone.

**OffShoot Pro → verified media drive → local AI indexer → CCC backup drive.**

OffShoot retains complete cards and verifies each copy. Field Lab queues verified ingests and adds full timestamped transcripts, brief visual notes, searchable stills, face/voice grouping and operator notes. A local browser provides search, identity review and chat with links to footage. Originals stay untouched; generated output and durable operator edits live in each project's `_DIT_NOTES` folder.

Qwen3.5-4B handles fast ingest visual notes; Qwen3.8-27B handles agentic chat, tool planning and image inspection. Parakeet, both Qwen models, embeddings and reranking run locally using MLX; face detection/embedding uses OpenCV. Text and image retrieval share the Orama architecture. No cloud inference key is required. Model weights are downloaded from pinned upstream revisions and checked; **no footage, weights, saved chats, identities or project metadata are included here**. Third-party model and dependency licenses remain their own. See [model roles and offline preparation](docs/MODELS.md).

This is an early deployment release. Supported target: **Apple Silicon, macOS 27+, Xcode Command Line Tools**, OffShoot Pro 26.3+. First installation requires internet and enough space for models, caches, dependencies and source builds; allow at least 65 GB free. Older operating systems have not been validated. OffShoot/CCC licenses are purchased separately. CCC is configured separately after explicitly choosing the second drive.

## Install with Codex

Paste this single prompt into Codex on the Mac you want to set up:

```text
Install the field-lab Codex skill from https://github.com/mattb/field-lab-deployed/tree/v0.1.0-alpha.6/.agents/skills/field-lab, then read its SKILL.md directly and start the first-time install process for that same release on this Mac. Continue in this turn rather than waiting for automatic skill discovery. OffShoot Pro and CCC are already installed.
```

This pins both the skill and application to the same tested release. Codex installs the skill, finds or clones the source checkout, checks the Mac, prepares the local software/models, and guides the drive configuration and acceptance tests. It asks for the ingest destination when the actual drive cannot be established from your session. No API key is needed. CCC task setup follows separately after the second physical drive is chosen.

The first-install process creates four native Desktop launchers: **Field Lab Ingest**, **Field Lab Library**, **Field Lab AI Progress**, and **Field Lab Stop AI**. They follow the active release across updates, show native error alerts and run without Terminal windows. Ingest opens OffShoot; selecting the card and starting a transfer remain explicit. See [Desktop launchers](docs/DESKTOP.md).

The [skill](.agents/skills/field-lab/SKILL.md), [install prompt](docs/prompts/FIRST-INSTALL.md), [update prompt](docs/prompts/FIELD-UPDATE.md), and [operations guide](docs/OPERATIONS.md) contain the underlying steps and recovery commands. A globally installed skill locates its repository independently of its installation folder.

For a reviewed source checkout:

```sh
bash deploy/bootstrap.sh install
```

This prepares a candidate and prints its release ID. Activation, machine/drive configuration and OffShoot setup follow the operations guide. No application settings change merely from cloning or preparing. Field updates use a selected tag/commit, isolated preparation, integrity checks, a quiet-time switch and a previous release for rollback. Pending AI work resumes by reconciliation.

Do the [smoke test](docs/SMOKE-TEST.md) on your own disposable footage and a test card before field use. A successful readiness check verifies assets, not every camera codec, macOS permission or physical transfer. AI search covers sampled stills and can miss appearances. Keep cards until the required independent backup and media checks pass.

USB cameras that appear in Image Capture instead of as disks can optionally use [MacDroid as an OffShoot folder source](docs/MACDROID-INGEST.md). A MISSION 1 PRO folder was accepted and enumerated; a full verified transfer through this adapter is still pending, and Skydio X10 compatibility is untested. MacDroid is separately installed/licensed and may cache source data. See the procedure for the verification boundary and camera-specific acceptance checks.

For a primary drive larger than the backup drives, optional [backup rotation](docs/BACKUP-ROTATION.md) provides a local **Back up pending footage** button, whole-card allocation, drive-swap prompts, atomic recovery and independent destination hashing. APFS clone sources preserve your current ingest hierarchy. Only explicitly registered NEW dedicated CCC tasks can be started; existing CCC tasks/settings stay untouched. Setup requires per-drive task/guard acceptance on the actual Mac. This is a partitioned archive; arbitrary later Finder moves are not automatically mirrored across offline drives. See [work-in-progress validation and limits](docs/RELEASE-v0.1.0-alpha.7.md).

## Development

Application source is in `ditnotes/`, `search/`, `viewer/` and `native-progress/`. Deployment scripts are in `deploy/`. See [CONTRIBUTING.md](CONTRIBUTING.md). Earlier research and experiments are retained under `docs/research/` and `development/` as historical context; their old paths and benchmark results are not installation defaults or portable certification.

Source code is MIT licensed. See [THIRD-PARTY.md](THIRD-PARTY.md) for downloaded components and model provenance.
