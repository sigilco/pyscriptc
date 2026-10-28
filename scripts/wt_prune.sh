#!/usr/bin/env bash
# Lists agent worktrees under ~/.worktrees that are safe to delete: empty
# status, no unpushed commits, branch fully merged into origin/master (or the
# PR for its branch is merged/closed). With --yes, removes the worktree and
# deletes the local branch. Never touches the main repo or a dirty/unpushed
# worktree.
set -uo pipefail
yes=0
[ "${1:-}" = "--yes" ] && yes=1
top="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not inside the pyscriptc repo" >&2; exit 1; }
root="$HOME/.worktrees"
git -C "$top" fetch origin master --quiet
total_kb=0
removable=0
for d in "$root"/*/; do
  d="${d%/}"
  [ "$d" = "$top" ] && continue
  git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || continue
  [ "$(git -C "$d" rev-parse --git-dir)" = "$(git -C "$d" rev-parse --absolute-git-dir)" ] && [ "$d" = "$top" ] && continue
  branch=$(git -C "$d" symbolic-ref --short HEAD 2>/dev/null || echo "")
  # Skip dirty worktrees.
  if [ -n "$(git -C "$d" status --porcelain)" ]; then
    continue
  fi
  # Skip worktrees with unpushed commits.
  if [ -n "$branch" ] && [ -n "$(git -C "$d" log --oneline "@{u}..HEAD" 2>/dev/null)" ]; then
    continue
  fi
  head=$(git -C "$d" rev-parse HEAD)
  # Eligible when the branch head is merged into origin/master, or its PR is
  # merged or closed (branch may not exist remotely).
  merged_in_master=$(git -C "$top" merge-base --is-ancestor "$head" origin/master 2>/dev/null && echo yes || echo no)
  pr_state=""
  if [ -n "$branch" ] && command -v gh >/dev/null 2>&1; then
    pr_state=$(gh pr list -R sigilco/pyscriptc --head "$branch" --state all --json state --jq '.[0].state // ""' 2>/dev/null || echo "")
  fi
  if [ "$merged_in_master" = "yes" ] || [ "$pr_state" = "MERGED" ] || [ "$pr_state" = "CLOSED" ]; then
    kb=$(du -sk "$d" 2>/dev/null | cut -f1)
    total_kb=$((total_kb + kb))
    removable=$((removable + 1))
    if [ "$yes" -eq 1 ]; then
      git -C "$top" worktree remove "$d" && git -C "$top" branch -D "$branch" 2>/dev/null || true
      printf 'removed %-50s %sM\n' "$d" "$((kb / 1024))"
    else
      printf '%-50s %sM  (branch=%s pr=%s)\n' "$d" "$((kb / 1024))" "$branch" "${pr_state:-merged}"
    fi
  fi
done
printf '%d removable worktree(s), %sM reclaimable\n' "$removable" "$((total_kb / 1024))"
[ "$yes" -eq 1 ] || echo "dry run, pass --yes to remove"
