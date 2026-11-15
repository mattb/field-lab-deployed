# Premiere XMP evidence and synchronization proposal

Research only, 2026-11-12. No media, Premiere settings, projects or deployments were changed. This note distinguishes current Adobe documentation from design recommendations and unresolved acceptance tests.

## Principal finding

XMP is a useful exchange/projection format, but it is not a substitute for either a project database or a Premiere project controller. The safe starting architecture is **transactional Field Lab metadata → versioned XMP projection → an explicit Premiere bridge → project save**. Making every new AI result an embedded rewrite of verified originals would conflict with our immutable-ingest/content-hash/backup contract. External sibling sidecar recognition and live refresh must be measured by format and Premiere version, not assumed.

## Documented Premiere behavior

### File and clip metadata are different stores

Adobe distinguishes shared source-file metadata from per-instance clip metadata inside the project. Several subclips can reference one file while carrying different project descriptions. A file-level XMP change therefore cannot represent all subclip-specific editorial meanings independently. [Adobe: metadata in Premiere](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/metadata-in-premiere.html)

Adobe says editable XMP properties are written to source files; supported files usually contain the packet, and unsupported formats use a sidecar. That statement is not a guarantee that an arbitrarily created `clip.xmp` takes precedence over embedded metadata in every MP4, MOV or WAV importer. [Adobe: edit XMP metadata](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/edit-xmp-metadata.html), [Adobe: metadata overview](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/metadata-in-premiere.html)

**Engineering consequence:** archive originals remain unchanged by default. Field Lab can manage full-filename sidecars in its own namespace/output tree even where Premiere needs a bridge to apply their contents. A human-selected editorial-copy mode could later embed into independent APFS clones or derivatives after acceptance testing; it must never silently modify originals or hardlinked copies.

### Linking is not bulk historical reconciliation

Adobe documents that linking starts synchronization from that moment: values entered before linking are not copied. It also warns against linking when multiple clip instances reference the same file, because shared values can conflict. `Name` maps to Dublin Core `Title`; `Log Note` maps to Dynamic Media `Log Comment`. [Adobe: link clip data to XMP](https://helpx.adobe.com/in/premiere/desktop/organize-media/edit-metadata/link-clip-data-to-xmp-metadata.html)

The current Media preferences expose **Write XMP ID to files on import**, **Write clip markers to XMP**, and **Enable clip and XMP metadata linking**. Disabling marker writing stores clip markers in the project. The nearby growing-file refresh options concern growing media; they do not document general metadata refresh. [Adobe: Media preferences](https://helpx.adobe.com/premiere/desktop/get-started/preferences-and-settings/media-preferences.html)

**Proposed default for untouched originals:** ID writing off, marker writing off, automatic clip/file linking off until an explicit tested editorial-copy workflow requests it. Apply project fields/markers using the bridge. Do not link subclip descriptions to file descriptions. These are recommendations, not changes made during research.

### Custom properties need a display/search contract

Premiere supports custom schemas alongside built-in schemas through Metadata Display, with custom fields intended for production-specific information. This establishes custom schema support, but not that every property automatically appears in every Project-panel column or Search Bin criterion. [Adobe: create schemas and properties](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/create-metadata-schemas-and-properties.html)

Adobe’s open Custom Metadata Panel is a useful reusable reference: it has Premiere-specific clip fields and optional one-to-one clip/XMP linking. Its wiki documents Clip types as String, Integer, Real and Boolean. [Official project wiki](https://github.com/adobe-dmeservices/custom-metadata/wiki/Custom-Metadata-Panel-in-Premiere-Pro), [repository](https://github.com/adobe-dmeservices/custom-metadata)

**Proposal:** install a small versioned schema/display preset with the bridge. Use common properties for useful generic interchange and a short list of scalar Field Lab project fields for reliable editor columns. Keep structured evidence in our own namespace/database. Test Search Bin availability and update behavior separately; schema presence alone is insufficient evidence.

## Proposed property map

These mappings are a design proposal. `fieldlab` below denotes a project-owned namespace URI chosen and versioned before implementation; it is not an existing Adobe schema.

| Meaning | Interchange property | Policy |
|---|---|---|
| Short editorial title | `dc:title` | Human-accepted display title; keep original filename and asset ID separately. |
| Brief content description | `dc:description` | Human summary or explicitly approved AI summary; don't overwrite manual description with each model run. |
| Searchable topics | `dc:subject` | Keyword bag; stable facet vocabulary, not transcript paragraphs. |
| Human log note | `xmpDM:logComment` | Corresponds to Premiere Log Note when explicitly linked or bridge-mapped. |
| Keeper decision | `xmpDM:good` | Operator decision, never silently inferred from cosine score. |
| Production labels | `xmpDM:scene`, `shotName`, `shotNumber`, `shotLocation`, `shotDay` | Use only verified/operator-assigned values; do not turn a visual guess into recorded location. |
| Durable identity | `fieldlab:ProjectID`, `AssetID`, `OriginalSHA256` | Own IDs; never repurpose Adobe-managed identifiers as content hashes. |
| People | `fieldlab:PersonIDs`, `PersonNames` | IDs stable; names can change; separate visible-person evidence from speaker evidence. |
| AI result | `fieldlab:AISummary`, `ModelRevision`, `EvidenceRevision`, `Confidence` | Explicit provenance and independent from human fields. |
| Review status | `fieldlab:ReviewStatus`, `HumanReviewed`, `SyncRevision` | Small scalar project fields for columns/search; authoritative history remains in database. |

Dublin Core defines title/description as language alternatives and subject as an unordered array; preserve those types rather than flattening every property into a string. [Adobe: Dublin Core namespace](https://developer.adobe.com/xmp/docs/xmp-namespaces/dc/)

The Dynamic Media namespace defines log comments, the keeper Boolean and production shot/location fields, as well as marker tracks. It is suitable for these existing meanings; a custom namespace is preferable for model provenance and application-specific evidence. [Adobe: Dynamic Media namespace](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-dm/)

## Timed notes and transcripts

XMP has a documented `xmpDM:Tracks` structure; each named track holds an ordered marker list and a default frame rate. Marker properties include start/duration, name, comment, type, speaker and speech probability. [Adobe: Track](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-data-types/track/), [Adobe: Marker](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-data-types/marker/)

Times are **frame/tick counts with an associated rational frame rate**, not arbitrary floating seconds. The spec documents NTSC-style rational rates, millisecond speech timing and Premiere DVATicks. A bridge must preserve original timebase and distinguish file-relative time, source timecode and sequence time. [Adobe: FrameRate/FrameCount](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-data-types/)

**Proposed representation:**

- A sparse `Field Lab visual notes` track, one duration marker per meaningful visual interval, aligned to the original source—not one sentence per sampled still.
- A separate human-notes track whose edits have precedence and are never replaced by a model run.
- A complete transcript retained as timed utterances in the database and a standard text/subtitle export. XMP Speech markers can be an interchange experiment, but their presence is **not evidence** that current Premiere Text-Based Editing will accept an external Parakeet transcript as a native editable source transcript.
- Speaker IDs/names in speech evidence and visible person IDs/names in visual evidence remain distinct. Confirmed face/voice linking may enrich the projection but must retain provenance.
- Stable generated-marker IDs should be recorded in the custom namespace and bridge map. Do not match solely by marker text/time and then delete unrelated editor-created markers.

XMP markers alone do not create subclips, sequences, Search Bins, proxy relationships or multicam source sequences. These are project-level operations for the Premiere bridge. The separate native Transcript API does support external timed JSON import since 25.6; see [editorial evidence](PREMIERE-EDITORIAL-EVIDENCE.md) and the [consolidated proposal](PREMIERE-EDITORIAL-PROPOSAL.md).

## Read/write tooling and preservation

Adobe’s XMP Toolkit exposes packet parsing/manipulation, file-handler discovery, associated-resource discovery, writability queries, `CanPutXMP`, and a safe-update close option that writes a temporary file then swaps it. Its file-handler API is preferable to assuming one sidecar rule for every camera package. Safe update is a file-level operation; it does not supply our cross-file transaction, filesystem fsync, project lock, revision journal or merge policy. [Adobe SDK API source](https://github.com/adobe/XMP-Toolkit-SDK/blob/main/public/include/TXMPFiles.hpp), [SDK API overview](https://github.com/adobe/XMP-Toolkit-SDK/blob/main/docs/API/index.html)

ExifTool is useful for initial inspection and a prototype exporter. Its sidecar documentation distinguishes tag-copy operations from raw XMP block preservation; only the block route carries unknown/non-writable tags intact. Its QuickTime documentation warns that unqualified writes can create both QuickTime and XMP tags. Always choose explicit namespaces and use copies/staging. [ExifTool sidecars](https://github.com/exiftool/exiftool/blob/master/html/metafiles.html), [ExifTool QuickTime tags](https://github.com/exiftool/exiftool/blob/master/html/TagNames/QuickTime.html)

**Recommended implementation direction:** start with one pinned XMP library/CLI adapter and one packet model. Parse the complete existing packet, patch only owned fields, preserve unknown properties/qualifiers/arrays and all unowned marker tracks, serialize to a same-directory temporary file, read back and validate, fsync then atomic rename. Never rebuild another application's packet from a flattened JSON dump. Never globally replace `dc:subject` or `xmpDM:Tracks` without a three-way merge.

Current Premiere UXP already exposes Adobe XMP Core/Files through `require('uxp').xmp`, including `XMPMeta` and `XMPFile`. Reuse this in the host bridge rather than implementing RDF or adding a second native library. [Adobe: UXP XMP module](https://developer.adobe.com/premiere-pro/uxp/uxp-api/reference-js/modules/uxp/xmp/getting-started/xmp)

The Metadata API, documented since 25.6, has `addPropertyToProjectMetadataSchema`, `getProjectMetadata`, `getProjectColumnsMetadata` and `createSetProjectMetadataAction` with an `updatedFields` list. This is the targeted project-field path to prototype. It also exposes `getXMPMetadata`/`createSetXMPMetadataAction`; their brief “project XMP” descriptions do not establish the absence of source-file writes, so test side effects before enabling them against originals. [Adobe: UXP Metadata](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/metadata)

Do not confuse `EncoderManager.setSidecarXMPEnabled` with a universal importer sidecar preference: it is an encoding/export API. Since 26.3, Adobe requires Action creation inside `project.lockedAccess`; execute transactions there to coordinate with editor actions/Undo. [Adobe UXP changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)

The open Adobe Custom Metadata Panel provides reuse/examples, but adding its entire CEP application is not necessarily simpler than the narrow Premiere bridge. Evaluate its extension compatibility and current host API before adopting it wholesale. No extension was installed for this research.

## Synchronization recommendation

Keep SQLite as the transactional store for embeddings, cross-clip identity graphs, full transcript, evidence, audit history, tombstones, conflicts and work queues. XMP becomes a portable editable projection and an inbound annotation source. The web UI continues to edit the same transactionally stored data; a watcher imports external XMP changes and an outbox exports committed edits. This avoids treating two files as simultaneously authoritative without recovery machinery.

For each asset and owned property, record the last common value and revision. If only one side changed, apply that change; if both changed differently, retain both and present a conflict. Human values outrank regenerated AI suggestions but should not silently overwrite another human's concurrent edit. Keyword bags require per-item additions/removals or tombstones rather than a simple set union; otherwise deleted keywords reappear. Apply the same ownership discipline to generated vs human markers.

The Premiere bridge should acknowledge which revision it applied, read back project metadata/marker state, and request a project save only after a completed batch. SQLite/XMP/Premiere commits cannot be one distributed atomic transaction; durable outbox/inbox entries and idempotent reconciliation are required. Never write `.prproj` bytes on disk while Premiere has an unsaved in-memory project open. Project editing/saving should use the supported host API, leaving user-owned edit bins/sequences alone.

## Unresolved acceptance matrix

For actual camera MP4, QuickTime MOV, WAV/BWF, MXF/package and proxy files, test:

1. Embedded-only, sibling-sidecar-only, conflicting embedded+sidecar and read-only original cases; record exact naming convention and import route.
2. Metadata before import, changed while open, reopen project, reimport, and bridge refresh. No current primary documentation found here guarantees arbitrary hot sidecar refresh.
3. Human edit in Premiere → web UI → reopen Premiere; linked and unlinked clip/file fields; two subclips referencing the same source.
4. Unknown custom namespaces, array/language alternatives, unrelated editor markers, clip names/labels and camera timecode survive roundtrip.
5. Correct marker start/duration at 23.976/29.97 and variable-frame-rate material; proxy/source mappings; no transcript word loss.
6. Source SHA-256 remains unchanged in immutable-original mode; metadata/project save interruption leaves a recoverable prior revision.
7. Actual Project-panel fields and Search Bin criteria update after bridge acknowledgement, including boolean/keyword/people facets.

These tests, not the generic XMP standard, decide which projections can become field defaults.
