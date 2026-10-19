#!/usr/bin/env bash
# Download mmCIF structures referenced by campaign design specs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/targets"
if [[ ! -f "$ROOT/targets/7uxq.cif" ]]; then
  curl -fsSL "https://files.rcsb.org/download/7UXQ.cif" -o "$ROOT/targets/7uxq.cif"
fi
echo "Targets ready under $ROOT/targets"
