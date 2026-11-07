# Analysis upgrades and UI upgrades

A deployment release ID identifies the entire installed software tree. An **analysis profile** identifies the recipe that produced a clip's transcript, visual notes, face and voice evidence. The two IDs serve different purposes: a new release can reuse every completed analysis object.

| Change | Existing analysis | Search/UI |
| --- | --- | --- |
| `viewer/src/` React, CSS or Markdown rendering | Reuse | Build and deploy the new UI; reload the browser |
| Browser runtime, Node, text/CLIP search models or reranker | Reuse | Prepare assets and refresh the search index when needed |
| Chat-only Qwen3.8-27B weights, role pin or chat prompt | Reuse | Prepare the chat checkpoint and deploy; new chat turns use it |
| `ditnotes/prompts/field-caption.txt` or inference code in `engine.py` | New profile | Reanalyze selected registered scopes, then publish search |
| ASR/VLM/diarization/speaker/face model weights, configs or identity model manifests | New profile | Reanalyze selected registered scopes, then publish search |
| Python dependency lock, Python runtime or FFmpeg decoder source pin | New profile | Reanalyze selected registered scopes; this deliberately treats any Python lock change conservatively |
| Sampling options, such as maximum selected frames | New profile | Reanalyze the requested scopes with those options |
| File move or a second download of identical pinned weights | Reuse | Reconcile the file paths; retain content identities |

Managed releases use the pinned SHA256 values for inference models. Their modification times and model-generation directory names do not affect analysis reuse. Changing a browser-only asset can create a new model generation for deployment while retaining the same analysis profile. Bootstrap verifies the actual model files against those pins; the profile calculation uses those verified pins without rereading gigabytes of weights for every planning call. Do not manually change installed models or edit an active release. Legacy standalone prototypes without a deployment lock retain their older conservative mtime-based rule.

The entire Python dependency lock, pinned Python runtime and FFmpeg decoder source are included conservatively. Even a Python dependency used only by chat can therefore invalidate analysis when that lock changes. Node, browser, React and chat-only model-weight changes remain separate. The ingest profile explicitly includes its own model families and excludes the 27B chat checkpoint. When changing other inference behavior, the maintainer must update a tracked analysis input or explicitly version the profile. Keep release notes clear about whether an upgrade changes analysis, search, database compatibility or presentation.

## Maintainer workflow

1. Make the change in the source repository. Keep an analysis-prompt change and a UI-only change separate when testing their effects.
2. Run `tests/test_upgrade_profiles.py` and the deployment tests. For an inference change, also run a disposable real-model test and inspect the new result; fixture tests cannot establish caption quality or runtime performance.
3. Publish a reviewed commit/tag with compatibility notes, expected reanalysis scope and validation evidence. Ariel's Codex fetches that exact ref, prepares it and activates through the existing field-update process.
4. Preserve previous code, models and sealed analysis objects. Do not clear indexes or delete operator databases to force a change through.

The caption prompt lives at `ditnotes/prompts/field-caption.txt`. Changes to that file or `ditnotes/engine.py` change the analysis profile. A React edit under `viewer/src/` does not.

## Plan before reanalysis

Use the reviewed source checkout and the same `--home` used at installation. These examples use the default home. Choose the actual media project, not an arbitrary volume or a broad parent directory:

```sh
bash deploy/bootstrap.sh stop
bash deploy/bootstrap.sh reindex --project "/Volumes/YOUR DRIVE/Ingests"
```

The default `reindex` command is a read-only plan. It shows the analysis profile, registered scopes and each file's `analyze` or `reuse` action. It refuses projects with no registered ingest scopes. It does not authorize unknown folders or fabricate OffShoot verification evidence. The plan serializes against release changes and can run while the service is active. Execution requires the AI service to be stopped.

A UI-only upgrade should show `analyze: 0` for clips that already have valid completed objects under the same analysis options. Pending clips, missing/corrupt objects or earlier analysis changes can still produce work; inspect the rows instead of assuming every pending item was caused by the UI change. A prompt upgrade should show the affected clips as `analyze` under a new profile.

Stop after the plan unless the operator or release instructions have requested that reanalysis. Do not reanalyze all footage merely as an update smoke test. For a planned prompt upgrade, the operator can tell Codex:

```text
Apply RELEASE using docs/prompts/FIELD-UPDATE.md. This release changes the caption prompt. Plan and then reanalyze the registered scopes of PROJECT after showing their file counts. Preserve operator notes, person names, face/voice links and previous AI objects. Resume remaining work safely after interruption; leave OffShoot and CCC settings unchanged.
```

## Run and resume

When OffShoot has no active or queued transfer, and the scope has been selected for reanalysis:

```sh
bash deploy/bootstrap.sh reindex --project "/Volumes/YOUR DRIVE/Ingests" --run --offshoot-idle
```

`--offshoot-idle` records an actual check in OffShoot. It is not an automatic transfer detector. Keep the AI service stopped during this command. The command takes a SQLite backup of operator metadata, processes the project's existing registered scopes, and republishes the search index. It never overwrites or reorganizes original footage.

Completed objects are stored by source content ID and analysis profile. After interruption, rerun the plan and the same run command: complete objects for the new profile are reused; incomplete work is retried. Old-profile objects remain available on disk. Stable project and asset IDs keep annotations connected to their original clips. Names, face/voice links and operator notes remain in `project.sqlite3`; regenerated AI output does not replace that database.

If only search assets changed, an explicit `reindex --run` can refresh the catalog/search projection while reusing every sealed analysis object. ASR/VLM inference is created lazily, so a full-reuse run does not load those models. It can still load local embedding/search models to republish search. A pure React presentation update needs only deployment and browser reload when the existing index remains compatible.

After a successful run, inspect a new caption and an existing operator note/name in the browser, check remaining work, and start the normal ingest service again:

```sh
bash deploy/bootstrap.sh doctor
bash deploy/bootstrap.sh start
```

Normal service startup also reconciles verified ingest work against the active profile. Plan an analysis-changing upgrade before starting it; a new profile can create a backlog even without an explicit manual run.

## Validation boundary

`tests/test_upgrade_profiles.py` uses disposable three-clip fixtures and a fake inference engine. It proves cache invalidation/reuse, new-profile interruption/resume, unchanged original hashes, stable IDs and preservation of real SQLite operator names, voice links and notes. It also verifies a search refresh with zero new analysis does not construct the inference engine. Real-model upgrade evidence belongs in the release's validation report; these fixtures do not prove footage-specific recognition quality or a fresh field laptop installation.

Code rollback selects an older executable release; it does not reverse database migrations or erase new operator edits. Review compatibility and backups before rolling back. Reanalysis preserves old AI objects, but that alone does not make every future database migration reversible.

## Recovery hardening in alpha.4

Owned analysis scratch now tolerates bounded macOS directory cleanup races. Persistent cleanup errors still fail the job, and cleanup cannot replace the original analysis error. If analysis was already sealed, retry reuses it. Failure history and per-file last causes are checkpointed atomically and survive reconciliation, retries, renames and later success. Queue/status summaries include the relevant cause with external paths redacted. These changes require no database schema migration and preserve operator metadata.

The engine source change conservatively creates a new analysis profile even though model weights and prompts are unchanged. Inspect the reindex plan before processing an existing project with this release; retain old sealed results and all operator metadata. Do not remove an existing index to update.

The explicit clip-face labeling helper accepts operator-confirmed content-ID/name mappings and applies them in one SQLite transaction. It does not infer names from filenames, assign voice names or bypass different-person, simultaneous-face or voice-link conflict guards. Projects and identity labels remain outside the repository.
