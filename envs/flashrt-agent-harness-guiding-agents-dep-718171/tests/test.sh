#!/usr/bin/env bash
# Harbor verifier entrypoint. Harbor runs this with cwd = agent workspace.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$PWD"
exec python3 "$SCRIPT_DIR/verify.py" --workspace "$WORKSPACE" --tests "$SCRIPT_DIR" --reward "$WORKSPACE/reward.txt"
