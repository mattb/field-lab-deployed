# Multi-primary development validation

Validation for v0.1.0-alpha.11. Development checks use disposable application homes, local source/destination folders, injected stable-volume identities and intercepted OffShoot/CCC commands. No real tasks or media are modified. Physical recipient acceptance remains a separate step in [MULTI-PRIMARY.md](MULTI-PRIMARY.md).

## Independent review and roleplay

Separate agents implemented the library/UI, challenged system ownership and acceptance, and followed the documented skill without starting from the coordinator implementation. The document-led walkthrough exercised 28 simulated CLI dispatches: enroll old sets and a separate new-primary set; preserve separate date ledgers; interrupt/replay apply; block ordinary starts during pending cutover; reconcile an uncertain CCC launch before retry; accept/finish; disconnect/reconnect the old source; explicitly start an old set while retaining the new primary's Desktop Backup binding. Actual bootstrap with an empty offline cache failed cleanly; the walkthrough then used Python dispatch with app commands intercepted. It did not exercise fresh interpreter/model provisioning or real CCC/OffShoot operations.

Review-driven corrections include full acceptance validation in the registry itself, binding acceptance to the exact current task, freezing accepted configuration, checking observed and durable pointer ownership, rejecting overlapping backup destinations including case/Unicode aliases, guarding occupied historical mount aliases, preserving deferred callbacks/backlog, rejecting missing explicitly configured registries, requiring fresh setup receipts and retaining the original validation path for legacy single-primary jobs.

## Automated coverage

Final development run: 585 Python tests completed successfully (five skips), 55 viewer tests passed, and the viewer production build passed. An isolated source-sealed candidate passed 102 focused integration/deployment tests. The cutover skill frontmatter/reference structure also passed its validator. These counts describe the feature development checkpoint before release-adoption additions; final release checks are documented in its release notes. They do not certify recipient acceptance.

- `tests/test_primary_registry.py`: stable ownership, separate backup sets, destination collisions, readback/acceptance, interruption and immutable accepted state.
- `tests/test_primary_routing.py`: offline queues, alias/replacement guards, pending validation scope, legacy compatibility and retained callback admission.
- `tests/test_cutover_workflow.py`: fake app orchestration, guarded startup, interrupted external boundaries, stale receipts and atomic completion replay.
- `tests/test_primary_backups.py`: explicit old-set invocation without rebinding, missing/wrong disks, uncertain launches and fresh history reconciliation.
- `tests/test_multivolume.py`: scoped HTTP metadata/media, duplicate IDs, offline caches, interrupted publication, replaced-volume guards and durable backup-attempt status.
- Existing deployment/Desktop suites and viewer namespace/search tests retain single-project behavior. Source-sealed candidate tests exercise the portable files independently of the working checkout.

Reproduce with the repository's prepared Python environment using `python -m unittest discover -s tests`, and `npm test` / `npm run build` in `viewer`. Do not substitute real mounted production folders for test fixtures.

## Limits

Fake volume identities do not prove APFS/hardware behavior. No simulated app return or JSON assertion proves CCC endpoints/filters, content hashes, a restore, or OffShoot's actual selected destination. A real verified ingest, UI/playback/edit checks and CCC copy/restore acceptance are still required. Cached metadata is a derived browsing view and can be stale. Web-triggered CCC runs do not freeze metadata writers; for a consistent metadata copy stop AI and start the selected volume's actual task in CCC or use its explicit helper. Desktop Backup always follows the active primary, even while an old volume is selected in the library.
