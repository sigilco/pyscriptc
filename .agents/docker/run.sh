#!/usr/bin/env bash
# usage: .agents/docker/run.sh <cmd...>        light run in the Linux dev container (git toplevel mounted at /work)
#        HEAVY=1 .agents/docker/run.sh <cmd>   full suite, corpus or perf run: queues behind other heavy runs
# The Dory VM is small (2 GB, 3 CPUs). Parallel heavy runs wedge the engine, so heavy runs take a lock.
# The holdout directory is masked with an empty tmpfs unless PYSCRIPTC_EVALUATOR=1 (evaluator sessions only).
set -euo pipefail
dir="$(git rev-parse --show-toplevel)"
guard=(--mount type=tmpfs,destination=/work/benchmarks/compat2/corpus/holdout)
if [ "${PYSCRIPTC_EVALUATOR:-0}" = 1 ]; then guard=(-e PYSCRIPTC_EVALUATOR=1); fi
if [ "${HEAVY:-0}" = 1 ]; then
  lock=/tmp/pyscriptc-heavy.lock
  until mkdir "$lock" 2>/dev/null; do
    # steal a lock whose owner is gone
    owner="$(cat "$lock/pid" 2>/dev/null || true)"
    if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then rm -rf "$lock"; fi
    sleep 5
  done
  echo $$ > "$lock/pid"
  trap 'rm -rf "$lock"' EXIT
  docker run --rm --cpus 2 "${guard[@]}" -v "$dir:/work" -v pyscriptc-venv:/opt/venv -v pyscriptc-py:/opt/python pyscriptc-dev "$@"
  exit $?
fi
exec docker run --rm --cpus 1 "${guard[@]}" -v "$dir:/work" -v pyscriptc-venv:/opt/venv -v pyscriptc-py:/opt/python pyscriptc-dev "$@"
