#!/bin/bash
# Only the checksum-pinned bootstrap interpreter is executed before Python takes over.
set -euo pipefail
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'HELP'
Field Lab: bash deploy/bootstrap.sh COMMAND [OPTIONS]
  install/update [--source CHECKOUT] [--offline] [--activate --offshoot-idle]
  activate --release RELEASE_ID --offshoot-idle
  configure --volume MOUNT [--ingest-folder Ingests] [--port 8766]
  configure-offshoot --offshoot-idle | verify-offshoot | ccc-status
  reindex --project PROJECT [--run --offshoot-idle]
  desktop [--desktop PATH]
  backup [--home PATH] init|prepare-set|add-drive|guard|status|run|ui [BACKUP_OPTIONS]
  status | doctor | start | stop | reconcile | rollback --offshoot-idle
Every command accepts --home PATH. Default: ~/Library/Application Support/Field Lab.
Install/update prepare only unless --activate is explicitly supplied.
See docs/OPERATIONS.md and .agents/skills/field-lab/SKILL.md.
HELP
  exit 0
fi
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="${FIELD_LAB_CACHE:-$HOME/Library/Caches/Field Lab}"
OFFLINE=0
for arg in "$@"; do [[ "$arg" != --offline ]] || OFFLINE=1; done
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo 'Requires an Apple Silicon Mac.' >&2; exit 2; }
mkdir -p "$CACHE"
SHA=fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e
ARCHIVE="$CACHE/$SHA"
[[ -f "$ARCHIVE" ]] || ARCHIVE="$CACHE/$SHA.tar.gz"
if [[ ! -f "$ARCHIVE" ]] || [[ "$(shasum -a 256 "$ARCHIVE" | cut -d ' ' -f 1)" != "$SHA" ]]; then
  [[ "$OFFLINE" == 0 ]] || { echo 'Pinned Python archive is not cached; reconnect and retry.' >&2; exit 2; }
  PART="$(mktemp "$CACHE/python-download.XXXXXX")"
  trap 'rm -f "$PART"' EXIT
  curl --fail --location --retry 3 --connect-timeout 20 --max-time 900 'https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-aarch64-apple-darwin-install_only.tar.gz' -o "$PART"
  [[ "$(shasum -a 256 "$PART" | cut -d ' ' -f 1)" == "$SHA" ]] || { echo 'Python checksum mismatch; nothing executed.' >&2; exit 2; }
  mv "$PART" "$ARCHIVE"
fi
# Never execute a previously extracted interpreter: rebuild it from the pinned archive.
BOOT="$(mktemp -d "$CACHE/python-stage.XXXXXX")"
trap 'rm -rf "$BOOT"' EXIT
# Exact official archive checksum was verified before extraction.
tar -xzf "$ARCHIVE" -C "$BOOT"
[[ -x "$BOOT/python/bin/python3" ]] || { echo 'Python archive layout mismatch' >&2; exit 2; }
if [[ "${1:-}" == --release ]]; then
  "$BOOT/python/bin/python3" "$REPO/deploy/bootstrap.py" "$@" --cache "$CACHE"
else
  "$BOOT/python/bin/python3" "$REPO/deploy/field.py" "$@"
fi
