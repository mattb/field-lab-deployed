# Orama JS full read-through

Scope: the complete `content/docs/orama-js` documentation subtree, including plugins, language support, internals and navigation—not Orama Cloud, OramaCore, unrelated example websites or linked third-party blog posts. Read 2026-11-06, America/Los_Angeles.

Documentation repository revision: [`94310a95680e06f12f7139af4982e1ccf8621d98`](https://github.com/oramasearch/docs/tree/94310a95680e06f12f7139af4982e1ccf8621d98/content/docs/orama-js). Inventory: **42 MDX articles + five navigation JSON files; 178,235 source bytes**. Every file was read in full. An initial oversized tool response was truncated; all affected files were subsequently reread in smaller complete batches.

Package reference: [`@orama/orama` v3.1.18](https://github.com/oramasearch/orama/tree/v3.1.18/packages/orama), revision `2fe41e163c2bdd7830eed7496c69134aea8ee3ba`. Read its package README and manifest, and the full AnswerSession, hybrid search and grouping implementations. Documentation and package version are distinct snapshots; do not assume a live example is an executable specification.

## Retrieval index and complete coverage

All paths below are under the pinned documentation subtree linked above.

| Task / area | Files read | Relevance to interview research |
| --- | --- | --- |
| Basic API and generation | `index.mdx`, `answer-engine.mdx` | Embedded retrieval; stock generation has a proxy dependency |
| Schemas and mutation | `usage/create.mdx`, `insert.mdx`, `remove.mdx`, `update.mdx`, `utilities.mdx` | Typed fields, stable IDs, batched insertion, removal, retrieval by ID |
| Search basics and tuning | `search/index.mdx`, `bm25.mdx`, `changing-default-search-algorithm.mdx`, `fields-boosting.mdx` | Exact terms and conceptual queries; benchmark ranking changes |
| Hybrid and vector search | `search/hybrid-search.mdx`, `vector-search.mdx` | Accept existing MLX vectors; keep compatible dimensions |
| Constraints and coverage | `search/filters.mdx`, `facets.mdx`, `grouping.mdx`, `preflight.mdx` | Restrict source types and diversify by interview; counts need interpretation |
| Presentation and broadening | `search/sorting.mdx`, `threshold.mdx`, `results-pinning.mdx` | Threshold is not confidence; do not promote sources as though independently relevant |
| Place search | `search/geosearch.mdx` | Optional only when reliable coordinates are supplied; not needed initially |
| Components | `internals/components.mdx`, `utilities.mdx` | Extension interfaces exist; replacing the retrieval engine is unnecessary |
| Plugin hooks | `plugins/index.mdx`, `writing-your-own-plugins.mdx` | Can extend lifecycle; no custom indexing framework needed |
| Embeddings and cloud services | `plugins/plugin-embeddings.mdx`, `plugin-secure-proxy.mdx`, `plugin-analytics.mdx` | TensorFlow plugin duplicates our MLX path; cloud plugins are unnecessary for offline operation |
| Persistence and document parsing | `plugins/plugin-data-persistence.mdx`, `plugin-parsedoc.mdx` | Cache serialization exists; ingest structured transcript JSON directly to preserve provenance |
| Alternative lexical ranking | `plugins/plugin-qps.mdx`, `plugin-pt15.mdx` | Optional evaluation, not assumed improvements |
| Highlighting | `plugins/plugin-match-highlight.mdx` | Deprecated plugin; timestamps still require transcript alignment |
| Site integrations | `plugins/plugin-astro.mdx`, `plugin-docusaurus.mdx`, `plugin-nextra.mdx`, `plugin-vitepress.mdx` | Read; not needed for the existing React application |
| Language support | `supported-languages/index.mdx`, `using-chinese-with-orama.mdx`, `using-japanese-with-orama.mdx` | Tokenizer choice matters for future non-English material |
| Token analysis | `text-analysis/stemming.mdx`, `stop-words.mdx` | Preserve names, acronyms and negation; do not blindly remove words |
| Navigation | Root, `usage`, `search`, `plugins`, `supported-languages` `meta.json` | Verified inventory and documentation structure |

## Findings cross-checked against 3.1.18

- Hybrid search performs lexical/vector retrieval and returns groups/facets. Relative score normalization means a high final rank is not independent evidence of relevance.
- The grouping implementation permits scalar string, number and boolean properties. It does not accept enum or array fields. Our current `assetId` is enum; a proposed string `interviewId` avoids that mismatch.
- `threshold: 0` narrows keyword matching. It is not a probability of correctness. Vector `similarity` is a separate setting, and the cross-encoder's calibrated acceptance gate remains separate again.
- The embeddings plugin documented here uses TensorFlow.js and 512-dimensional embeddings. Our existing text pipeline uses local MLX with 384-dimensional vectors; supplying those vectors directly avoids another inference stack.
- The persistence plugin serializes/restores an index. It does not replace our transactional metadata storage or atomic generation publication.
- The pinned AnswerSession searches for the Secure Proxy plugin and sends its message list to that proxy's chat stream. A local mocked transport test confirmed that a retrieved sentinel document's body was absent from those messages. This was a controlled API test, not a cloud test or a benchmark of model quality.

## Reproducing the AnswerSession observation

Use an environment with `@orama/orama` pinned to 3.1.18. No cloud account, model or network request is needed:

```js
import { create, insert, AnswerSession } from '@orama/orama';
let sent;
const plugin = {
  name: 'orama-secure-proxy',
  extra: {
    proxy: {
      async *chatStream(args) {
        sent = structuredClone(args.messages);
        yield 'mock answer';
      },
    },
    pluginParams: { chat: { model: 'openai/gpt-4o-mini' } },
  },
};
const db = create({ schema: { text: 'string' }, plugins: [plugin] });
insert(db, { id: 'test-source', text: 'science EVIDENCE_SENTINEL_314159' });
const session = new AnswerSession(db, { systemPrompt: 'Answer using sources.' });
await new Promise(resolve => setTimeout(resolve, 0));
await session.ask({ term: 'science' });
console.log(session.state.at(-1).sources.count); // observed: 1
console.log(JSON.stringify(sent).includes('EVIDENCE_SENTINEL_314159')); // observed: false
```

This finding supports keeping the existing local tool-based agent for grounded synthesis. It does not prevent using Orama for retrieval. The resulting architecture proposal is [INTERVIEW-KNOWLEDGE-AGENT.md](../INTERVIEW-KNOWLEDGE-AGENT.md).
