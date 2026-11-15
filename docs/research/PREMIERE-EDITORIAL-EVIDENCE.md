# Premiere-native editorial surfaces for Field Lab

Research only, 2026-11-12. No media, Premiere settings, project, runtime or release was changed. The target is useful editing objects, not merely an XMP dump. All mappings below are proposals until exercised in the installed Premiere build.

## Findings that materially change the proposal

### 1. External transcripts have a native route now

Adobe's current UXP `Transcript` API documents `importFromJSON`, `createImportTextSegmentsAction` and `exportToJSON` since 25.6; `hasTranscript` is documented since 26.3. Adobe's sample panel implements the JSON import using project locked access and a transaction. Adobe publishes a transcript JSON schema requiring language, segments and speakers, with word-level timing/confidence support. This is a substantially better home for the complete Parakeet transcript than stuffing it into Description, XMP or thousands of markers. Sources: [Transcript API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transcript), [Adobe sample](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/src/transcript.ts), [Adobe JSON schema](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/assets/transcript_format_spec.json).

Adobe's August 2025 employee announcement gives the manual route: Source Monitor → Text/Transcript → Import → Import Static Transcript, choosing timed JSON. It explicitly connects this import to caption creation and Text-Based Editing, and began as a beta announcement. The current API reference/sample are stronger evidence that developers can implement it, but successful manual import and API readback/save/reopen still need acceptance in the exact installed release. [Adobe announcement and attached spec](https://community.adobe.com/announcements-732/now-in-beta-import-your-own-transcript-311910).

Do not confuse this with **Import corrected transcript (txt)**: Adobe's current help describes correcting text exported from an existing transcript and warns against deleting sections. That is not arbitrary untimed TXT import. [Corrected transcript workflow](https://helpx.adobe.com/premiere/desktop/edit-projects/edit-video-using-text-based-editing/edit-transcripts-using-text-based-editing.html).

**Proposal:** deterministic Parakeet-to-Adobe JSON conversion, preserving actual word timing and speaker IDs; do not have an LLM invent timings. Once a source transcript has been edited in Premiere, export/read it and merge under explicit ownership/conflict rules instead of replacing it whenever ASR reruns. Segment-only timestamps do not establish word-accurate text editing: if needed, word alignment is a distinct pipeline task. SRT remains useful for captions interchange but is not this transcript contract.

### 2. Timed annotations can be first-class searchable edit material

Current April 2026 Adobe help documents project-wide Marker search by name, comment or color. Double-clicking a result seeks its source/timeline location; range-marker results seek the range start. This directly fits concise scene chunks, human timed notes, confirmed person appearances and editorial selects. [Find, move and delete markers](https://helpx.adobe.com/uk/premiere/desktop/organize-media/apply-labeling/find-move-and-delete-markers.html).

The UXP Markers API supports markers on a sequence or clip project item and addition with name, type, start, duration and comments. Therefore bridge-written clip range markers have a documented in-app route; they need not depend on arbitrary XMP marker ingestion. [Markers API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/markers).

Adobe's February 2026 announcement for build 26.2.0.18 additionally describes dragging marker results directly into a sequence and notes a then-outstanding nonzero-start issue. Treat dragging/results-to-edit behavior as an explicit acceptance check, not a universal guarantee inferred from an old beta post. The current help page confirms search/seek, not every beta interaction. [Adobe marker-search announcement](https://community.adobe.com/announcements-732/search-markers-across-your-entire-project-1549479).

**Proposal:** three major visual chunks become three meaningful range markers, not a marker every sampled frame. Human notes remain separate from AI guesses. Do not put every transcript word into marker comments. Native clip markers remain editable by the editor, so import their changes back or flag conflicts; refresh only bridge-owned objects.

### 3. Search Bins are useful, but are not semantic search

Current Adobe project-template documentation describes dynamic Search Bins and two metadata criteria. It recommends useful predefined bins such as Favorites (Boolean), offline status, sequences, interview/B-roll and shot-type text. A template can preserve bins, sequences, labels and these searches across projects. [Project templates](https://helpx.adobe.com/premiere/desktop/organize-media/create-projects/create-your-own-project-templates.html).

Adobe's 2015 reference documents automatic updates after metadata changes/import/deletion and alias membership rather than duplicated media. It also records a significant field caveat: a single-property search is generally clip-metadata only, with Name, Tape Name, Description, Comment, Log Note, Scene, Shot and Client exceptions spanning clip/file metadata. This is old documentation and must be tested in the current host; it is reason not to assume every custom XMP field becomes a searchable Search Bin predicate. [Archived Adobe reference, Search Bins](https://helpx.adobe.com/archive/en/premiere-pro/cc/2015/premiere_pro_reference.pdf).

Current List View documentation says chosen XMP fields can be columns, properties can be added to a schema, and column choices are saved in the project. **Column visibility alone does not prove custom-field Search Bin support.** [Metadata columns](https://helpx.adobe.com/premiere/desktop/get-started/customize-the-project-panel/customize-list-view-in-project-panel.html).

**Proposal:** establish a small current-host-tested searchable scalar field set for people, type, topic and review state. Use standard descriptive fields selectively plus visible Field Lab schema columns; do not overwrite editor-authored Description or Log Note to manufacture compatibility. Test project/clip values as well as file-XMP values. Keep complex predicates, full graph links and vectors in Field Lab.

### 4. Premiere already has local AI search; our embeddings cannot be assumed importable

Adobe's current Search-panel FAQ says transcript/metadata searches use exact text matches while visual queries use natural descriptions. It says analysis/search run locally with fixed Adobe models. Adobe's visual-search instructions describe its own background analysis of sampled stills. No reviewed Adobe documentation exposes an import contract for our CLIP/Orama embeddings or identity graph into that index. [Search FAQ](https://helpx.adobe.com/premiere/desktop/organize-media/file-organization/media-intelligence-and-search-panel.html), [Media Intelligence search](https://helpx.adobe.com/premiere/desktop/organize-media/file-organization/search-for-media-using-ai-powered-media-intelligence.html).

**Proposal:** materialize useful saved web/chat search results as named bins, named subclips/range markers and optional selects sequences. Store query/provenance/result IDs in bridge metadata. Refresh these generated memberships through the bridge, not by claiming that a native Search Bin executes our vector query. Adobe's own visual search can coexist as another editor tool.

## Benefit-first mapping

The table is a proposed product mapping, not a claim that all targets already have proven automation APIs.

| Web UI / pipeline feature | Useful Premiere-native output | Editor benefit / ownership |
|---|---|---|
| Full timestamped transcript | Source-clip native Transcript, with speaker IDs/names; captions generated when wanted | Text-Based Editing and exact phrase finding. Merge editor corrections; never rebuild blindly. |
| Speaker identity / reviewed voice name | Native transcript speaker labels plus clip-level people/topic summary | Distinguish speakers in dialogue. A voice identity is not proof that a face is on screen. |
| Approved face name and timed appearance | Clip range marker “Ariel visible”; people metadata on relevant source/subclip | Navigate actual appearances. Only confirmed evidence projects as fact; uncertain recognition remains reviewable. |
| Face/voice linking review | Approved name relationships become transcript names and appearance markers; optional evidence still bin | Native Premiere has no documented equivalent of our whole embedding/review graph. Keep graph and review controls in Field Lab; expose decisions, not vectors. |
| Brief three-chunk visual outline | Three named clip range markers; optional named subclips if useful | Browse useful sections without marker clutter. Long source clips remain intact. |
| Human timed note | Clip point/range Comment marker | Notes travel with source clips into editorial; preserve editor changes and deletions. |
| Human whole-clip note | Visible Comment/Log Note or dedicated human-owned Field Lab property | List-view reading/sorting; separate human annotations from AI Description. |
| Topic / shot type / action / location tags | Small tested set of searchable metadata columns plus template Search Bins | “Interviews”, “B-roll”, “Ariel”, “Exteriors”, “Needs review” update without duplicate media. Multivalues must be flattened/encoded deliberately and tested for token semantics. |
| Saved semantic/image/chat search | Generated named result bin of source items/subclips; optional ordered result/selects sequence | “Ariel explaining sea ice”, backed by actual evidence, becomes material the editor can drag or cut immediately. Query is executed by Field Lab; generated native bin is its current projection. |
| Accepted quote/selected passage | Named subclip with handles and/or range marker, optional selects/stringout sequence | Editable source range, not a rendered new MP4. Sequence is a working editing surface. Preserve original source/timecode. |
| Favorite / approved / rejected | Native Favorite if proven writable; otherwise tested review-status field and label/marker convention | Favorites/approved Search Bins. Do not overload one label color with every independent attribute. |
| Similar-clip link or inherited note | Shared tags + optional “Related” result bin / marker comments | Useful related material is exposed; graph edge and confidence remain explicit in Field Lab. Never project an unreviewed inference as an operator assertion. |
| Stills / thumbnails | Selected reference stills in a dedicated bin; freeform arrangement optionally | Useful reference/contact sheet material, not all sampled frames dumped into editorial bins. A still frame is not a subclip. |
| Camera, card/roll, shoot day, scene/take | Camera Label/Angle where semantically correct; Tape Name/Scene/Shot/logging fields and source bins | Find source, sort footage, support multicam grouping. Do not guess sync from filenames or use a roll ID as a camera ID. |
| Timecode + audio alignment | Actual multicamera source sequences once sync is verified | Editable multicam, not just metadata text claiming “synced”. |
| Proxies | Actual Attach Proxies relationships and proxy-status columns | Fast editing with source originals retained for finishing. A path string in XMP is not an attached Premiere proxy. |
| Color/settings/look history | Accurate metadata + verified source color interpretation; optional reference markers/stills | Helps grading without treating a narrative look note as an applied transform. Do not auto-apply a LUT from an AI inference. |
| Verified offload/backup status | Read-only status column + provenance/receipt reference | Editor sees readiness. A native metadata flag is a report, not the underlying checksum/backup proof. |

## Native object details and boundaries

### Subclips, bins, labels and sequences

Adobe documents source In/Out → Make Subclip producing a subclip project item. It references the source range, rather than requiring a new transcoded file. Use meaningful names and enough handles for editorial; validate trim behavior and match-frame in the installed build. [Create subclips](https://helpx.adobe.com/nl/premiere/desktop/edit-projects/intro-to-editing/create-a-subclip-from-the-project-panel.html).

Current UXP FolderItem exposes normal bin operations and `createSmartBinAction(name, searchQuery)`; ProjectItem exposes color-label changes and stable-looking project-item IDs. The API existence is not a complete search-query serialization specification; seed tested search templates or use a host-verified query serializer. [FolderItem](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/folderitem), [ProjectItem](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/projectitem).

Generated sequence updates should be conservative: maintain `Field Lab / Selects / <query>` as owned output, and let the editor duplicate it into an editorial bin. Never rebuild an editor's actively cut sequence when a search result changes. Version materialized query sequences if the editor has changed them. An app-level bridge should create/save the real PRPROJ; direct XMP updates alone do not create native bins, subclips, sequences or proxy relationships.

### Camera labels can do real work

Adobe documents multi-camera creation by timecode, audio, In/Out or marker synchronization; Camera Label/Camera Angle metadata determine grouping onto tracks. Populate camera identity consistently before multicam creation, but require actual synchronization evidence. Face/voice co-occurrence is not camera sync evidence. [Create multi-camera source sequence](https://helpx.adobe.com/ca/premiere/desktop/edit-projects/set-up-multi-camera-sequences-for-editing/create-a-multi-camera-source-sequence.html).

### Proxy relationships are not generic metadata

Adobe permits attaching externally generated proxies to supported video/video+audio/offline project items; audio-only and still sources are not supported by that attachment workflow. Its ingest/proxy documentation also lists unsupported Interpret Footage/Modify Audio Channels behavior and other compatibility limits. Generate and validate matching proxy timing/audio configurations; then attach through Premiere. [Attach proxies](https://helpx.adobe.com/in/premiere/desktop/organize-media/ingest-proxy-workflow/attach-proxies-to-full-resolution-media.html), [Proxy limitations](https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/ingest-and-proxy-workflow.html).

### Separate generated library from editorial projects when warranted

Adobe Productions supports smaller linked projects and primary clips referenced by other projects; it is intended for long-form/shared-media work. Team Projects is cloud-managed collaboration, which is a different deployment choice. [Team Projects versus Productions](https://helpx.adobe.com/premiere/desktop/collaborate-with-others/collaborate-using-team-projects/when-to-use-team-projects-and-when-to-use-productions.html).

**Proposal:** begin with a conventional project and a clearly owned `Field Lab` bin, then consider a Production separating generated source/metadata and editor sequences if target-build cross-project metadata/marker propagation is proven. Do not force Productions complexity on a solo operator merely to solve an untested sync problem. One `.prproj` file cannot be safely rewritten externally while Premiere owns it.

## Suggested first proof

Use two short owned clips, one with multiple speakers and one with several visual chunks. Have Field Lab generate whole-clip descriptive fields, confirmed people, three range markers, one human annotation, a native transcript JSON, and one saved search's named result/subclip sequence. In Premiere verify:

1. Fields appear as columns and tested Search Bin predicates really match; custom schema is not merely visible.
2. Markers search/seek at frame-accurate locations, range durations and source/timecode offsets are correct.
3. Source transcript JSON imports via UXP, exports with same wording/timing/speakers, enables Text-Based Editing, and survives save/reopen.
4. Named selections can be edited/match-framed with handles and use original media; proxy attachments and multicam are genuine native relationships if included.
5. Change a human note/marker/transcript speaker name in Premiere, then sync both directions. Conflict handling preserves the edit. Deleting or reordering editor-owned objects is not undone.
6. Repeat sync without duplicate native objects; reopen generated PRPROJ and an editorial duplicate and verify both. Test an interruption before/after native transaction/save without clearing original annotations.

The result should be an editing-ready project with searchable facts, navigable ranges and usable selects—not a project full of opaque AI fields.
