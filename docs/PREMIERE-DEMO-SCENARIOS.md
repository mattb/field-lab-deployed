# Premiere editorial acceptance scenarios

This is a synthetic, disposable test corpus. It includes no production datasets, real identities or operator metadata. The builder creates a new directory and refuses an existing destination. All names are fictional, and portraits are schematic drawings. Face/voice links are deliberately authored fixture labels, not evidence of recognition accuracy.

Build with the Field Lab Python environment:

```sh
python development/premiere_demo.py '/path/to/a/new/Synthetic Project' --ffmpeg '/path/to/ffmpeg'
```

macOS `say` generates each word separately. Recorded audio sample bounds become authored transcript word times, with a short pause between words. These are known fixture values, not ASR alignment or confidence measurements. `--silent` creates the same explicit authored transcript against silence for unattended tests. Nothing downloads models or uses an agent at runtime. Generated H.264/AAC media and JPEG graphics are created locally.

`EXPECTED-RESULTS.json` contains the exact expected names, tag membership, note ranges, transcript text, media hashes and identity labels for a reviewer. Reviewers should assess the visible Premiere workflow and saved/reopened results without reading the plugin or bridge implementation.

## First pass: an editor receives a useful project

1. Connect the local Field Lab bridge to a new native Premiere project.
2. Synchronize the synthetic project. Four playable clips should appear, retaining camera/card subfolders.
3. Inspect tag bins:

| Bin | Expected clips |
|---|---|
| Interview | Morgan interview.mp4 |
| Sea ice | Morgan interview.mp4; Casey sea ice field.mp4 |
| Wildlife | Penguin colony.mp4 |
| Sea ice cream | Sea ice cream.mp4 |

The `Sea ice` bin must exclude `Sea ice cream`. Morgan belongs to two bins, with no duplicate source import caused by repeated synchronization.

4. Open Morgan's native transcript. It should read **Sea ice tells a changing story.** and identify speaker **Morgan**. Playback should follow the authored word timing. Distinguish a native editable transcript from transcript text merely placed in a marker.
5. Inspect each clip's metadata, whole-clip note and timed human note. The exact text and ranges are in `EXPECTED-RESULTS.json`. Each clip has one meaningful visual-outline range, rather than a marker for every still.
6. Save, close and reopen the native project. Verify objects remain usable, and verify source SHA-256 hashes against the fixture manifest.

## Second pass: changes preserve editorial work

1. In Premiere, create an editor-owned bin named **My edit** and an editor-owned marker. Re-sync unchanged Field Lab data twice. Neither item should disappear; managed clips, bins and markers should not duplicate.
2. In Field Lab, remove **Sea ice** from Casey and add **Needs review**. Re-sync. Casey leaves only the Sea ice membership and enters Needs review. Its original media and other editorial references remain usable.
3. Change a managed note in Premiere. Re-sync unchanged Field Lab data. The editor's change must survive or appear as a clearly reported conflict; silent overwrite fails this test.
4. Change that same note in Field Lab. The workflow must retain both conflicting values for review, not choose the latest timestamp automatically.
5. Keep an editor-owned sequence open during synchronization. The bridge must not rearrange it.

## Third pass: offline and interruption recovery

1. With Premiere disconnected, edit tags and notes in Field Lab. Status must say queued, never saved-to-Premiere.
2. Reconnect and synchronize. Saved status should appear only after native save succeeds.
3. Interrupt around application/save/acknowledgement, then reconnect. The workflow must reconcile instead of duplicating native objects.
4. Repeat with internet disconnected. No cloud, model download or Codex step should be needed for deterministic sync.

## Review rubric

A code-blind reviewer receives screenshots, native readback, save/reopen results and the expected manifest. Ask:

- Can an editor locate the intended clip through ordinary Premiere concepts?
- Are tags, human notes, AI outline and voice/face labels distinguishable?
- Does the interface accurately distinguish queued, applied-unsaved, saved and conflicted work?
- Do negative examples remain excluded?
- Does a second sync preserve editorial changes and avoid duplicates?
- What manual step, confusing wording or missing native object would stop Ariel in the field?

Record the observed result and screenshot/readback evidence per scenario. A mock-host unit test does not count as Premiere proof. Mark unsupported host capabilities explicitly rather than representing fallback markers as native transcripts or ordinary bins as native Search Bins.
