# Read-only static sharing

Export a snapshot outside both the media project and source checkout. Keep deployment state and QA reports outside the published `site` folder. The exporter refuses an existing output directory and requires a reconciled current search projection. It copies allowlisted media, thumbnails, text metadata and bounded People review samples; it does not publish SQLite databases or raw face/voice embedding vectors.

Build the static entry using Vite's `build` API with `viewer/static.html` as `build.rollupOptions.input` and a separate output directory. Then run with the Field Lab Python runtime:

```sh
python -m ditnotes.static_export --project PROJECT --output EXPORT/site --tool-root PREPARED_RUNTIME --ui-dist STATIC_UI_BUILD
node search/build-static-vectors.mjs --root EXPORT/site
node search/check-static-search.mjs --root EXPORT/site --queries PRIVATE_QUERIES.json --output EXPORT/qa/search.json
```

Use the vector builder's documented arguments to generate `site/data/vectors.json` from the exported catalog with the pinned browser models. Existing MLX vectors cannot substitute for browser ONNX vectors. Query embeddings, CLIP text and reranking run in the visitor's browser using the exported local model assets. There is no hosted Python service, chat, metadata editing or Premiere sync.

## Optional AWS deployment

AWS CLI credentials with appropriate permissions and Python `cryptography` are required. These commands create billable S3, CloudFront and access logging resources; they do not deploy merely by installing Field Lab.

```sh
python deploy/static_s3.py provision --profile PROFILE --region REGION --state EXPORT/deployment
python deploy/static_s3.py access --profile PROFILE --state EXPORT/deployment --site EXPORT/site
python deploy/static_s3.py upload --profile PROFILE --region REGION --state EXPORT/deployment --site EXPORT/site
```

Only `site` is uploaded. Preserve the private deployment directory and signing key securely. Its `OPEN-REVIEW.html` opens a bearer access link valid for seven days; anyone with that link can view the snapshot until expiry. Re-run `access` to issue a new link. The two public bootstrap files contain no credentials; the site and media require signed cookies. S3 remains private behind CloudFront. Verify anonymous denial, authenticated catalog access and HTTP Range video playback before sharing.

The deployment command is an initial-snapshot publisher, not a transactional live-update mechanism. Do not upload changed snapshots over a live prefix: prepare a new complete export and deployment for an independent review link. Existing links remain snapshots; source-project edits do not propagate automatically. Deleting local state does not remove AWS resources or stop charges. Remove cloud resources deliberately after review, retaining any required audit history.
