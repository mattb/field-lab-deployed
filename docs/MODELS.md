# Local model roles

Field Lab has two Qwen roles. They share the existing local search tools and evidence rules; a larger language model does not create a separate search index.

| Work | Model |
| --- | --- |
| Automatic visual notes during ingest/reindex | Qwen3.5-4B, 4-bit MLX |
| Agentic chat and interview research: planning, collection, image inspection and synthesis | Qwen3.8-27B, 4-bit MLX |
| Complete audio transcripts | Parakeet TDT |
| Face/voice identities, text/CLIP embeddings and reranking | The existing specialized local models |

The 4B vision model keeps the routine ingest path bounded. Non-ingest Qwen reasoning uses the 27B chat model; chat must not silently fall back to 4B. Both run locally. The larger model can still make mistakes: named-person queries use operator labels and identity evidence, inspected candidate frames must satisfy every requested condition, and an unverified search publishes no matching shots.

Interview research uses two bounded invocations of this same default 27B role: an evidence collector, then a writer with an original-transcript dossier and no retrieval tools. It does not require a third model. Explicit development comparisons with another local checkpoint do not change the production role or establish equivalent quality. See [research behavior and limits](INTERVIEW-KNOWLEDGE-AGENT.md). Source-reference checks and model loading checks do not establish the semantic accuracy of a synthesis.

The current face stack is **YuNet 2023mar detection + SFace 2021dec recognition**, through OpenCV DNN on CPU. SFace produces 128-dimensional normalized identity vectors; it does not run through MLX or Qwen. See the [MLX-oriented replacement research](FACE-EMBEDDINGS.md) for candidates and the checks required before preserving labels through a model migration.

`ditnotes/model_roles.py` identifies the pinned roles. The chat checkpoint is `mlx-community/Qwen3.8-27B-4bit` at revision `10c35caafbb80f7dc6a7a432cdd11af10a6d4818`. `deploy/assets.lock.json` lists its exact download files, sizes and SHA256 values. The 27B download is approximately 16.1 GB in addition to the existing ingest/search models. Model weights are never stored in Git.

## Offline preparation

The normal install/update preparation downloads and verifies both Qwen models. `prepare-offline --check` reports `analysis_models` and `chat_models` separately; overall readiness requires both. Use the installed release's interpreter:

```sh
"$HOME/Library/Application Support/Field Lab/current/.venv/bin/python" \
  "$HOME/Library/Application Support/Field Lab/current/dit_notes.py" prepare-offline --check
```

The download cache and installed model generation can each hold a copy. Allow at least 65 GB free for first preparation, caches, dependencies and both model roles. The 27B model also needs substantially more unified memory than the 4B ingest model; validate actual chat and ingest on the recipient laptop instead of assuming disk readiness proves a usable memory/performance budget. The larger chat model is loaded on demand, rather than in the ingest warmup. After each chat turn, the app stops and reaps the model server it started to free that memory for the next ingest. A reused server started by the operator remains under the operator’s control.

Chat connects only to the same-machine model service. Its model identity must match the expected 27B checkpoint for this release. An existing 4B server or unrelated service on the chat port is an error to resolve, not a usable substitute. The app must not terminate a process it does not own merely to take that port.

## Updates

Changing only chat weights, role selection or the chat prompt requires preparing/deploying the new software and model assets, but does not invalidate completed footage analysis. Existing captions and transcripts remain associated with their original analysis profile. A changed ingest caption prompt or ingest model still creates a new analysis profile and can require reanalysis; use [the upgrade plan](UPGRADES.md).

Python dependency-lock changes remain conservative and can invalidate analysis even if the particular dependency is used only by chat. Review the reindex plan before assuming a model update is chat-only. Saved operator metadata and chat sessions remain in the project's durable database rather than a model folder.
