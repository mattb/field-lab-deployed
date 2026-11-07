# Cross-interview research

Status: implemented in the research development branch; real local-model evaluation is in progress. Deployment to the field installation has not been validated. No project-specific transcripts, names, labels, or metadata belong in this repository.

## Using research mode

Open **Research** in the existing media library. Write a question and choose **Ask**, or **Save** to keep it for later. Notes and follow-up questions are optional. Answers contain playable source links, with expandable claim evidence and source coverage. The original media remains in the shared player. Editing a saved question retains earlier answers as history and marks them stale; a new indexed snapshot also makes an earlier answer stale. Asking again creates another answer rather than overwriting history.

Qwen3.8-27B, 4-bit MLX, handles synthesis. The existing local text embeddings, cross-encoder and native Orama 3.1.18 index supply original transcript evidence. This mode does not use an external inference API or a second vector database. Fast ingest captions continue to use Qwen3.5-4B.

Questions, notes, runs and immutable answers live transactionally in the project's existing SQLite store. A question version check prevents competing edits from silently overwriting each other. A research server holds an execution lock, and startup marks unfinished runs interrupted so saved questions can be retried. There is at most one queued or active research job, sharing the ordinary chat executor and model owner. Generated answers are not fed back into the evidence index.

Each run pins a published catalog/vector pair. Saved sources include the source content identity, exact transcript text, timing and snapshot revision. Renames can still play; replaced or missing media cannot silently substitute for the original source. The library shows pending interview counts, and a partial corpus is never described as complete. A server managing an active ingest refuses research execution until that ingest is quiet; development evaluation uses a separate frozen test project and server.

Research progress shows elapsed time and actual model context/decode counters when the local model emits them. These are progress measurements, not a promised completion percentage or ETA for the whole investigation. Model evidence packets omit repeated provenance already retained in storage. Native MLX prefix caching is enabled with a 2 GiB memory limit and disk caching disabled; actual reuse and latency must be measured on the target Mac. The model is released after the investigation finishes.

The sections below describe the implemented evidence strategy and optional future enrichment. The current implementation retrieves existing speech records and reads their neighboring context; it does not yet generate section maps, derived interview overviews or automatic follow-up updates.

## Recommendation

Extend the existing local chat with an interview-research mode. Reuse Orama 3.1.18 for retrieval, the existing MLX text embeddings and reranker, PydanticAI for tool orchestration, and Qwen3.8-27B for synthesis. No new vector database or search framework is needed. Start with original transcript evidence; add a resumable, optional text-only enrichment pass after that works.

The useful product is a research assistant that can compare accounts, find explanations, assemble sourced interview briefs, and maintain a list of unanswered questions. Shot search remains available in the same application. Research answers need their own text-evidence validation rather than the current requirement for visually inspected matching shots.

## Two levels of evidence

| Level | Contents | Purpose |
| --- | --- | --- |
| Original transcript passages | Exact recognized words, token/sentence times, source media and stream, transcript revision, speaker observations | Quotable and playable evidence; available as soon as transcription is published |
| Derived research records | Question/answer blocks, topic headings, concise summaries, entities and explicitly expressed plans or uncertainties; every record links to original spans | Find the right interview and surrounding explanation; never replace the original testimony |

Use the same Orama text index and model space for both. Distinguish record types and provenance. Keep the existing CLIP index for visual queries; do not use face, voice or CLIP vectors as text embeddings. A future embedding-model change must rebuild a separate revision of all affected text vectors, not mix incompatible spaces.

Original records should have stable references based on source content, stream, transcript revision and token/span identity. Carry both start and end times. Editing a transcript should invalidate affected derived records and their vectors while preserving the original machine transcript and operator audit history.

## Start directly from transcripts

The current search projection creates speech documents from sentences, splitting long sentences into 85-word pieces. This supports pinpoint retrieval, but short sentences often omit what a pronoun or a reply refers to. Long sentence splits also share the original sentence's time range, so a chunk start is not necessarily a word-accurate quote start.

Build small, speaker-aware retrieval passages around adjacent utterances. Keep each embedded input below the existing embedding tokenizer's **256-token** limit, including any contextual prefix. A provisional target is 120–200 tokens with a small overlap; measure and split using the actual tokenizer, not a word-count assumption. Long answers become multiple child passages linked to a parent answer/section. Fetch the larger parent or neighboring turns only after retrieval. Never silently truncate a long answer into one embedding.

Preserve the interviewer's question as context while distinguishing it from the interviewee's answer. A question about an experience is not evidence that the interviewee had that experience. Ambiguous speaker overlaps stay ambiguous. A folder title can suggest an interview subject; it cannot establish who spoke every sentence. Reuse saved, reviewed voice identities and permit unknown speakers.

## Optional preprocessing

Once raw retrieval is useful, run a separate local text-only job to propose:

1. Question/answer boundaries and topic sections, with source spans.
2. A short interview overview and candidate topic/entity aliases.
3. A small number of sourced research notes: explanations, personal accounts, intentions, uncertainty and follow-up questions.

These records are explicitly machine-derived. Validate span references and exact quote strings programmatically; flag uncertain names and technical terms for correction rather than silently rewriting testimony. An LLM's extracted claim is not an independently verified fact. Prefer a compact section map over generating hundreds of overlapping summaries or hypothetical questions for every sentence.

Use the existing 27B reasoning role for this optional pass; keep it outside the ingest critical path. It needs its own measured time and memory budget. Start with one overview pass per interview and only introduce more expensive extraction if an evaluation demonstrates a benefit. Do not add another heavy model job alongside an already busy ingest merely to populate research summaries.

## Retrieval and answering

For a focused question:

1. Search transcript passages with Orama's native lexical + vector hybrid search. Exact names, acronyms and scientific terms need lexical retrieval; paraphrases need vectors. Search concise subquestions separately when useful.
2. Rerank bounded candidate batches with the existing local cross-encoder. Merge overlapping passages and fetch neighboring turns or the parent answer. Keep the quality gate independent of relative Orama ranking.
3. Give Qwen a compact evidence bundle containing source IDs, speaker attribution and uncertainty, exact text and times. Generate an answer whose material claims cite those spans.
4. Validate citation IDs, quote substrings and media ranges; open citations in the existing player. Semantic support still needs evaluation—valid IDs alone do not make an answer correct.

For cross-interview comparisons, add source diversity. Group candidates by a scalar string `interviewId` and take a bounded number per interview before final selection, or issue native filtered searches per interview. In 3.1.18 `groupBy` accepts string/number/boolean fields, **not** our current enum `assetId`; do not assume the existing schema works unchanged. Reranking should not let one verbose interview occupy the whole context.

For broad questions such as “what themes recur across the interviews?”, top-k passage retrieval is insufficient. Enumerate the completed interview inventory, review each interview's section map/overview, collect supporting original passages, and synthesize across that set. Track which interviews were considered and which were unavailable. A derived overview is a routing aid; final testimony claims must resolve to original evidence.

Counts must be calculated from distinct sources or reviewed people, not passage counts. Repeated interviewer questions and duplicate media do not represent independent agreement. A facet shows matching indexed records, not scientific consensus. Differences may concern different places, conditions or time periods rather than a genuine contradiction; show both accounts with those qualifications.

## Agent and UI

Reuse the current framework and citation/player controls. Add a research answer structure with claim-to-evidence references and coverage, alongside the existing shot answer structure. Do not force a knowledge answer into `VisualEvidence`, “matching shot” failures, or a three-sentence answer limit.

The main tool contracts can stay small:

- `search_interviews(query, interview_ids?, sources_per_interview?)`: native Orama retrieval plus existing reranking, restricted to transcript/research records.
- `read_passages(ids, surrounding_context?)`: original text, questions/answers, attribution and source times.
- `interview_coverage()`: completed, pending, failed and searched sources at a pinned snapshot.
- `open_sources(ids)`: existing local playback links, using retrieved source IDs.

These are additions to the current tools, not a new agent framework. Keep the browser UI minimal: a sourced answer, playable citations, and expandable coverage. The operator should not manage chunks, vectors, CSVs or generated metadata.

## A useful extension: a living question board

For each research question, retain the best cited answers, differing accounts, uncertainties and proposed follow-ups. Let the operator save or edit a question in the existing metadata store. When new field interviews arrive, update relevant evidence and suggest follow-ups. With explicit preproduction/field dates, this can compare an earlier expectation with a later experience without overwriting either account. Suggested editorial connections remain proposals, visibly separate from what participants actually said.

This provides practical outputs: an interview brief, a comparison of complementary voices, a sourced story outline, or questions to ask tomorrow. It does not require a graph database. Ordinary source-ID relationships and the existing SQLite store are sufficient initially.

## Persistence and live ingest

Read only sealed published catalog/transcript generations. Each answer records the snapshot revision and coverage it used. The next question may see a newer snapshot; an in-flight answer should not silently combine generations.

Store canonical research records and operator edits transactionally in the existing project SQLite database. Key derivation jobs by transcript hash, model revision, prompt version and segmentation recipe. Reconcile unfinished work at startup; publish a new complete search generation atomically. A failed or interrupted enrichment job leaves the previous searchable generation available. Orama serialization is an optional rebuildable cache, not the authoritative store for names, edits or research notes.

## Why not stock AnswerSession?

The 3.1.18 implementation looks for the Secure Proxy plugin by name and invokes its chat transport; it is not a documented general local-model provider interface. More seriously, a zero-network mocked test retrieved a sentinel source successfully but observed that its text was absent from the messages sent to the mock model. Search results were present only in session state. A hypothetical custom proxy could work around this, but we already have an agent framework that passes tool evidence to local Qwen.

Keep Orama's retrieval and the existing PydanticAI/MLX generation path rather than forking AnswerSession or pretending that enabling it alone creates grounded question answering. [Pinned source](https://github.com/oramasearch/orama/blob/v3.1.18/packages/orama/src/methods/answer-session.ts).

## Evaluation before calling it useful

Build a manually reviewed question set covering exact facts, paraphrases, a pronoun-dependent answer, multiple interviews, disagreement versus different scope, an unsupported question, uncertain speaker identity and a corrected transcript. Include both focused questions and broad corpus summaries.

Measure retrieval recall, source diversity, correct speaker attribution, quote accuracy, claim support, appropriate abstention, coverage reporting and response latency. Compare raw-passages-only against the optional preprocessing on the same questions. Also interrupt enrichment, resume it, edit a transcript, reorganize a source folder and confirm that saved labels and source links survive. Use disposable projects for failure tests.

## Documentation basis

All 42 Orama JS documentation articles and five navigation files were read at documentation commit `94310a95680e06f12f7139af4982e1ccf8621d98`. The live documentation is not a versioned 3.1.18 manual; relevant behavior was cross-checked against package tag `v3.1.18`, commit `2fe41e163c2bdd7830eed7496c69134aea8ee3ba`. See the [read-through record](research/ORAMA-3.1.18.md).
