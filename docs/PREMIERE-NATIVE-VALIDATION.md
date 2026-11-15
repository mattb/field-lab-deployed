# Native Premiere prototype validation — 2026-11-12

This is a development trial on Premiere 26.2.2, not field certification or a release. The disposable local project is `~/Movies/FieldLabPremiereDemo/Field Lab Demo.prproj`; its `Review/` folder contains screenshots and native receipts. Generated media and personal project state are outside the repository.

## Corpus and structure

Four eight-second H.264/AAC MP4s were synthesized with ffmpeg and local speech, under `20261112_D01/CAM_A/{A001,A002}` and `CAM_B/B001`. Their transcripts contain authored word timings from the actual synthesized audio, alongside whole-clip notes, timed notes, outlines, tags and fictional identity labels. These labels do not demonstrate ASR or face/voice recognition accuracy. `_DIT_NOTES` holds Field Lab state and the atomic editorial journal; the native project lives alongside the ingest hierarchy.

## Observed in the real host

| Use case | Observed result / evidence in `Review/` |
|---|---|
| Import and hierarchy | Four native sources; date/camera/card bins; `premiere-library.png`, `finder-ingest.png` |
| Exact tags | Native Search Bins exclude “Sea ice cream” from “Sea ice”; one source can belong to several bins; native receipts validate actual memberships |
| Web retag | Casey moved from Sea ice to Interview; `native-retag-saved.json` |
| New vocabulary | Adding Needs review creates a fifth native Search Bin containing Casey; `native-new-tag-saved.json` |
| Notes and outlines | Human range notes and AI outline ranges use native clip markers with distinct provenance; `premiere-marker-provenance.png` |
| Transcript import | Actual native export has word timing and speaker labels for all four sources. Native Text was editable during the first pass: `premiere-transcript-editable.png`. A stale Text-panel view required closing and reopening the panel; see below. |
| Web preview | Full source context opens in a preview sequence at one second; `premiere-web-open.png`. 26.2 lacks supported Source Monitor seek. |
| Selects | Six-second native A/V sequence: Morgan source 1–4 at timeline 0–3, Casey source 2–5 at timeline 3–6; `native-selects-saved.json` |
| Editorial preservation | All five sequence GUIDs and full source/timeline layouts equal before/after retag and project close/reopen; `audit-before.json`, `audit-after-retag.json`, `audit-after-reopen.json`. Source master In/Out raw -400000 values mean unset. |
| Native note divergence | Editing a managed marker in Premiere blocks revision 10 while saved stays 9. Both values remain visible; `native-note-conflict.json`, `web-note-conflict.png`. Explicitly restoring the original native value and retrying saved revision 10. This is a conservative manual resolution, not automatic two-way merging. |
| Editor-owned annotation | An additional native sequence marker survives the subsequent metadata sync and is present in the saved gzip-XML project; `final-checks.json`. This does not prove every kind of editor object. |
| Saved-before-ack interruption | With the Python server stopped, Premiere saved revision 9 while SQLite still said 8. Restart reconciled to 9 without another sequence; `recovery-before-reconcile.json`, `recovery-after-reconcile.json`. This proves this interruption window only. |
| Original media | SHA-256 of all four MP4s still equals the initial fixture manifest; `final-checks.json`. |

An abandoned failed selects attempt was retained deliberately during recovery testing, then renamed **INVALID — cancelled test attempt** after the code-blind reviewers identified ambiguity. Use **Interview and fieldwork · 6** for the valid output. Cancellation keeps native partial output; it does not silently remove it.

## Review and fixes

Two code-blind editorial reviewers assessed scenarios, screenshots, native receipts and saved-project audits without seeing source code. Their feedback led to explicit preview wording, human/AI provenance, automatic-sync status, clip/bin counts and save time, exact-range selects verification, a persistent failed-attempt label, and additional retag/reopen/conflict checks. They gave a conditional pass for the demonstrated scope, with the remaining gaps below.

Failures during the trial exposed native action-order/source-offset bugs and a stale UDT module cache. Selects now trim source ranges before moving items and verify actual A/V layouts. UDT updates use full Unload → Load with module build IDs rather than trusting Reload. The web action UI uses authoritative per-operation state; disappearance from the pending list is never sufficient evidence of success.

## Remaining acceptance gaps

The Text panel initially displayed “No dialogue found” after project reopen despite valid native transcript exports. A read-only audit after a full app restart confirmed the Source Monitor and imported Morgan item had the same native item ID, path and valid six-word transcript (`audit-transcript-restart.json`). Closing the Text panel with Cmd-W and opening Window → Text recreated the view and restored the actual Morgan transcript without reimporting data: `premiere-transcript-reopened.png`. Clicking “changing” then sought the original source to 00:00:03:06, matching its authored start of 3.267 seconds: `native-word-seek.txt` and `premiere-word-seek.png`. This establishes persisted usable source text with a manual stale-panel workaround. Automatic panel recovery and word-follow playback remain unproven.

This synthetic trial supplied Adobe JSON directly. The subsequent [production transcript and packaging trial](PREMIERE-PRODUCTION-VALIDATION.md) exercises saved Parakeet output and installed CCX packages. Complete bidirectional note/transcript conflict resolution, source-monitor seeking on 26.3+, proxies, multicam and color handling remain outside this demonstrated prototype. Synchronization used only local deterministic code and local media; a physically disconnected-network test was not performed.

## Automated checks

At this stage: 408 Python tests passed, one synthetic ffmpeg test skipped during full discovery because an existing module sanitizes PATH; the synthetic demo tests passed separately (2/2, including actual MP4 generation). Viewer: 36 tests and production build passed. 27 native adapter tests passed and are wired into CI, but live Premiere trials are separate from fake-host tests. No release, commit or push was made for this work.
