# Install and update the Premiere bridge

The field laptop runs the installed Field Lab service plus the native **Field Lab** UXP plugin. No Codex session is needed for ordinary synchronization. Install the plugin separately from OffShoot/CCC configuration. Never package footage, `_DIT_NOTES`, operator names, machine configuration or model weights.

There are two distinct operations:

- The maintainer builds a **CCX installer using the pinned Adobe SDK packager or Adobe UXP Developer Tools**. This is a local build, not publication.
- The field laptop verifies that installer and uses Adobe's installed **Unified Plugin Installer Agent (UPIA)** to register/update it. UDT and developer mode are not needed for ordinary packaged-plugin use.

Adobe recommends UDT to create CCX rather than assembling the ZIP manually. This repository also reuses Adobe’s unmodified SDK packager, as described below; it does not implement a ZIP format. Independent distribution does not need Adobe Marketplace review or a package-level signature. Keep the stable independent-distribution ID `org.fieldlab.premiere.bridge`; a later Marketplace listing needs its own channel ID. [Adobe packaging guide](https://developer.adobe.com/uxp/guides/how-to/distribution/package/), [distribution overview](https://developer.adobe.com/uxp/guides/how-to/distribution/overview/).

## Maintainer: prepare and seal a package

Run from the reviewed checkout with a numeric version greater than the currently installed plugin. Use the same application home throughout; defaults below are portable.

```sh
bash deploy/bootstrap.sh premiere stage --version 0.1.1
```

This copies only an allowlist of runtime files and licenses into `shared/premiere/candidates/CANDIDATE_ID/plugin/`, changes only the staged manifest version, checks independently stamped module hashes, and seals every file. Preparation is resumable and does not change the loaded plugin or active app. The JSON response prints the exact staged `manifest` path and recommended `package_output` folder.

The repeatable build path uses Adobe’s pinned packager implementation:

```sh
bash deploy/bootstrap.sh premiere package --candidate CANDIDATE_ID
# Once npm dependencies are cached, this also works without registry access:
bash deploy/bootstrap.sh premiere package --candidate CANDIDATE_ID --offline
```

The maintainer-only `development/premiere-packager/package-lock.json` pins `@adobe/uxp-devtools-core` **1.2.0** and its dependency integrity hashes. `npm ci --ignore-scripts` avoids native helper setup. The small wrapper invokes the SDK’s unmodified `PluginPackageCommand.package()` method; all file selection, host manifest handling and archive creation remain Adobe code. This is the archive method used by the official CLI. We use it directly because the old CLI’s native helper does not boot on this arm64 host, and the current UDT app crashed when opening its Package menu. No developer security flag, native helper stub or host permission is enabled by this build.

This pinned SDK-internal entry point is a tested build dependency, not a promise of compatibility with arbitrary future SDK versions. The public [Adobe CLI source/readme](https://github.com/adobe-uxp/devtools-cli/blob/main/packages/uxp-devtools-cli/README.md) documents packaging; our separate strict source/permission/payload checks and actual UPIA/native test are mandatory. The build records its lock/wrapper/artifact hashes in `build-provenance.json`, then seals the installer. Already-sealed candidates are verified and reused rather than rebuilt with different ZIP timestamps.

Alternatively, in Adobe UXP Developer Tools, **Add Plugin** with the staged manifest, then **… → Package**, selecting a destination outside Git. It need not be loaded in Premiere. Attach the resulting file:

```sh
bash deploy/bootstrap.sh premiere attach-ccx --candidate CANDIDATE_ID --ccx '/path/from/Adobe/FieldLab.ccx'
```

`attach-ccx` inspects the archive without extracting it. It rejects traversal, symlinks, duplicate entries, oversized payloads, wrong host/ID/version, semantically modified manifests, modified runtime files and extra unsealed payload. Unsealed `META-INF` payload is not exempted. If a newer Adobe packager adds container metadata, inspect and explicitly support that format before accepting it. Adobe normalizes manifest JSON whitespace when packaging; its parsed contents must still exactly match the staged manifest. The verified CCX is copied durably into the candidate and receives a separate SHA-256 seal. A local checksum establishes content identity, not the authenticity of an arbitrary Git tag or downloaded artifact. Review/pin the source and obtain artifacts through the maintainer's trusted channel.

Keep the sealed `candidate.json`, its exact `plugin/` source, `package.json` and `plugin.ccx` together when transporting a candidate to a field machine's `shared/premiere/candidates/CANDIDATE_ID/`. Do not transport the maintainer's installer `state.json`, lock, config or runtime state. Offline install requires these local artifacts and installed Adobe Premiere/Creative Cloud/UPIA, not a model download.

## Field laptop: install or update

First install/activate the matching Field Lab application candidate through the normal [operations](OPERATIONS.md) commands. For a plugin-only update, leave the application and OffShoot/CCC configuration untouched.

1. Save and quit Premiere normally; unload a development copy in UDT if one is loaded. Do not force-quit an editing session. Pending Field Lab operations stay in the existing project journal.
2. Inspect `premiere status`. Install the reviewed, sealed candidate:

```sh
bash deploy/bootstrap.sh premiere status
bash deploy/bootstrap.sh premiere install --candidate CANDIDATE_ID --premiere-closed
```

The script also checks the live process list; the flag alone does not bypass a running host. It revalidates all source and CCX seals, commits an installation attempt before invoking Adobe UPIA, and records a confirmed installed version only when UPIA returns success and lists **Field Lab** at that version. It never writes Adobe plugin registries/preferences directly, disables security, changes privileges, uninstalls another plugin or replaces project data. [Adobe UPIA install instructions](https://helpx.adobe.com/creative-cloud/apps/integration-with-other-apps/manage-plugins/install-plugins-using-upia-tool.html).

3. Reopen Premiere and the intended `.prproj`. Open **Window → UXP Plugins → Field Lab**, choose the Field Lab project root if this is the first pairing, and start sync. Folder permission is a normal user-scoped UXP picker and is not copied from another Mac.
4. Queue a **new** small metadata update or preview from the web UI after installation. An initial snapshot queued by the background worker before installation cannot prove the new runtime; repeating its queue request may reuse that old operation. Prefer first pairing after installation, or let the initial snapshot finish and request a new preview. Capture that exact `operation_id` from its request/receipt; require a **saved** native receipt with the matching package ID, version and module hashes:

```sh
bash deploy/bootstrap.sh premiere verify-runtime --candidate CANDIDATE_ID \
  --receipt '/project/_DIT_NOTES/editorial/bridge/receipt.json' \
  --operation-id OPERATION_ID
```

Installation alone remains **runtime unverified** until this actual native evidence matches. Inspect native bins, source media, notes/transcripts and saved/reopened editor work. Do not call a fresh-laptop deployment proven by a mocked installer test.

Verification binds the actual project receipt to its saved database acknowledgement and post-install operation creation time. Idempotent host replay may change only the receipt envelope ID; all project, build, operation and readback evidence must match exactly.

## Interrupted install and recovery

`shared/premiere/state.json` records current/previous candidates and any unknown installer attempt using atomic replacement and fsync. It is separate from Field Lab project databases and model storage.

After a crash/timeout or nonzero Adobe result:

```sh
bash deploy/bootstrap.sh premiere reconcile
```

Reconciliation inventories Adobe registration; it does not blindly rerun an install or remove plugins. If Adobe lists the attempted version, it finishes the ledger commit and requires runtime verification. Otherwise it preserves the unknown attempt and diagnostics for review. Do not delete `state.json` to bypass this guard. Inspect Adobe's installer result before deciding on a manual retry/recovery; user data is not rolled back.

Updates must increment the plugin's three-part version. **Code rollback** packages the previous known-good source as another, higher plugin version, then follows the same install and native verification path. This avoids uninstalling the plugin or assuming that Adobe permits an in-place version downgrade. The previous artifact is retained; no old candidates are automatically deleted. A plugin rollback does not downgrade Field Lab schemas or overwrite newer annotations.

After a native metadata mapping change, the backend's deliberate projection-version bump reprojects metadata; it should not rerun ASR/VLM unless the analysis itself changed. On the tested Premiere 26.2 host, a stale Text panel may require closing just that panel and reopening **Window → Text**. Never erase/reimport a valid transcript solely to fix its display.

## Verification boundaries

The installer is local and deterministic. Adobe licensing/host installation remains a separate prerequisite; universal fresh-machine and physically disconnected-network behavior is not established by unit tests. The integration trial records actual Adobe SDK package creation, UPIA results, native build receipt, update behavior and preserved metadata separately. No GitHub release or Adobe Marketplace publication is implied by any command here.
