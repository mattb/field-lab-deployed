# Cross-interview research

Status: implemented in the research development branch; real local-model evaluation is in progress. Deployment to the field installation has not been validated. No project-specific transcripts, names, labels, or metadata belong in this repository.

## Using research mode

Open **Research** in the existing media library. Write a question and choose **Ask**, or **Save** to keep it for later. Notes and follow-up questions are optional. Answers contain playable source links, with expandable claim evidence and source coverage. The original media remains in the shared player. Editing a saved question retains earlier answers as history and marks them stale; a new indexed snapshot also makes an earlier answer stale. Asking again creates another answer rather than overwriting history.

Qwen3.8-27B, 4-bit MLX, is the default local model for evidence collection and synthesis, using the existing agentic-chat role. The existing local text embeddings, cross-encoder and native Orama 3.1.18 index supply original transcript evidence. Fast ingest captions continue to use Qwen3.5-4B. Research does not use an external inference API or a second vector database. A development run with another local model is an explicit evaluation choice, not a production fallback or evidence of equivalent quality.

Questions, notes, follow-ups, run diagnostics and immutable answers live transactionally in the project's existing SQLite store. A question version check prevents competing edits from silently overwriting each other. A research server holds an execution lock, and startup marks unfinished runs interrupted so saved questions can be retried explicitly. There is at most one queued or active research job, sharing the ordinary chat executor and model owner. Generated answers and earlier saved answers are not added to the evidence index or supplied as transcript evidence for the next run. Operator notes guide the question and are labeled as context rather than testimony.

Each run pins a published catalog/vector pair. Saved sources include the source content identity, exact transcript text, timing and snapshot revision. Renames can still play; replaced or missing media cannot silently substitute for the original source. Coverage distinguishes ready, pending, failed, searched and read sources. Searching a recording or reading an opening does not establish that its entire transcript was reviewed.

Ingest has priority. A server managing an active ingest refuses a new research run while retaining the question. If ingest starts during research, a guard interrupts the investigation and stops only the model process owned by the chat service. Retry the saved question when ingest is quiet; interrupted investigations do not automatically resume. An independently started model server remains under the operator's control. Development evaluation must use a separate disposable project/server rather than mutate a live ingest.

Research progress shows elapsed time, collection/writing stages and actual model context/decode counters when the local model emits them. These measurements do not provide a completion percentage or whole-investigation ETA. Native MLX prefix caching is enabled with a 2 GiB memory limit and disk caching disabled; actual reuse, memory requirements and latency must be measured on the target Mac. The app releases the model it owns after the investigation finishes.

## Current two-phase investigation

1. **Collect original evidence.** A bounded collector receives the question and pinned interview inventory. It can read interview openings, search original transcripts and read surrounding exchanges. Searches rerank both original fragments and same-stream neighboring context, without marking ranking-only material as read. When more than one hit is requested, temporally distinct qualifying windows are preferred over duplicate nearby anchors. Searches include bounded neighboring original context; comparisons can also search up to three other already-opened recordings for cross-check candidates. These candidates are not independent agreement or proof of absence. Evidence reads execute sequentially. A run admits at most 12,000 unique original words or 800 passages; rereads are free, and a request is admitted completely or refused explicitly without truncating testimony. This is a processing bound, not evidence of absence. The collector has at most six model requests and 24 tool calls, and is instructed to stop reformulating an unresolved subquestion after two attempts.
2. **Build the original dossier.** The application groups exact passages recorded in the read ledger by interview, with source references, audio stream, start/end times, available speaker labels and coverage. The dossier records whether collection returned normally, exhausted its budget or encountered a model-output error. This is application status, not a finding about the evidence. Unread search hits, collector prose and collector conclusions are excluded. Repeated provenance is retained in canonical storage rather than repeated in every model packet. Passage limits can omit context; the collector can request additional specific passages. Listed excerpts can be discontinuous; their order alone does not establish an exchange between speakers.
3. **Write from that dossier.** A separate writer invocation receives the question and original evidence only. It has no retrieval tools. It returns one set of typed paragraphs and useful follow-ups through PydanticAI native JSON-schema output, constrained by the installed MLX server and llguidance. Evidence paragraphs include their original source IDs once; the application derives answer prose, structured claims, playable links and source cards from them. Explicit limitation paragraphs retain their full text in the limitations section and cannot masquerade as source-backed claims. An answer made only of gaps must explicitly be unsupported. List counts and total tokens are bounded; prose has no character cutoff that could force an unfinished sentence. Current evaluation defaults use medium reasoning with a 1,536-token thinking allowance and a 5,500-token completion cap; smaller models and nonthinking generation are being compared privately, without changing the deployed model role. The writer phase permits at most three model requests, including validation retries. The two phases share a 12-minute investigation limit; reaching a limit does not establish that relevant evidence is absent.

The phases reuse PydanticAI and the same local chat model. This separation prevents a collector's generated summary from becoming the writer's evidence. It does not guarantee that the collector found every relevant exchange or that the writer interpreted it correctly.

## Grounding checks and quality limits

The application checks that supported claims cite original passages retrieved and read in this run, that each declared exact quote occurs within one cited original chunk, and that source cards match the union of claim citations. Quotes cannot be assembled by joining chunks. Model validation limits publication to 40 unique source passages, matching playback publication's bound. Answer prose must include playable citations covering every interview used by its claims. A supported comparison requiring at least three interviews also needs at least three distinct claims. Corpus-scoped answers require searching and reading context from every available transcribed recording, or a qualified partial outcome. Pending interviews must remain visible as a coverage limit.

Before the original passage arrays, the writer receives an application-owned `publication_policy`: the effective minimum distinct cited recordings, the fully answered paragraph minimum, corpus coverage rules, missing search/read-context recording aliases and counts, and the unavailable-inventory count. These are the live validator's requirements, including any collection-cohort override; they do not assert user intent, verified speakers, scientific support or exhaustive reading. Choosing `scope=corpus` also invokes corpus coverage even when the application's corpus flag is false. Incomplete source-count or corpus coverage may use the existing `partial` outcome with nonempty limitations, or an unsupported response without claims or sources; other evidence and citation checks still apply. The unavailable-inventory count includes assets without ready transcript passages and must not be mistaken for a precise count of pending interviews in a mixed-media project. Rebuilding a dossier refreshes this policy from live run state while retaining original passage content; saved policy from another run is not current authority.

The top-level machine-owned policy is trusted output requirements; transcript text and recording labels remain untrusted data, including any instructions or similarly named fields inside them. Its `permitted_outcomes` reflects only current read-evidence, possible distinct-source count and application-required corpus coverage: no read evidence permits only unsupported, while insufficient source count or required corpus search/read gaps permits a limited partial answer or unsupported. Otherwise answered is potentially eligible, not certified; writer-chosen corpus scope can further restrict it, and every claim, citation, quotation, paragraph and limitation gate remains in force. The metadata does not automatically rewrite an outcome or establish semantic correctness.

These are reference, presentation and coverage checks. They do not prove that a paraphrase follows from its citation, a comparison makes the right connection, an inferred speaker is correct, or an answer is useful. A claim can pass structural validation while exaggerating certainty, conflating habitats or omitting a crucial qualification. Independent semantic evaluation against the original surrounding exchanges remains necessary; successful tool calls or valid IDs are not a quality score.

The source-count gate does not resolve names in a question into a required set of recordings. Reading a cohort of interview openings helps route collection, but does not mechanically guarantee that every named source is represented in the final answer. Check the actual source coverage for the requested recordings.

Recording labels identify source files, not the speaker of every sentence. A single recognized sentence can combine a question and an answer. Anonymous saved voice-group aliases provide timing context only: groups can be provisional or incorrectly matched, their sentence-wide associations can cover successive turns or overlap and can include another audio stream. They do not establish names, roles or word-level attribution. Unknown or overlapping speakers remain unresolved; interviewer suggestions are not testimony. Plans and hopes are not completed outcomes, and differences between places or conditions are not automatically contradictions. Machine transcripts and their quotations are not audio-verified. Long sentence chunks share sentence timing, so playback may begin before the quoted words.

The writer can return `answered`, `partial` or `not_supported`. A failed validation or exhausted writer budget can produce a conservative inability-to-finish response; it must not be interpreted as a finding that the corpus lacks evidence. Research is not yet validated for deployment to the field installation, and no response-time or semantic pass rate is promised here.

## Collector tools and playback

The collector has four tools: `read_interview_openings(interview_ids)`, `search_interviews(query, interview_ids?, sources_per_interview?)`, `read_passages(ids, surrounding_context?)` and `interview_coverage()`. They use request-local references that the application maps back to canonical source identities. Search and read tools return original transcript text, not generated enrichment. Source playback is derived by the application after validation; the writer does not call `open_sources` or construct its own URLs.

Research has its own answer structure with claim evidence and coverage alongside shot search. It does not require visually matching shots or a three-sentence answer limit. The operator sees a sourced answer, playable citations and expandable coverage. Partial and unsupported outcomes are explicitly labeled, with their limitations initially expanded. Polling responses omit raw model messages and internal provenance ledgers; canonical diagnostics remain stored transactionally.

## Extending the persistent question board

The current board already saves questions, notes, follow-ups and immutable answer history. Editing a question or changing the snapshot can make an old answer stale; they do not automatically trigger another investigation. A future extension could update relevant evidence and propose new follow-ups when field interviews arrive. With explicit preproduction/field dates, this can compare an earlier expectation with a later experience without overwriting either account. Suggested editorial connections remain proposals, visibly separate from what participants actually said.

This provides practical outputs: an interview brief, a comparison of complementary voices, a sourced story outline, or questions to ask tomorrow. It does not require a graph database. Ordinary source-ID relationships and the existing SQLite store are sufficient initially.

## Optional future enrichment

The implementation currently retrieves existing speech records and original neighboring context. It does not generate section maps, derived interview overviews, automatic question-board refreshes or evidence updates when new interviews arrive. The design notes below describe optional extensions; they are not additional implemented ingestion work.

### Two levels of evidence

| Level | Contents | Purpose |
| --- | --- | --- |
| Original transcript passages | Exact recognized words, token/sentence times, source media and stream, transcript revision, speaker observations | Quotable and playable evidence; available as soon as transcription is published |
| Derived research records | Question/answer blocks, topic headings, concise summaries, entities and explicitly expressed plans or uncertainties; every record links to original spans | Find the right interview and surrounding explanation; never replace the original testimony |

Use the same Orama text index and model space for both. Distinguish record types and provenance. Keep the existing CLIP index for visual queries; do not use face, voice or CLIP vectors as text embeddings. A future embedding-model change must rebuild a separate revision of all affected text vectors, not mix incompatible spaces.

Original records should have stable references based on source content, stream, transcript revision and token/span identity. Carry both start and end times. Editing a transcript should invalidate affected derived records and their vectors while preserving the original machine transcript and operator audit history.

### Start directly from transcripts

The current search projection creates speech documents from sentences, splitting long sentences into 85-word pieces. This supports pinpoint retrieval, but short sentences often omit what a pronoun or a reply refers to. Long sentence splits also share the original sentence's time range, so a chunk start is not necessarily a word-accurate quote start.

Build small, speaker-aware retrieval passages around adjacent utterances. Keep each embedded input below the existing embedding tokenizer's **256-token** limit, including any contextual prefix. A provisional target is 120–200 tokens with a small overlap; measure and split using the actual tokenizer, not a word-count assumption. Long answers become multiple child passages linked to a parent answer/section. Fetch the larger parent or neighboring turns only after retrieval. Never silently truncate a long answer into one embedding.

Preserve the interviewer's question as context while distinguishing it from the interviewee's answer. A question about an experience is not evidence that the interviewee had that experience. Ambiguous speaker overlaps stay ambiguous. A folder title can suggest an interview subject; it cannot establish who spoke every sentence. Reuse saved, reviewed voice identities and permit unknown speakers.

### Optional preprocessing

Once raw retrieval is useful, run a separate local text-only job to propose:

1. Question/answer boundaries and topic sections, with source spans.
2. A short interview overview and candidate topic/entity aliases.
3. A small number of sourced research notes: explanations, personal accounts, intentions, uncertainty and follow-up questions.

These records are explicitly machine-derived. Validate span references and exact quote strings programmatically; flag uncertain names and technical terms for correction rather than silently rewriting testimony. An LLM's extracted claim is not an independently verified fact. Prefer a compact section map over generating hundreds of overlapping summaries or hypothetical questions for every sentence.

Use the existing 27B reasoning role for this optional pass; keep it outside the ingest critical path. It needs its own measured time and memory budget. Start with one overview pass per interview and only introduce more expensive extraction if an evaluation demonstrates a benefit. Do not add another heavy model job alongside an already busy ingest merely to populate research summaries.

### Retrieval and answering

For a focused question:

1. Search transcript passages with Orama's native lexical + vector hybrid search. Exact names, acronyms and scientific terms need lexical retrieval; paraphrases need vectors. Search concise subquestions separately when useful.
2. Rerank bounded candidate batches with the existing local cross-encoder. Merge overlapping passages and fetch neighboring turns or the parent answer. Keep the quality gate independent of relative Orama ranking.
3. Build the original dossier from the read ledger and give it to the separate writer. Any future derived records should route retrieval rather than replace the cited original spans.
4. Validate citation IDs, quote substrings and media ranges; open citations in the existing player. Semantic support still needs evaluation—valid IDs alone do not make an answer correct.

For cross-interview comparisons, add source diversity. Group candidates by a scalar string `interviewId` and take a bounded number per interview before final selection, or issue native filtered searches per interview. In 3.1.18 `groupBy` accepts string/number/boolean fields, **not** our current enum `assetId`; do not assume the existing schema works unchanged. Reranking should not let one verbose interview occupy the whole context.

For broad questions such as “what themes recur across the interviews?”, top-k passage retrieval is insufficient. Enumerate the completed interview inventory, review each interview's section map/overview, collect supporting original passages, and synthesize across that set. Track which interviews were considered and which were unavailable. A derived overview is a routing aid; final testimony claims must resolve to original evidence.

Counts must be calculated from distinct sources or reviewed people, not passage counts. Repeated interviewer questions and duplicate media do not represent independent agreement. A facet shows matching indexed records, not scientific consensus. Differences may concern different places, conditions or time periods rather than a genuine contradiction; show both accounts with those qualifications.

### Persistence and live ingest

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
