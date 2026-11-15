# Asset tags

The selected asset has a prominent **Tags** field above its player, for both video and audio. It uses [react-tag-autocomplete](https://github.com/i-like-robots/react-tag-autocomplete), bundled for offline use. Type a tag and press comma or Enter to add it, or choose an existing project tag from the suggestions. Click a tag's remove button to untag the asset. Additions and removals save automatically; leaving the field also saves unfinished text. Suggestions refresh on focus, after saves, and every ten seconds without replacing your edits. Selected tags are excluded from suggestions. A save button is available while changes are pending.

Tags describe the whole asset. Notes can still carry their own **Note tags**; those supply autocomplete suggestions but are never silently promoted to asset tags. Tag vocabulary is project-local and starts empty. Removing a tag from an asset retains its spelling in autocomplete history.

## Persistence and recovery

The project SQLite database is authoritative. Asset assignments, vocabulary, revision and audit record commit in one durable transaction. Updates require the revision that was read: concurrent edits return a conflict and preserve the typed entry for review. Browser drafts are scoped to project and asset, with local storage recovery when available. Browser drafts are not durable project metadata until the save succeeds; storage-disabled browsers retain drafts only for that page session.

Names normalize Unicode and whitespace and deduplicate without regard to case; the first project spelling is retained. A tag cannot contain commas, control characters or line separators. Limits are 64 tags per asset and 100 characters per tag.

`GET /api/asset-tags?content_id=...` returns assignments, project vocabulary and asset revision. `POST /api/asset-tags` accepts `content_id`, `tags` (an array of names), and `base_revision`. A stale revision returns HTTP 409 with current saved values. Tags remain associated with the existing content identity, independent of the asset's folder location.

## Search and the proposed Premiere bridge

Tags use the existing Orama index. Dedicated operator-tag records support text search, and stable tag IDs permit exact facet matching or intersections. No additional model or embedding pass is needed. A whole-asset tag is not evidence that a person or event appears at a particular timestamp.

The Premiere bridge is still a proposal. Its adapter should project these names to portable XMP keywords and maintain a Field Lab-managed bin per tag, keyed by stable project/tag ID. Membership must use exact assignments, never substring matching (for example, `Sea ice` must not match `Sea ice cream`). Untagging removes the managed membership on the next synchronization without deleting source clips or editor-owned objects. An asset may belong to several tags/bins. Materialized bin membership provides a fallback where native Search Bin keyword behavior is not proven. Refer to the [editorial proposal](research/PREMIERE-EDITORIAL-PROPOSAL.md) for ownership, offline synchronization, conflicts and saved-project acknowledgement.

No XMP or Premiere project is written by the asset-tags implementation itself.

## Verification

Tests use disposable projects: API validation, save/reopen, shared index projection, exact tag facets, optimistic conflicts, Unicode normalization, browser draft recovery, and process interruption before/after commit. The browser smoke test exercises autocomplete, comma-separated entry, blur-save and reload persistence. No live project database or verified original media is changed by these tests.
