# Field Lab for Premiere

Development prototype for Premiere 25.6 or later. The installed-host proof is maintained in the repository's Premiere demo report; fake-host tests are not evidence that a particular Premiere build supports an operation.

The panel projects a paired Field Lab project's assets into native editable objects:

- `Field Lab / Media` retains the relative ingest folder hierarchy.
- `Field Lab / Tags` holds native Search Bins using exact stable tag identity tokens, independently of readable tag spelling. Bin membership is read back before synchronization can be acknowledged as saved.
- Human timed notes and concise visual-outline ranges become source clip comment markers. AI marker names start with `AI:`; comments distinguish AI visual notes from human notes.
- Whole-asset notes, tags and approved visible people and separately approved speakers become project-private metadata fields. **Details → Show metadata columns** appends Tags, Notes, Visible people and Speakers to the existing column layout without replacing its other entries.
- Authored or genuinely aligned timed word JSON can become native transcripts. Sentence-only text never receives fabricated word timings.

Web results can request an exact source reveal or a new selects sequence through the same typed queue. Hosts with native Source Monitor seek use it; 26.2 uses a full-context preview sequence at the requested source time. Selects trim generated audio/video instances without altering source master In/Out. Generated outputs are versioned, saved, ownership-marked and read back; an editor-modified output is preserved and reported as a conflict on replay.

All code and UI assets are local. The plugin does not invoke an agent, a model or a cloud service. It does not call the source-XMP setter. Premiere's own media preferences still need to be checked and original hashes verified: importing clips or adding markers can otherwise cause Premiere itself to write XMP into media.

## Load for development

1. Enable **Settings → Plugins → Enable developer mode** in Premiere and restart the app.
2. In Adobe UXP Developer Tools 2.2 or later, add this directory's `manifest.json` and load the plugin. While manually exercising workflows, use **Load** rather than **Load & Watch**: automatic reload can dismiss an open native file picker.
3. Open the disposable paired Premiere project. Show the panel from **Window → UXP Plugins → Field Lab** if needed.
4. For the local journal transport, choose the Field Lab project root with **Choose Field Lab project**. The panel derives the internal bridge directory itself. This uses supported UXP permission-scoped persistent folder access. Existing development bridge-folder permissions remain usable without silently expanding access.
5. Use **Start sync** for background updates; **Pause** stops polling after any active native operation finishes. A paired active project is required; another open project causes a wait, not a failed queue. **Details** contains **Sync now** for a single manual attempt and shows readback or the precise failing native action. A saved revision is acknowledged only after native readback and Premiere's `save()` succeeds.

The optional HTTP controls are an experimental transport. Adobe documents macOS restrictions on insecure HTTP; an allow-listed port alone does not establish that it works. Do not disable system protections or certificate checking to make it work. The journal is the fallback. Its atomic replacement receipts are consumed into the same durable Field Lab SQLite queue; it is not a second independent metadata store.

This is a developer-loaded prototype, not a signed/notarized production installer or a published release.

## Ownership and recovery

Pairing checks the expected native path, Field Lab project identity, source path containment, and the native project GUID from previous saved readback. It never executes code found in metadata. Asset IDs and last applied values are stored in native project metadata on the imported clips. These survive a saved project and allow a new plugin session to recover ownership without browser storage.

An initial empty bin named `Field Lab` may be claimed on first pairing. A populated unowned bin requires review. Local plugin storage also remembers the pairing during the initial import. Source imports inside the owned Media tree can be adopted after an interruption only by an unambiguous exact path; ownership IDs take precedence thereafter. An owned source removed or moved out of the managed tree after a saved baseline is a conflict, not a reason to import another copy.

Owned marker comments carry a stable provenance suffix. Marker edits/deletions, field changes and transcript corrections in Premiere block an incompatible refresh. Editor-owned objects are preserved. A failed save reports **applied**, not **saved**. Retrying reads native state before changing it, so a lost acknowledgement does not duplicate clips, markers or transcript imports.

The current conflict path preserves changes and exposes them for review; it is not yet a complete bidirectional conflict-resolution UI. Native transcript import, schema fields and Search Bin behavior must be verified in the actual host build, including save/reopen.

## Implementation contracts

Custom schema properties use the exact registered names (`FieldLabTags`, `FieldLabAssetId`, etc.) in the `premierePrivateProjectMetaData` namespace. `Column.*` prefixes describe built-in properties and must not be prepended to these custom names. Metadata APIs and import target bins receive explicit `ProjectItem.cast(...)` conversions at SDK boundaries. Marker, transcript and metadata actions are constructed under the native project lock.

Transport consumes the versioned `request.json` envelope emitted by `ditnotes.premiere_bridge`. Receipts include operation/hash/project/path identity, a unique attempt ID, readback and the honest status `saved`, `applied`, `conflict` or `error`. Native project files are serialized by Premiere, never rewritten as private XML by the bridge.

Run contract tests with:

```sh
node --test premiere-plugin/tests/*.test.cjs
```

Official references: [UXP setup](https://developer.adobe.com/premiere-pro/uxp/plugins/), [ProjectItem casts](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectitem), [Metadata](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/metadata), [Markers](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/markers), [Transcript](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transcript), [network restrictions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/), and Adobe's [custom-field reference](https://github.com/Adobe-CEP/Samples/blob/master/PProPanel/jsx/PPRO/Premiere.jsx).

## Updating a loaded plugin

During native testing, UDT **Reload** was observed to retain an older required CommonJS module even after the entry script changed. Use **Unload**, then **Load** (or restart the host at a quiet moment) for a complete code update. Do not update a plugin while it is applying a native operation. **Details → Check Premiere** and every receipt include independent `main`, `native` and `commands` source build IDs; compare them with `python3 premiere-plugin/scripts/stamp-build.py` output before attributing a test result to a new build.

The ownership manifest stores standard SHA-256 transcript fingerprints, not duplicate word arrays. The bundled MIT `js-sha256` implementation works without Node crypto in UXP; its pinned source/license/integrity information is in `vendor/README.md`. Full native metadata ownership values are read back before an acknowledgement can claim a save. The contract suite includes a synthetic 20-minute/6,000-word serialization case; that is not a 20-minute native-host performance benchmark.

Generated selects trim source In/Out, read back the resulting timeline positions, then use native whole-item move actions to place those trimmed instances. Native Start/End setters trim source content and are not a substitute for moving an item. A newly created output receives a `building` ownership record and initial layout before trims; each phase checkpoints its actual layout. A retry reuses an unchanged known partial output, or one that exactly matches the same operation's explicitly retried failed readback. An unexpectedly changed partial output is preserved for review. Completed outputs carry their exact source/timeline layout and are never silently rebuilt after editorial changes.

**Details → Audit Premiere project** is an explicit read-only diagnostic. With the paired project active, it captures every native sequence's name/GUID and audio/video source/timeline ranges, plus the source master In/Out values exposed by the host API. Unsupported reads are reported as unavailable. It atomically replaces `audit.json` in the approved bridge folder; it neither edits nor saves Premiere and does not run on each background poll. This supports before/after editorial-preservation checks without relying only on a screenshot.

Native transcript readback now requires valid timed words. An expected transcript that becomes missing, empty, malformed or unreadable is a conflict, not a successful refresh and not automatic permission to restore a deleted transcript. A new import remains pending until native word/text/timing readback succeeds; a failed import cannot become a false Saved on its next attempt. Receipt transcript verification concerns native data, not Text-panel rendering. The read-only audit also records the current Source Monitor item identity/path/export alongside owned-source transcript summaries, so those two contexts can be compared when the Text panel is empty. On the tested 26.2.2 host, a stale Text panel after save/reopen showed No dialogue found despite valid native exports and a matching Source Monitor item. Closing just the focused Text panel and reopening Window → Text restored the imported transcript without data reimport; clicking a word then sought the original source at the authored time, within one video frame. This is a verified manual panel workaround, not an automatic repair guarantee or certification of other host versions.
