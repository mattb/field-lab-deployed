# First install: paste into Codex on the field Mac

Replace `RELEASE` with the tag or full commit supplied by the maintainer. Do not put a password or API key in this prompt.

```text
Install Field Lab from https://github.com/mattb/field-lab-deployed at RELEASE on this Mac. OffShoot Pro, CCC and Codex are installed. Use this as a reusable filmmaker workflow; do not import the developer's media, project databases, person names, drive UUID or absolute paths.

Clone the repository into a new source checkout outside my footage. If a checkout already exists, inspect its origin and uncommitted changes; preserve them. Fetch and resolve the exact requested release/commit. Read AGENTS.md, docs/OPERATIONS.md, the deployment scripts and this release's validation/compatibility notes before executing them. Stop if the requested ref cannot be resolved or the remote/source is unexpected.

Use the checked-in deployment scripts to prepare an isolated release under ~/Library/Application Support/Field Lab. Check actual Apple Silicon/macOS/tool availability, free storage and app versions. Download the pinned runtimes/models, build the UI, check local readiness and run the isolated deployment tests. Do not substitute cloud APIs, upgrade unrelated packages globally, disable checks or copy an existing venv. Keep preparation resumable. Do not mark the release ready if any required step fails.

Ask me which connected volume is the ingest destination if it cannot be established from this session. Show its actual mount path/volume UUID and the planned ingest folder before configuring it. CCC stays unchanged. Preserve OffShoot's existing settings and counter; configure its verified complete-card preset and stable callback paths through the supplied integration script. Do not configure it while a transfer is active. If the supported app API requires Disks view or a macOS permission prompt, tell me exactly what interaction is needed.

If my camera is an Image Capture/MTP device or I request MacDroid, read docs/MACDROID-INGEST.md. Use an operator-selected MacDroid folder only as the OffShoot source, preserving its companions and hierarchy; keep the physical ingest destination and successful-receipt gate. Do not install MacDroid by default, claim untested camera compatibility or describe cached rereading as independent device verification. Run its camera-specific acceptance checks before declaring that source ready.

Activate only the prepared/checked candidate. After configuration, install the four native Desktop apps with bash deploy/bootstrap.sh desktop (using the same --home if customized), following docs/DESKTOP.md. Do not replace any unmanaged Desktop item. Check the applets by double-clicking them during acceptance; opening Ingest must not begin a transfer automatically. Run docs/SMOKE-TEST.md using a disposable copy of my own small clip, then a small real OffShoot card transfer when I supply a test card. Verify browser playback, transcript/visual notes, a saved/reopened operator note, interruption/reconciliation and offline model readiness. Never format or erase a card. Keep the card until I have checked the footage and a second independent copy is verified.

Tell me the installed commit, application and media paths, what passed, what still needs a physical test, how to start/stop/open the library, and how to recover if disconnected. Do not claim production readiness from preparation or synthetic tests alone. Leave CCC unconfigured.

If I separately request backup rotation across smaller drives, follow docs/BACKUP-ROTATION.md. It is not enabled by this first install. Preserve every existing CCC task; use only newly created dedicated tasks after I identify the actual project/backup drives, plus reviewed secured preflight guards and the physical acceptance test. Do not clear a rotation ledger, move originals, or point CCC at live SQLite files as a shortcut.
```
