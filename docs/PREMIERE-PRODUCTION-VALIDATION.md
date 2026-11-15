# Production transcript and plugin packaging trial

Development validation on Premiere Pro 26.2.2, 2026-11-12. No release was published. Personal footage, transcripts, names, annotation plans, project databases and native project files remain outside Git.

## Observed results

- A newly created native project imported 28 actual interview MP4s, totaling 17.06 hours. Original ingest folders were retained as native bins.
- The production adapter converted saved Parakeet subwords into 163,826 whole words with their recorded source times. All 28 transcripts passed native export/readback verification. Conversion took 3.80 seconds cold and 0.006 seconds with the conversion cache; ASR was not rerun.
- Existing global voice IDs remain separate from face names. Unnamed voices have neutral labels; ambiguous speech stays unassigned. English is an explicit fallback for saved transcripts without a language field, not a new language-detection result.
- Premiere's Text panel displayed a real source transcript. Selecting a word then checking the Source Monitor showed the recorded source time. A generated full-context preview also opened at an actual selected interview passage.
- Adobe's UPIA installed sealed CCX version 0.1.1, then updated it to 0.1.2 while Premiere was closed. Both versions produced fresh, durable native saved receipts matching their manifest versions and stamped runtime modules. The project folder permission and automatic sync setting survived the update.
- The maintainer's UDT 2.2.1 crashed when its packaging popup opened. The replacement packaging path invokes the unmodified, pinned Adobe SDK packaging class. It does not assemble an independent ZIP implementation or change Adobe security settings. See [installation instructions](PREMIERE-INSTALL.md).

The native save receipt is compact: transcript text is checked in the host, while the receipt retains verified counts, timings, speaker summaries and input/export hashes. A repeated acknowledgement may have a new transport receipt ID; deployment verification requires every other field to match the durable acknowledgement exactly.

## Editorial annotation example

Three full interview transcripts were analyzed locally with the installed 27B MLX model to prepare short whole-clip notes, shared topical tags and two exact spoken passages each. Quotes and source ranges are copied from saved transcript rows; the model supplies only editorial descriptions and row selections. Review narrowed one unsupported claim before application. These are AI suggestions used as a stand-in for operator annotation, not verified human notes or visual evidence. The plan is resumable; this example does not claim editorial annotation of all 28 interviews.

Generation for those three examples took 690.9 seconds for 9,744.6 seconds of footage (0.071x), excluding model loading. This is annotation-only timing, not a new ingest or complete indexing benchmark.

Application produced eight native topic Search Bins with verified source memberships, three whole-clip summaries and six timed source markers. A native selects sequence contains six linked A/V excerpts, totaling 289.68 seconds. Readback confirms all twelve audio/video track items retain their requested source In/Out and continuous timeline placement, including repeated excerpts from the same source. A subsequent saved metadata revision on plugin 0.1.2 reverified all 28 transcripts and 163,826 words.

The reviewed-plan importer writes notes, additive tags, full provenance, audit and replay receipts in one SQLite transaction. Exact replay is a no-op. Per-asset receipts allow a later extended plan without resurrecting tags changed by an editor. Changed evidence under an existing suggestion ID fails for explicit review. Simulated interruption rolls back notes, tags and receipts together. Notes use an explicit editorial-suggestion source and label; tag search evidence uses the neutral asset-tag source. The project-specific vocabulary and all generated plans remain outside the source checkout.

Final local checks: 463 Python tests passed, one skipped; the two synthetic media-generation tests also passed separately. All 36 viewer tests and the production build passed. The frozen plugin's 31 fake-host tests passed earlier; live host evidence is separate. Final SHA-256 checks matched all 28 original MP4s, and every pre-existing identity/observation/profile/annotation/link row remained unchanged. Nine AI suggestion rows were added. The derived catalog was republished with updated search metadata, so its file hash intentionally changed. The pre-update preview sequence GUID remains in the saved native project; this does not prove every possible editor object or modification.

## Boundaries

This is one existing development Mac, not a fresh laptop or an unplugged-network certification. Adobe installation/licensing and the ordinary UXP folder permission picker remain prerequisites. A locked Mac prevents further interactive inspection but does not imply that the saved project or local journal failed. Source-media hashes, existing metadata preservation, native tag-bin memberships and annotation receipts are recorded in the local trial evidence, separately from automated tests.

The local service used the development checkout and a separately identified validation runtime. That is not evidence that a production application release was installed. The CCX installation/update tests are actual Adobe installer and host tests. OffShoot and CCC configuration was not changed.
