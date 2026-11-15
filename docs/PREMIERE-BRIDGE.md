# Premiere bridge prototype

Field Lab's web UI can turn asset tags, notes, AI outlines and validated timed-word transcripts into native Premiere objects, and send source reveals or selected ranges into Premiere. The bridge runs as local deterministic code. It needs neither Codex nor inference to synchronize already-indexed material.

This is a development prototype. It is not included in a published release or a proven fresh-laptop installer. Do not replace an existing production project or alter OffShoot/CCC configuration to try it.

## Native proof so far

On Premiere **26.2.2**, a disposable authored four-clip project demonstrated:

- Native Media bins retaining shoot/camera/card folders.
- Native Search Bins with exact tag membership, including exclusion of `Sea ice cream` from `Sea ice` and one source in multiple tag bins.
- Project-private tag/note metadata, clip range markers and native transcript import.
- A web result opening a full-context preview sequence at the requested source time.
- A native six-second selects sequence with both video and audio using source ranges 1–4 seconds and 2–5 seconds, rather than rendered replacement media.
- Native readback followed by a real `.prproj` save. Original synthetic media hashes remained unchanged.

Screenshots, readback, save/reopen and editorial-preservation checks remain separate acceptance evidence. The [scenario checklist](PREMIERE-DEMO-SCENARIOS.md) defines the full test. Fake-host tests alone do not establish Premiere support. Metadata column display, native edits/conflicts and additional recovery cases should be recorded against the tested build, not inferred from API documentation.

**Verified Text-panel workaround on 26.2.2:** after save/reopen, a stale Text panel showed “No dialogue found” even though native exports and the Source Monitor item identity were correct. Closing just the focused Text panel and reopening **Window → Text** restored the native Morgan transcript without reimporting data. Clicking “changing” then sought the original Source Monitor to `00:00:03:06`, within one 24 fps frame of its authored 3.267-second word start. This proves the imported transcript is usable for source word navigation on this tested host. It does not certify automatic UI repair on every session or Premiere version. If the symptom returns, verify the Source Monitor context and recreate the panel; do not clear or reimport valid transcripts as a display workaround.

Premiere 26.2 has no supported UXP Source Monitor seek. Its **Open preview in Premiere** fallback uses a native full-context preview sequence. A 26.3+ host may use Source Monitor seek after a capability/version check; that newer route has not been tested here.

## Try the disposable demo

Use the prepared Field Lab Python environment and local ffmpeg. The generator refuses to replace an existing destination:

```sh
python development/premiere_demo.py '/path/to/new/Synthetic Project' --ffmpeg '/path/to/ffmpeg'
python development/serve_premiere_demo.py '/path/to/new/Synthetic Project' --port 18798
```

The generator uses schematic images, fictional labels and authored local audio/timings. It does not measure recognition accuracy or perform ASR. The server loads no inference models and refuses a non-synthetic project. Open the printed local web URL. Create a new Premiere project named `Field Lab Demo.prproj` inside that synthetic project folder.

Load `premiere-plugin/manifest.json` with Adobe UXP Developer Tools 2.2+ after enabling Premiere's developer mode and restarting. In the Field Lab panel:

1. **Choose Field Lab project** and select the project root once.
2. **Start sync**. The panel derives its internal bridge folder and remembers the permission.
3. Keep the paired Premiere project open. **Pause** stops polling after an active native operation finishes.

The ordinary panel names the Field Lab project and active Premiere project, shows whether automatic sync is on, and reports saved revisions/counts or a queue/conflict. **Details** holds diagnostics, column setup, a single manual sync and a read-only native audit. No inner-folder navigation or token is required in the normal workflow.

The working macOS transport is a permission-scoped directory journal under `_DIT_NOTES/editorial/bridge`. The web UI talks to the existing local Field Lab API; the plugin reads that API's atomic request projection and writes atomic receipts. SQLite remains authoritative. Experimental HTTP controls are tucked into Details: Adobe's macOS runtime blocked insecure HTTP in this trial, so do not disable security protections to enable them.

## Saved, conflicted and cancelled

Field Lab commits desired operations before publishing requests. A plugin acknowledgement distinguishes **applied but not saved**, **saved**, **conflict** and **error**. Pending changes survive Premiere being closed. A new application session reads the existing queue/native ownership instead of resetting the database or rerunning AI.

The plugin bounds writes to its paired project and owned objects. Source project metadata stores ownership IDs, last applied values and standard SHA-256 transcript fingerprints; full transcript copies are not stuffed into ownership fields. Native objects are read back before a saved acknowledgement. Human and AI marker provenance is explicit; visible people and speakers have separate fields.

Premiere edits and deletions are preserved and may block a conflicting refresh. **Retry after review** is not permission to overwrite an unrelated editorial change. The current conservative conflict path is not a complete bidirectional resolver. A failed selects/reveal request can be explicitly cancelled: **Cancelled; Premiere changes kept** means any native partial output is retained, not rolled back or falsely marked saved. A new request receives a new operation/version. Completed selects are never rebuilt over an editor-modified sequence.

Keep original media immutable. Premiere's own XMP-writing preferences must be checked and source hashes verified: using project-private metadata does not automatically stop the host from writing import IDs or clip markers into files.

## Updates and diagnostics

Do not update a loaded plugin during an active operation. During testing, UDT **Reload** retained an older required module. Use **Unload → Load**, or a quiet host restart, then verify independent module build IDs through **Details → Check Premiere**. Every receipt includes those IDs. Source build IDs are generated with:

```sh
python3 premiere-plugin/scripts/stamp-build.py
node --test premiere-plugin/tests/*.test.cjs
```

**Audit Premiere project** writes a read-only `audit.json` through atomic replacement in the approved bridge folder. Compare sequence GUIDs and source/timeline ranges before and after synchronization, including editor-owned control sequences. Unsupported master In/Out reads are labelled unavailable. The audit does not save or edit Premiere and does not run on every poll.

Native projection version 2 forces the current metadata mapping to refresh without rerunning ASR or VLM. A later mapping change must deliberately bump that version and ship a compatible adapter. Code rollback cannot undo subsequent operator edits or native project changes.

## Remaining production work

The [production transcript and packaging trial](PREMIERE-PRODUCTION-VALIDATION.md) converted real saved Parakeet word timing/diarization and verified 28 native transcripts. Sentence-only catalog text never acquires invented word timestamps. [CCX installation and update](PREMIERE-INSTALL.md) were exercised through Adobe's installer and live host on the development Mac. Full bidirectional metadata/transcript conflict resolution, independently validated proxies/multicam/color handling, and installation on another laptop remain unproven. The embedding graph remains in Field Lab; useful metadata/results are projected into Premiere.

See the [plugin reference](../premiere-plugin/README.md), [editorial proposal](research/PREMIERE-EDITORIAL-PROPOSAL.md), and [native API evidence](research/PREMIERE-BRIDGE-EVIDENCE.md) for implementation boundaries and source links.

The web header shows Premiere status at the top right: pending requests, the last confirmed save, and the last confirmed save. Idle checks are silent; activity appears only for known pending changes or errors. Queued work waits for the paired project and running Field Lab panel, which checks every two seconds. Preview completion explicitly tells the operator to switch to Premiere; browser requests do not foreground the app. Save confirmation times reflect receipt acknowledgement and may be later than the native save after an interruption.
