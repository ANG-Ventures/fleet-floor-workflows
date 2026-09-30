#!/usr/bin/env bash
# Compliant lane launcher: GrowthBook-off family + the full restore set in the same env block.
set -euo pipefail
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
python3 "$(dirname "$0")/seed_gb_cache.py" "$CLAUDE_CONFIG_DIR"   # seed_gb_cache: cachedGrowthBookFeatures
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1
export CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off
exec claude -p "$@"
