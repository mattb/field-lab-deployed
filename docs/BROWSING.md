# Browsing the project

The left navigation has collapsible Files, Tags and People sections. Files reflects the indexed source paths and nested ingest folders. Expand a folder to see its subfolders and files. Collapse the entire navigation using the menu button.

Tags and people are flat, project-wide lists. Their parenthesized counts show matching files; multiple observations of one face in one file count once. Distinct file entries that share content still count as separate files. Root counts show total files, available tags and active People respectively. Tags retained in vocabulary history can have zero current file assignments. Note-only tags are not silently promoted to file tags.

Choose alphabetical, newest or most files using the sort control. Newest uses source modification time for files/folders and creation time for tags/people; folders use their newest descendant. Ties are alphabetical. A single file has population one, so file lists break population ties alphabetically.

Select a tag or face to browse matching files. Select the People heading to review people, names and reviewed image/audio evidence. Chat remains a separate tool below the navigation.

Search project searches the entire project across filenames, transcripts, notes, tags, person identities and sampled images. Typing a search clears the browsing scope; there are no separate image/text/face search boxes. Text and image matches retain independent relevance gates and interleave in the result list, with their source labeled. Missing image models leave text search available. Incomplete indexing may limit results until ready. Search stays local and uses the existing Orama indexes and local inference.

Navigation data refreshes from saved metadata. The compact endpoint is cached by catalog membership and metadata revision; it sends no embeddings or voice windows. Counts describe recorded assignments and sampled face detections, not proof of a person's absence elsewhere in a recording.

Drag the right edge of the sidebar to resize it; the browser remembers the width. See [People](PEOPLE.md) for review and migration controls.

Semantic text search uses local MiniLM sentence/paragraph embeddings, Orama hybrid retrieval and an MS MARCO cross-encoder reranker. MLX reranks up to 160 candidates in bounded batches; browser-only fallback uses a smaller pool. Transcript search derives overlapping 100-word passages (maximum 110), preserving original transcript timing and stream boundaries. The full transcript stays unchanged. Keyword fallback is explicitly shown when semantic models are unavailable. Similar wording does not guarantee a match: ambiguous phrases and relevance cutoffs can still miss useful passages.
