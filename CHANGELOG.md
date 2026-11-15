# Field Lab changelog

Read the entries after the installed version through the selected target before updating. Version boundaries below were checked against Git commits, remote tag targets and published GitHub release metadata; dates for published releases use America/Los_Angeles. A withdrawn draft is not an install target. Detailed historical documents may describe development evidence rather than recipient-machine acceptance.

## v0.1.0-alpha.10 — 2026-11-15

Changes since alpha.9: one repository-URL request now resolves the newest published release, including prereleases, pins its tag/commit and follows the selected release's own update manifest. Normal updates include declared workflow migrations; explicitly software-only updates remain narrower. README, AGENTS and the main skill all route to [the same entry point](docs/UPDATE.md).

- Adds `FIELD-LAB-RELEASE.json`, published-release discovery and validation of the selected manifest/procedure paths. No stable-only `/releases/latest` fallback or guessed moving-branch release.
- Requires per-release change/upgrade notes and developer maintenance rules. Includes this history so an older laptop can understand cumulative changes without this chat or a large pasted prompt.
- Includes `ccc-date-filter-v1` as an idempotent workflow migration: retain correct existing setup; reconcile pending handovers; configure only dedicated Field Lab tasks on established/operator-selected disks; bind Backup after acceptance. Missing destination/app-control or verification evidence remains pending.
- **Update impact:** no model pins, dependency lock, analysis recipe or project data schema change relative to alpha.9; no reindex needed for these changes. Preserve all shared state and migration history. The update procedure may perform scoped CCC setup after software activation; a software-only request does not.
- **Validation/limits:** release-selection/manifest tests, source candidate tests, existing deployment suites and independent short-request roleplay. This is not proof of CCC UI/physical copy acceptance on the recipient laptop.
- Source: [one-prompt entry implementation](https://github.com/mattb/field-lab-deployed/commit/8d238e64b8f18a1163a7e1f105c19174bb52f163), plus the changelog/maintenance follow-up included in this tag. [Release notes](docs/RELEASE-v0.1.0-alpha.10.md).

## v0.1.0-alpha.9 — 2026-11-15

Changes since alpha.8: replaces the former backup rotation implementation with CCC reading original dated project folders directly. Removes the former rotation engine/UI/CLI and their tests; existing copies, tasks and private records are preserved.

- Adds the backup skill and read-only date-filter proposal helper. Previous tasks Include assigned dates; current Excludes all previous assignments. CCC UI remains necessary for task/filter changes; no headless import API is claimed.
- Adds the native Desktop **Backup** action, installed with the four core apps. It has no predefined task; a private atomic binding identifies current. Refreshes preserve the binding; launch checks task existence/CCC idle and starts via the published CLI. It does not stop metadata writers or independently verify a copy.
- Adds first-set/adoption instructions, legacy-task/preflight/schedule inspection, assignment and pending-handover records, candidate/source packaging tests and blinded local-folder roleplays.
- **Update impact:** no new models, dependency lock, inference recipe or project schema relative to alpha.8. No backup-driven reindex. At this historical release, software update and backup adoption were separate requests; alpha.10 removes that extra-prompt requirement. Retired automation must be inspected rather than restarted. Code rollback does not undo CCC configuration.
- **Validation/limits:** 505 local Python tests (five skips), 45 viewer tests/build and all four CI jobs passed. A clean source archive compiled an unbound native shortcut in a disposable folder. Actual laptop activation, CCC UI filters and physical copy/restore remained recipient checks.
- Source/tag target: [726fb56](https://github.com/mattb/field-lab-deployed/commit/726fb5685b34d696e94778272db3b371c84c01ee). [Release notes](docs/RELEASE-v0.1.0-alpha.9.md).

## v0.1.0-alpha.8 — 2026-11-14

Changes since alpha.6 (there was no published alpha.7): Premiere bridge and sealed plugin package/install/update tooling; production transcript adapter, exact-tag Search Bins, source metadata/markers, previews and selects. Editor-owned changes surface conflicts instead of silent replacement.

- Adds separate asset tags/autocomplete, resizable folder navigation and one global semantic/CLIP search using the existing Orama architecture.
- Adds additive unified People, face/voice review and cross-recording evidence, with visible/heard distinctions and preserved legacy records. Adds AV validation and private editorial-plan helpers.
- Adds read-only static export and browser-compatible prebuilt search vectors; optional private S3/CloudFront publishing is a separately requested operation, not installation behavior.
- Included an optional backup rotation implementation inherited from the withdrawn draft; that implementation is retired/removed by alpha.9. Preserve any existing backups and records; do not run its removed commands after updating.
- **Update impact:** new durable tag/Premiere/People metadata. People migration creates a SQLite backup/receipt and verifies legacy evidence preservation; `unified_people: 1` capability checks block older incompatible writers. Code rollback does not reverse data migrations or native Premiere changes. No model-lock/requirements/ingest-engine change relative to alpha.6; these additions are not a blanket request to rerun ASR/VLM. Inspect the actual reindex plan, and perform explicit migration/maintenance where needed.
- **Validation/limits:** automated contracts and failure tests plus actual Premiere 26.2.2 trials on one development Mac and private static delivery checks. No fresh field-laptop or physical CCC certification. Full-suite publication evidence is in the GitHub release; later current-source validation counts must not be attributed backward.
- Source: [dac937d](https://github.com/mattb/field-lab-deployed/commit/dac937d), [64f8514](https://github.com/mattb/field-lab-deployed/commit/64f8514adcd77c3bc39258d8a2d27661cf155fd0) (tag target; child-process contract fixture portability). [Detailed notes](docs/RELEASE-v0.1.0-alpha.8.md).

## v0.1.0-alpha.7 — withdrawn draft, not a published release

The initial backup rotation work was committed in [066f8ae](https://github.com/mattb/field-lab-deployed/commit/066f8ae). [ece0323](https://github.com/mattb/field-lab-deployed/commit/ece0323) marked it unreleased and restored the alpha.6 install pin. There is no surviving alpha.7 remote tag or published release in the audited inventory. Its code subsequently shipped within alpha.8 and was removed in alpha.9. Do not infer a field installation or acceptance from the draft's tests. Preserve existing user data and task configuration.

## v0.1.0-alpha.6 — 2026-11-07

Changes since alpha.5: documentation for optional MTP camera → MacDroid → OffShoot folder-source ingestion, with companion-file preservation, destination identity checks, receipt gating and adapter-cache verification limits.

- **Update impact:** documentation-only; no application code, model lock, analysis profile, project schema, hooks or CCC/OffShoot setting change. No reindex. MacDroid is optional and separately installed/licensed; never install/change it merely as a software update.
- **Validation/limits:** development observation established folder acceptance/enumeration, not a full verified camera transfer. Physical transfer/interruption tests and device-specific compatibility remain required.
- Source/tag target: [9be61c4](https://github.com/mattb/field-lab-deployed/commit/9be61c4198bc8c75afbde5ae1533a0ab2fc41bd8). [Detailed notes](docs/RELEASE-v0.1.0-alpha.6.md).

## v0.1.0-alpha.5 — 2026-11-07

Changes since alpha.4: conservative automatic face grouping policy, face-only backfill/regroup maintenance on saved stills and preservation of operator matching feedback/identity constraints. Deployment exposes planning and explicit application rather than clearing the project.

- **Update impact:** do not reset People/face evidence or rerun all audio/captions for a requested face-only repair. Follow [face maintenance](docs/FACE-TUNING.md), review the actual plan and retain backups/legacy IDs. No weights or SQLite schema changed, but detector implementation changes conservatively affect the whole-analysis profile. Face-only maintenance is an explicit alternative on existing saved stills; normal installation does not silently run it. Model/readiness and real recognition quality are separate checks.
- Source: [b61b168](https://github.com/mattb/field-lab-deployed/commit/b61b168), [bdd56e8](https://github.com/mattb/field-lab-deployed/commit/bdd56e846797af0d4f0f571219b5d54aebe948ef) (tag target; portable policy fixtures retain the production SQLite gate).

## v0.1.0-alpha.4 — 2026-11-06

Changes since alpha.3: preserve per-file analysis failure causes through retries/reconciliation, bound scratch cleanup races without hiding the original error, and explicitly apply operator-confirmed clip face labels atomically. Portable release source identity excludes Git worktree control files.

- **Update impact:** retain old analysis objects and operator metadata; no project schema migration was claimed. The changed analysis implementation creates a new analysis profile despite unchanged weights/prompts; inspect the registered-scope reindex plan before restarting work. Never clear an index or restore old operator state as an update workaround.
- Source: [5edce72](https://github.com/mattb/field-lab-deployed/commit/5edce72), [e605241](https://github.com/mattb/field-lab-deployed/commit/e605241), [9ebf67d](https://github.com/mattb/field-lab-deployed/commit/9ebf67d653cf5fd0359cf622901b8365453f13a4) (tag target). [Recovery upgrade guidance](docs/UPGRADES.md#recovery-hardening-in-alpha4).

## v0.1.0-alpha.3 — 2026-11-06

Changes since alpha.2: retry/reconcile transient project publication contention rather than dropping ingest work; keep the service/status useful while another process holds the project lock. Prolonged lock contention returns work to pending; other failures remain visible.

- **Update impact:** retain queue, completed analysis and operator data. Resume/reconcile pending work; do not reset jobs or kill unrelated holders to bypass locking. No model/analysis recipe or project schema changed; no reindex was required for alpha.3.
- Source/tag target: [7d122fa](https://github.com/mattb/field-lab-deployed/commit/7d122fa8e6729db1793e3ee8cb9abf75a1ff296a).

## v0.1.0-alpha.2 — 2026-11-06

Changes since alpha.1: split fast ingest and large local agentic-chat model roles. Qwen3.5-4B remains the ingest VLM; Qwen3.8-27B is the chat/tool/image-inspection role. Adds pinned 27B assets, separate readiness/identity checks and owned-server lifecycle behavior. Reindex planning can run read-only while the service is active; execution still requires a clean stop.

- **Update impact:** new large model download/storage and recipient memory/performance acceptance; weights stay outside Git. Chat-role changes are excluded from ingest invalidation; model/dependency/analysis changes from any older baseline still need the actual reindex plan. Preserve shared model generations and existing metadata. Do not terminate an operator-owned unrelated model server.
- Source/tag target: [6b3e837](https://github.com/mattb/field-lab-deployed/commit/6b3e83786f7dcf2caed94f2a1490a7a93162cb0a). [Model roles](docs/MODELS.md).

## v0.1.0-alpha.1 — 2026-11-05

Initial published deployment: local media indexing/search/chat and persistent operator metadata, pinned offline assets, OffShoot verified-ingest receipt gate, durable queue/reconciliation, isolated source releases and candidate checks, machine-specific configuration and Codex install/update skill.

Before the first tag, follow-up commits added OffShoot readback and complete-card settings checks, bounded shutdown/profile handling and upgrade playtests, portable Swift source inclusion, native Desktop actions, clean install paths with spaces, APFS volume identity resolution and the pinned install prompt.

- **Update impact:** first setup downloads dependencies/models and requires selected physical destination, offline readiness, disposable own-media checks and actual OffShoot acceptance. No developer datasets/configuration ship. Original media, shared state and project metadata are distinct from executable releases; rollback is code selection, not restoration of old operator data. CCC setup is separate in this historical release.
- Source: initial [6362d11](https://github.com/mattb/field-lab-deployed/commit/6362d11), then eddfbc8, 9149f47, befee92, 0649b32, 526104b, through tag target [c6f4891](https://github.com/mattb/field-lab-deployed/commit/c6f4891623175be046f40ae2182d871f59748f27).
