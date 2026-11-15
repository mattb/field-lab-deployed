# Third-party software and models

The MIT license covers this repository's original source. It does not relicense dependencies, models, training data, commercial desktop applications, or referenced publications. Review each upstream license for your use and distribution.

`deploy/assets.lock.json` records immutable download URLs and SHA256s for all downloaded model files, Python, Node, uv and FFmpeg source. `requirements.lock.txt` and the two npm package-lock files pin Python/JavaScript package versions. Package-manager integrity verification applies to npm packages; Python package versions are pinned but wheels are not yet independently hash-locked in this release.

The installer obtains Python from astral-sh/python-build-standalone, Node from nodejs.org, uv from astral-sh/uv and builds FFmpeg from its pinned official source without external GPL libraries. It builds Swift helpers locally. No proprietary desktop-app binary or model weight is redistributed through this repository.

Downloaded model families include Parakeet TDT, Qwen3.5 4B for ingest and Qwen3.8 27B for chat, Nemotron diarization, SpeechBrain ECAPA, MiniLM text embeddings/reranking, CLIP image/text embeddings, OpenCV YuNet and SFace. The lock file and preparation modules identify their exact repositories and revisions. Upstream model READMEs/licenses are downloaded where specified; availability or an MLX conversion does not itself grant extra usage rights. CLIP conversion uses a separate pinned PyTorch environment; runtime inference remains MLX.

Orama, React, react-markdown, remark-gfm, Transformers.js, ONNX Runtime Web, PydanticAI and other dependencies retain their upstream licenses. OffShoot Pro and Carbon Copy Cloner are separately licensed applications. Research notes contain links and commentary; original videos, full transcripts and book text are not bundled.

## Maintainer-only Premiere package builder

`development/premiere-packager/package-lock.json` pins Adobe's `@adobe/uxp-devtools-core` 1.2.0 (Apache-2.0) and npm dependency integrity hashes. Source: https://github.com/adobe-uxp/devtools-cli. The small checked-in wrapper calls the unmodified Adobe `PluginPackageCommand.package()` implementation. Dependencies are downloaded by `npm ci --ignore-scripts`, never vendored into the field plugin or shipped as model/runtime inference dependencies. See `docs/PREMIERE-INSTALL.md` for the tested arm64 fallback, provenance and native-install verification boundary.

- react-tag-autocomplete (ISC): https://github.com/i-like-robots/react-tag-autocomplete — offline asset tag editor; license retained in the npm dependency.
