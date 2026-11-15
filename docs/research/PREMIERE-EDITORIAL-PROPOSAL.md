# Field Lab → Premiere: offline editorial bridge proposal

Research proposal, 2026-11-12. Three independent agents investigated XMP, native editorial organization and project automation, then reconciled API/version findings. No plugin, project, media or app settings were changed. Nothing in this research has been committed, pushed or published as a release.

## Recommendation

Build a small **Field Lab UXP plugin in Premiere** plus a narrow API in the existing local Field Lab Python service. This should turn ingest, AI evidence and operator decisions into useful native editing objects, not merely a pile of metadata fields. It runs offline without Codex, an agent, MCP, an API key, hosted inference or a remote service. Existing local Qwen/search/MLX processing supplies the optional natural-language retrieval; deterministic code performs synchronization and project mutations.

Keep the durable Field Lab database for evidence, identities, annotations, revisions and synchronization state. Publish/read **XMP as a portable editable exchange layer**, and use Premiere's actual APIs for transcripts, markers, bins, sequences and project saves. Do not replace the database with XMP or mistake an updated packet for an updated native project.

## What Ariel gets

Opening the shoot's Premiere project should show an already usable library:

```text
Shoot.prproj
  Field Lab/
    Media/                 # originals, arranged by shoot day/camera/card
    Search Bins/           # tested native metadata predicates
      Interviews
      B-roll
      Needs review
    People/                # approved people; speaking and visible kept distinct
    Selects/               # saved Field Lab searches as usable source ranges
    References/            # selected stills/look references only when useful
  Edits/                   # editor-owned sequences; bridge never rebuilds them
```

Each clip should expose a short useful description, people/tags/review state, a native transcript with speaker names, and a small number of meaningful range markers. A ten-minute shot with three major changes should have three visual-outline ranges, not sixty nearly identical markers. A web search or chat result should offer **Open in Premiere** or **Create selects sequence**. The editor can immediately browse, match-frame, trim and cut the returned original footage.

The plugin needs only connection/project status, pause/resume and any unresolved conflict/review controls. The web UI adds sync status and the native-output actions. No CSVs, repeated imports, Codex prompts or manual sidecar juggling in daily operation.

## Evidence: the useful capabilities already exist

- Adobe's UXP **Transcript API since 25.6** imports external timed transcript JSON and exports native transcripts. Adobe publishes the word/speaker schema and an import transaction example. This targets native Text-Based Editing; SRT alone is not that contract. [Transcript API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transcript), [Adobe transcript sample/schema](https://github.com/AdobeDocs/uxp-premiere-pro-samples/tree/main/sample-panels/premiere-api).
- **Clip range markers** can be created through UXP, and current Premiere help documents project-wide marker search/navigation. This is a natural home for human timed notes and concise scene outlines. [Markers API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/markers), [marker search](https://helpx.adobe.com/uk/premiere/desktop/organize-media/apply-labeling/find-move-and-delete-markers.html).
- **Search Bins are scriptable** through `FolderItem.createSmartBinAction`; exact search-query grammar and custom-property behavior still need a host proof. Column visibility is not proof of Search Bin support. [FolderItem](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/folderitem), [project templates](https://helpx.adobe.com/premiere/desktop/organize-media/create-projects/create-your-own-project-templates.html).
- Project-specific metadata setters, custom schemas and column controls exist. Adobe also exposes XMP parsing/manipulation in UXP, so the plugin need not implement its own RDF parser. [Metadata API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/metadata), [UXP XMP](https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/uxp/xmp/getting-started/xmp).
- Premiere's native project APIs create/import/save actual projects. The proposal uses those APIs, not a background rewrite of an open PRPROJ's internal XML. [Project API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/project).

These are documented capabilities, not a completed native-runtime test on this Mac.

## UI-to-native contract

| Field Lab capability | Proposed useful Premiere output | Boundary |
|---|---|---|
| Complete audio transcript | Native source transcript, actual timed words and approved speaker labels | Use stored raw ASR results, not just the UI's sentence list. Preserve editor corrections. Choose/map audio streams deliberately. |
| Concise visual outline | Named clip range markers; optional subclips | Keep meaningful chunks, provenance and uncertainty; no every-frame marker dump. |
| Human timed note | Editable clip comment/range marker | Roundtrip edits and deletions; do not recreate a deleted human note on every sync. |
| Whole-clip human note, tags | Human-owned metadata columns and portable XMP fields | AI summaries are separate fields, never replacements for human descriptions. |
| Approved face names | Visible-person metadata plus precise appearance ranges | Naming a face does not prove it is speaking; sampled frames do not prove continuous presence. |
| Approved voice names | Transcript speakers plus speaking-range markers/selects | Keep global identity IDs stable through renames. Clip-specific speaker rename is not automatically a global person rename. |
| Face/voice linking and grouping review | Native searchable results of confirmed decisions, review markers/bins for uncertainty | The full embedding graph/cosine review remains in Field Lab; Premiere has no documented equivalent. |
| Text/image/semantic search, chat retrieval | Generated result bins, selected source ranges and ordered selects sequences | Field Lab runs the query. Do not pretend Adobe Search Bins execute Orama/CLIP queries or import our vectors. |
| Favorite/keeper/reject decision | Tested review-status field; optional native Favorite and label if writable/proven | A label is one classification, not a replacement for independent tags. |
| Related clips / accepted inherited notes | Related-result bins/shared tags and explicitly attributed notes | Unaccepted suggestions remain suggestions. |
| Stills | Selected reference bin/stills, optional visual workspace | Do not flood the project with every sampled frame. |
| Shoot date, camera, roll/card, scene/take | Native source bins and tested metadata fields | Preserve original names, camera metadata and timecode. Camera ID and card/roll ID differ. |
| Proxy choice | Actual native Attach Proxies relationship | A proxy path in XMP alone is not attachment; verify duration, frame rate and audio layout. |
| Verified multi-camera synchronization | Actual multicamera source sequences | Only with real sync evidence; identity co-occurrence is not audio/timecode synchronization. |
| Camera settings / look history | Source facts, logging fields, reference markers/stills and validated color interpretation | A look note is not an automatically applied LUT or camera color-space measurement. |
| Offload/backup state | Read-only readiness/provenance metadata | Convenience display, never the underlying checksum proof or card-release authorization. |

For a query such as “Ariel and another person together,” intersect verified appearance intervals, not whole-clip person tags. A long clip containing each person at different times is not proof of a same-shot match. Native Search Bins are clip-level organizational tools; precise evidence-filtered queries should become ranges/selects.

## The local API bridge

```text
OffShoot → local AI + human annotations → Field Lab database/outbox
                                              ↕
                              versioned XMP exchange packets
                                              ↕
Web UI → Field Lab local API ← outbound authenticated plugin session
                                              ↓
                             Premiere native actions/readback
                                              ↓
                                 Premiere saves Shoot.prproj
```

**One implementation detail matters:** UXP can make WebSocket client connections but cannot host an accepting WebSocket server. Therefore the listening API lives in Field Lab's existing Python service. The plugin connects outward and receives typed native-operation requests. The user still gets one local API to Premiere. Adobe also flags macOS HTTP restrictions; loopback WebSocket/TLS/permissions must be the first native spike, not an assumed working transport. Use a supported, verified local transport, or a permission-scoped local journal fallback; never disable certificate validation or weaken system security. [Adobe network recipe](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/).

Initial bounded operations:

- `capabilities` / `projectInfo`: actual host version, writable project and supported methods.
- `syncBatch`: known asset IDs, changed fields, owned ranges and transcript revision.
- `readEdits`: imported human edits, marker deletions and transcript corrections.
- `revealAsset` / `revealResult`: open the known native item or source range.
- `createSelects`: known result IDs and validated ranges into a plugin-owned sequence.
- `save` / `syncStatus`: distinguish applied operations from successfully saved project state.

No `eval`, shell execution, arbitrary JS, unrestricted timeline-editing API or broad MCP mutation catalog. Validate the project identity, asset IDs, source bounds, ownership, protocol and operation revisions on both ends. Pair a project-scoped local token, bind only loopback, apply narrow network permissions and keep queued operations durable across disconnects. The local model may choose evidence through existing search tools; it does not issue arbitrary Premiere code.

Reuse direct Adobe UXP APIs, official samples and `require('uxp').xmp`. A general agent/MCP framework is unnecessary for deterministic sync. Pymiere is not the recommended foundation because its maintainer marks it unmaintained. Adobe's new official local MCP was only previewed at MAX, with private beta planned for early 2027; do not depend on it. Details and primary links are in [bridge evidence](PREMIERE-BRIDGE-EVIDENCE.md).

## Live XMP without breaking ingest

Premiere distinguishes shared file metadata from project-specific clip metadata. It commonly embeds XMP into supported source files; arbitrary adjacent `.xmp` files are not a documented universal override or live-refresh mechanism for MP4/MOV/WAV. [Adobe metadata model](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/metadata-in-premiere.html).

Default proposal:

1. Preserve original media bytes and the sealed date/card directory inventories. Store managed packets under `_DIT_NOTES/editorial/xmp/ASSET_ID.xmp`, not inside already sealed card folders. These are editable exchange packets, not an assertion that Premiere auto-discovers that location. The bridge explicitly applies their relevant fields.
2. Read existing embedded/sidecar camera metadata as input. Patch only owned properties in exchange packets and preserve unknown schemas, qualifiers, array types and unowned marker tracks using Adobe's SDK.
3. Map shared editorial facts to standard XMP where semantics match: title/description/keyword bag, human log comment, keeper flag and true scene/shot information. Add a small Field Lab namespace for asset/project IDs, distinct visible/speaking people, AI provenance and review/sync revisions. Keep vectors, raw face crops and the full audit/identity graph in their existing stores.
4. In Premiere use **project metadata** for displayed fields and native project markers/transcripts. Do not automatically enable linking, XMP ID writes or marker-to-source-XMP writes against immutable originals. Any preference change needs its own explicit configuration and copy-only acceptance test.
5. Watch external packet edits, import them through the same validation/history path as web annotations, and project committed native edits back into the shared field model/XMP. No whole-packet replacement that erases unknown or human data.

The existing backup rotation seal would reject newly added sibling files in a sealed card scope, and embedded writes would change whole-file content IDs/checksums. Keeping editable XMP inside the generated metadata area avoids both problems and puts it in the existing metadata snapshot scope. An optional later editable-media-copy mode could embed into independent APFS clones, with explicit identity mapping; it should not be the first field default.

## Ownership and interruption recovery

XMP, the database and PRPROJ cannot commit as one atomic transaction. Use a durable outbox and per-target readback/saved revisions:

1. Commit metadata changes, base values, provenance and operation ID in SQLite.
2. Publish each exchange packet with parse/validation, fsync and atomic replacement; record packet revision/hash.
3. The connected plugin checks the expected project and capability set, applies a bounded native transaction and reads back results.
4. Premiere saves the project; only then acknowledge the saved revision. Show **Queued**, **Applied, not saved**, **Saved**, or **Conflict** honestly.
5. On reconnect/crash, compare actual owned objects/revisions before replay. Operation ownership IDs must survive save/reopen; a native transaction/save/ack crash must not duplicate clips or markers. Establish that storage/readback mechanism in the first host proof rather than assuming marker text is a stable ID.

For shared human fields use a three-way merge against the last common revision. Human edits outrank refreshed AI suggestions, but simultaneous conflicting human edits are retained for review, not resolved by timestamp. Track keyword/marker deletions so they do not resurrect. Separate raw ASR from human-corrected transcripts. Preserve unowned markers and sequences. If an editor modifies a generated selects sequence, publish a new generated revision instead of rearranging the editor's work.

Start with one conventional project and a bounded Field Lab-managed bin; avoid forcing Productions complexity on a solo filmmaker. Productions can later separate generated source-library projects from editorial projects after cross-project propagation/locking is tested.

## Offline and project lifecycle

`prepare-offline` should also stage/install the plugin and all locally used assets, verify compatible APIs and transport, and perform a disconnected sample roundtrip. An installed/activated Premiere eligible to run offline is a prerequisite; ordinary Adobe licensing is distinct from Field Lab's no-cloud runtime. Package the plugin UI and code locally: no CDN scripts, hosted fonts, remote schemas or runtime dependency downloads. Optional Adobe language/Media Intelligence packs must already be installed if that optional feature needs them; our own search/ASR must not depend on Adobe downloading a model in the field.

When Premiere is running with the bridge connected, apply incremental batches and save the real project at quiet boundaries using Premiere's API. When Premiere is closed, Field Lab still analyzes, accepts annotations and writes XMP/outbox state offline; the PRPROJ remains the last saved revision. On next opening the project/plugin, catch up automatically. Do not claim a continuously current `.prproj` while its necessary native host is unavailable, and do not substitute unsupported background XML rewrites.

## Version-specific realities on this Mac

Read-only app inspection found Premiere **25.6.6** and **26.2.2** installed; no project was opened. Current SDK docs include newer methods. Transcript import, project metadata, clip markers, bin creation and native project save are documented since 25.6. `createSubClipAction` and direct Source Monitor positioning are documented since 26.3, so those cannot be assumed on the local 26.2.2 host. [ClipProjectItem](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/clipprojectitem), [SourceMonitor](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sourcemonitor).

Use range markers immediately; prove generated sequence insertion/per-instance trimming and sequence playhead navigation for exact-range selects on the older host. Native subclips/direct source seeking are capability-gated enhancements. No app upgrade is part of this proposal.

Repository inspection also found that `engine.py` preserves raw ASR sentence/token timing in saved result JSON while `library.py` flattens the browser catalog to sentence text/start/end. The native transcript adapter should consume that saved rich ASR data plus diarization, not regenerate timings from the browser's flattened text. Token-to-word conversion, real confidence availability and multiple audio stream mapping need validation before Adobe JSON export; never invent word times or speaker certainty.

## First proof, before implementation expands

Use a disposable owned clip/project and run entirely without internet or Codex:

1. Plugin connects locally, reports real capabilities/project ID and reconnects after service restart. Prove macOS transport/permissions first.
2. One human note → native range marker; one metadata field → visible column and genuinely matching Search Bin; save/reopen and hash original media unchanged.
3. Complete raw ASR → Adobe JSON → native transcript; verify Text-Based Editing, words, timing, speaker names, source offsets and export/readback.
4. One approved face/name → appearance range; one saved web query → a native selects sequence containing exact source ranges and linked A/V, with original handles/timecode preserved.
5. Change a note/transcript name in Premiere, sync to the web UI, then create a simultaneous edit conflict. Preserve both and all editor-owned objects.
6. Kill/restart around native transaction/save/ack; retry twice with no duplicate objects or lost human edits. Close Premiere, queue new work, reopen and distinguish last saved from caught-up revision.

Then add generated templates/people bins, more metadata facets, proxies, verified multicam and reference material. Agree success from visible editable Premiere objects and saved-project readback, not from an XML packet alone.

## Supporting evidence

- [XMP, fields, file-vs-project behavior and preservation](PREMIERE-XMP-EVIDENCE.md)
- [Full UI-to-editorial mapping and Premiere search surfaces](PREMIERE-EDITORIAL-EVIDENCE.md)
- [Native APIs, installed versions, local transport and reuse options](PREMIERE-BRIDGE-EVIDENCE.md)

## First-class asset tags

The web implementation now provides a prominent comma-delimited asset tag field with project-history autocomplete. [Asset tags](../ASSET-TAGS.md) defines the durable assignment/API and shared Orama projection. The bridge should use stable project/tag IDs for owned bin identity and exact asset membership, with names exposed as XMP keywords. One asset can belong to multiple tag bins; removing a tag removes only the managed membership. Keep whole-asset tags distinct from range notes and appearance evidence. This prepares the data contract; XMP publication and native bin synchronization are not implemented yet.
