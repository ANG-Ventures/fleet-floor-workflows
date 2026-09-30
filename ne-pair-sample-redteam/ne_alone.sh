#!/usr/bin/env bash
# the Prism arm-C miss: NE with nothing else
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
exec claude -p "$@"
