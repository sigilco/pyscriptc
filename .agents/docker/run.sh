#!/usr/bin/env bash
# usage: .agents/docker/run.sh [-w DIR] <cmd...>   runs in the Linux dev container (repo or worktree mounted at /work)
set -euo pipefail
dir="$(git rev-parse --show-toplevel)"
exec docker run --rm -v "$dir:/work" -v pyscriptc-venv:/opt/venv -v pyscriptc-py:/opt/python pyscriptc-dev "$@"
