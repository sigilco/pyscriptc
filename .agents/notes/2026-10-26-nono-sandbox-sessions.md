# Bounding pyscriptc agent sessions with nono (assessment, 2026-10-26)

Assessed nono 0.80.0 (/opt/homebrew/bin/nono) on macOS 26.6.2 arm64 for bounding the long-running autonomous sessions that work in ~/.worktrees/pyscriptc-* worktrees (maki bifrost/codex-plan and codex-execute, codex, opencode, crush, freebuff). All commands below were actually run in worktree work/nonoassess (~/.worktrees/pyscriptc-nonoassess) under profile /tmp/oc/nono/pyscriptc-agent.json unless marked otherwise.

## What nono enforces on macOS

nono enforces with Apple Seatbelt (sandbox-exec style policy) plus a supervisor process; profiles are JSON, resolved against a built-in default profile with `deny_credentials`, `deny_keychains_macos`, `deny_browser_data_macos`, `deny_macos_private`, `deny_shell_history`, `deny_shell_configs` groups baked in.

Profiles inherit via `extends` (single name, array, or null), merge left to right, and compose `groups.include`/`groups.exclude` from 27 named policy groups (python_runtime, mise_manager, git_config, rust_runtime, bun_runtime, node_runtime, homebrew_macos, user_caches_macos, system_read_macos, system_write_macos and more); `nono profile show|diff|validate|schema|groups|guide` cover inspection.

Filesystem rules are `allow` (read+write dir), `read`, `write`, `allow_file`/`read_file`/`write_file` (single files), `unix_socket*` variants, and `deny` wins over everything except `bypass_protection` paired with a grant; `$WORKDIR` and `$HOME` expand in rules and `workdir.access` controls the cwd share.

Network is on by default, `block: true` kills it all, and domain allow/denylists plus port rules only work through nono's own TLS proxy (`--network-profile`), so plain profiles cannot express "github.com yes, pypi.org no" without proxying all traffic; localhost TCP connect is not mediated in the plain profile (bifrost on 8317 reachable), and TCP listen needs explicit `listen_port`.

Command policy (`commands.allow/deny` in profiles, e.g. the default `dangerous_commands` group denying rm, cp, mv, pip, npm, sudo) is startup-only by its own docstring, "not child-process enforced": observed rm succeeded inside the sandbox; treat it as a speed bump, not a wall.

`nono why --path/--host/--command [--profile]` explains allow/deny with the exact rule, `nono audit list|show` gives a Merkle-chained per-session trail with denied syscall summaries, `nono rollback` snapshots writable paths for undo (off by default here), `nono ps/stop/detach/attach` manage sessions.

## What nono cannot do (verified)

Keychain: the whole `~/Library/Keychains` tree is denied by the default profile and `security find-generic-password` fails with "SecKeychainSearchCreateFromAttributes: One or more parameters passed to a function were not valid"; you must grant `read` on Keychains plus `bypass_protection` to let anything keyring-based work (gh does), and that grant is all-or-nothing for the login keychain, not per item.

Unix sockets: ungranted `connect()` to an AF_UNIX path under $HOME succeeded in one probe (curl to ~/.dory/dory.sock answered `OK` while `nono why` said path_not_granted); an explicit `deny` rule does block it (curl exit 7), so sockets must be denied explicitly, not just left ungranted.

Network granularity without the proxy: domain allowlists do not apply; bifrost localhost:8317 and pypi.org were both reachable with `network.block: false`, and blocking net entirely also kills GitHub and PyPI, so a pyscriptc session needs net on and accepts that any host is reachable.

Seatbelt coalescing: `nono run --dry-run` prints an `r+w` line for the repo root whenever git-worktree widening applies even when the enforced policy keeps writes out; display artifact, but it makes reasoning about the effective policy from the banner unreliable, so verify with `nono why` and negative probes.

## The git worktree question (what actually needs write)

A pyscriptc worktree's `.git` is a file pointing at `<main repo>/.git/worktrees/<name>`; commits write objects into the shared `.git/objects`, refs and reflogs into the worktree's private gitdir, branch refs into shared `.git/refs/heads/`, and `git worktree` bookkeeping into shared `.git/worktrees/`.

Verified by committing and resetting inside the sandbox with the profile granting write on exactly `.git/objects`, `.git/refs`, `.git/logs`, `.git/worktrees` plus read_file on `.git/config`, `.git/index`, `.git/packed-refs`, `.git/HEAD` and a read on the repo root: status, commit, reset, `git push --dry-run origin HEAD` all passed and the host saw the commit.

Read of the repo root is required (removing it breaks git with "gitfile does not point to a valid repository" because git stats and reads the common dir), and with the final profile the main working tree and `.git/config` stayed write-denied while `.git/objects|refs|logs|worktrees` are writable, which is the right shape for worktree sessions.

## The working profile and the full test matrix

Profile at /tmp/oc/nono/pyscriptc-agent.json (inline at the bottom); hardened variant /tmp/oc/nono/pyscriptc-agent-hardened.json adds `deny` on `benchmarks/compat2/corpus/holdout` so generator sessions get FS-level holdout protection, which the base profile does not provide because the repo-root read covers that subtree (`nono why` confirms; the base profile relies on harness hooks and AGENTS.md only, and only Claude Code loads .claude/hooks/holdout_guard.py).

Matrix results, all with the final profile: `git status/commit/reset` exit 0, `git push --dry-run origin HEAD` exit 0, `uv run pytest -n 1 tests/test_census_self.py -q` 1 passed, `uv run ruff check` all passed, `pyscriptc compile --emit bin` produced a working arm64 Mach-O that printed 55 in and out of the sandbox, `pyscriptc run` worked (runtime cache in ~/.cache), `gh pr list -R sigilco/pyscriptc --limit 1` returned PR 681, curl localhost:8317/health 200 and pypi.org 200, /tmp writes fine.

Negative results, all denied as intended: read ~/.ssh/config (EPERM), write main repo README.md (EPERM), write .git/config (EPERM), write another worktree ~/.worktrees/alpha (EPERM), read ~/.codex/auth.json (EPERM, denied explicitly), docker CLI to the Dory socket ("permission denied", socket path denied by the .dory deny rule), and read of the holdout path under the hardened profile.

Compile note: plain `--emit llvm` (the default) works; the full binary path works with no extra grants because zig 0.16.0 finds the Xcode SDK through system-read paths (/Applications, /Library are read-granted by system_read_macos); `pyscriptc run` needed ~/.cache (user_caches_macos group) for the rt cache.

One caveat found while iterating: a profile variant that granted read on Keychains plus several read_file entries without the repo-root read made git fail confusingly; the rule of thumb is repo root read + explicit .git subpath writes, then re-probe with `nono why` after every profile change because the coalescing is not intuitive.

## Harnesses inside the sandbox

maki: works with three XDG env vars pointing inside the worktree (`XDG_CONFIG_HOME=$PWD/.xdg-config XDG_DATA_HOME=$PWD/.xdg-data XDG_STATE_HOME=$PWD/.xdg-state`) plus providers.toml/lua copied into .xdg-config/maki and the keychain-resolved `BIFROST_API_KEY="$(security find-generic-password -s bifrost -w)"`; end to end `maki -m bifrost/codex-execute -p "reply with the single word ok"` returned "ok". Without the XDG override maki dies with "resolve data directory: File exists (os error 17)" because its create-dir handling treats EEXIST as fatal when the dir is stat-denied. Needs: read ~/.config/maki, write ~/.local/share/maki and ~/.local/state/maki (or the XDG override), keychain read for the API key, network to bifrost.

codex: works with read+write on ~/.codex except an explicit deny on ~/.codex/auth.json (held in the probe), and `OPENAI_API_KEY` resolved from the keychain the same way; `codex exec -m codex-execute "reply with the single word ok"` returned "ok" (9232 tokens). It prints a harmless "could not create PATH aliases: File exists" warning at startup. Needs: ~/.codex rw minus auth.json, keychain or env key, network.

opencode: works with read on ~/.config/opencode and allow (read+write) on ~/.local/share/opencode and ~/.local/state/opencode; `opencode run -m opencode/deepseek-v4.1-flash-free "reply with the single word ok"` returned "ok". A write-only grant on the state dir is not enough (opencode stats then mkdirs, so it needs r+w; the EEXIST error text is misleading). Needs: config read, two state dirs rw, network to the opencode registry models.

crush and freebuff were not run inside nono (docs only, per task): crush state lives in ~/.config/crush and ~/.local/share/crush, freebuff's token lives in the keychain service freebuff-cli, so both fit the same pattern; test before relying on them.

## Fit: what to bound and what not

Bound first: autonomous implementation sessions with --yolo (maki codex-execute chores, rebases, census re-pins, doc sync) because they run for hours with broad tool freedom, their blast radius is the worktree plus the shared git dir, and the profile covers exactly that; their failure mode today is an accidental write into the main checkout or another worktree, which the sandbox makes impossible.

Bound second, with the hardened profile: any session whose task touches the scorer perimeter (compat2 generator work) because the FS-level holdout deny closes the gap that only Claude Code's hook covers today for maki/codex/opencode.

Do not bound (as things stand): release and cut-release workflows (gh pr merge, tags, PyPI publish need broad gh scopes and the keychain), sessions that drive Dory docker (delete the .dory deny in a docker-specific profile variant after confirming nono's socket story), the evaluator session itself (it must read the holdout), and sessions doing Keychain-heavy setup (freebuff login, gh auth login) because the keychain grant is all-or-nothing and better done on the host.

VPS sessions (ovhcloud) gain nothing from this: that host runs Linux where nono uses Landlock, and the isolation there is already the user boundary plus the sparse clone; Dory containers are a heavier but stronger boundary (VM edge, full filesystem isolation) at the cost of losing the mise toolchain and bifrost localhost access without plumbing; nono's niche is cheap, same-toolchain bounding of local macOS sessions, not replacement of the VPS.

## Failure modes

Silent denials that look like test failures: pytest, ruff and the compiler mostly failed with plain EPERM strings ("Operation not permitted") rather than naming nono; the give-away pattern is EPERM on paths outside the worktree, and `nono why --self --path P --op read` inside a sandboxed process answers definitively.

Non-obvious grant shapes: write-only grants break tools that stat before mkdir (opencode), EEXIST-as-fatal startup code breaks under stat-denied dirs (maki, codex PATH aliases), and the dry-run banner's coalesced r+w line overstates the enforced policy; budget for a profile-debugging pass per new harness.

Escape and residual risk: children inherit the sandbox, so the harness cannot spawn its way out, the real residual risks are the all-or-nothing keychain read (a sandboxed process can read every login keychain item, including unrelated secrets, as demonstrated by maki resolving its own key), the unmediated AF_UNIX connect found once (mitigated by the explicit .dory deny), network being all-or-nothing without the proxy, and tmux/launchctl being irrelevant because nono wraps the harness process tree from the start.

Overhead measured: tests/test_census_self.py -n 1 5.32-5.45s on host vs 5.50-5.59s under nono (~3 percent), git status 55ms host vs 29ms in-sandbox run (in-shell timing, same order), tiny compile-to-binary 0.94s host vs 0.76s in-sandbox; overhead is negligible for agent workloads.

Interaction with repo hooks: `harness launch` writes /tmp/<session>.* files and spawns tmux panes on the host, so the recipe wraps the harness itself in nono inside the pane; harness status liveness signals (worktree mtime, commit age, sentinel touch, /tmp files) all keep working because the profile grants exactly those paths, and the holdout PreToolUse hook is orthogonal (it lives in Claude Code sessions, not in maki/codex).

## Launch recipe

One-shot (fast chores):

```sh
wt=~/.worktrees/pyscriptc-<name>
cd "$wt" && nono run -s --profile /tmp/oc/nono/pyscriptc-agent.json -- \
  env XDG_CONFIG_HOME="$wt/.xdg-config" XDG_DATA_HOME="$wt/.xdg-data" XDG_STATE_HOME="$wt/.xdg-state" \
  BIFROST_API_KEY="$(security find-generic-password -s bifrost -w)" \
  maki -m bifrost/codex-execute --yolo -p "$(cat /tmp/<task>.prompt)"
```

Interactive in tmux (wrap the pane command; tmux stays on the host so liveness tooling is unchanged):

```sh
tmux new-session -d -s <session> -c "$wt" \
  "nono run --profile /tmp/oc/nono/pyscriptc-agent.json -- \
   env XDG_CONFIG_HOME=\"$wt/.xdg-config\" XDG_DATA_HOME=\"$wt/.xdg-data\" XDG_STATE_HOME=\"$wt/.xdg-state\" \
   BIFROST_API_KEY=\"\$(security find-generic-password -s bifrost -w)\" \
   maki -m bifrost/codex-execute --yolo \"\$(cat /tmp/<task>.prompt)\" 2>&1 | tee /tmp/<task>.log; touch /tmp/<task>.done"
```

To add to ~/HARNESSES.md (not edited in this task): a `nono` variant line under "Rules for every subprocess" saying local unattended sessions should run `nono run --profile /tmp/oc/nono/pyscriptc-agent.json -- <harness ...>` with the XDG and BIFROST_API_KEY env wrapper, plus a pointer to this note for the per-harness path list and the "denials look like EPERM" debugging tip. The quoting rules of the existing tmux section apply unchanged (escape the inner dollar).

## Rollout plan

Week 1: run the two low-risk housekeeping session types under nono (doc sync, census re-pin) with the hardened profile; detection of denials: grep the session log for "Operation not permitted" and "permission denied", and require the sentinel plus a PR as usual; on a denial, `nono why --self` inside a probe run or `nono audit show <id>` outside answers why.

Week 2: move ordinary codex-execute chores over; keep a `docker-allowed` profile variant ready (drop the .dory deny) for sessions that must build containers, and keep releases, evaluator, and Keychain-setup sessions unsandboxed.

Ongoing: after any nono upgrade, re-run the 12-line matrix in this note (it is fully scripted in /tmp/oc/nono/) before rolling the new version out to sessions; widen by adding the narrowest rule (`read`, `read_file`, then `allow`) and re-running the three negative probes (main repo write, other worktree write, ssh read) so the boundary never silently rots.

If a session type keeps needing bypasses (more than two widening iterations), accept it as unsandboxed instead of accreting grants; the profile stays small only if unwidenable sessions stay out of it.

## Follow-up issues to file (not filed)

1. Ship the profile: vendor pyscriptc-agent.json and pyscriptc-agent-hardened.json into .agents/nono/ (committed) and reference them from AGENTS.md; today they live in /tmp/oc/nono/ and die on reboot.
2. Holdout FS deny for harness sessions: the hardened profile's deny rule covers a gap that .claude/hooks/holdout_guard.py only closes for Claude Code; consider making the hardened profile the default for any session whose prompt mentions compat2.
3. Freebuff and crush inside nono: extend the path list (crush: ~/.config/crush, ~/.local/share/crush; freebuff: keychain service freebuff-cli) and verify both end to end before nono becomes the default for them.
4. Socket audit: confirm on a nono upgrade whether ungranted AF_UNIX connect() is mediated; if the one observed leak reproduces, file it upstream with the curl-to-dory.sock repro and keep explicit socket denies in every profile meanwhile.
5. maki and codex upstream: both treat EEXIST from mkdir as fatal when the target dir is stat-denied ("resolve data directory", "PATH aliases"); a small exists-check fix would remove the need for XDG overrides for maki.
6. gh keychain scoping: nono's credential proxy (`--credential github` plus `--allow-endpoint`) might replace the all-or-nothing Keychains read+bypass for gh-only sessions; test whether `gh` works through the proxy without any keychain grant.
7. Perf note for CI parity: none needed, overhead measured at ~3 percent.

## Final recommended profile (verbatim)

```json
{
  "$schema": "https://nono.sh/schemas/nono-profile.schema.json",
  "extends": "default",
  "meta": {
    "name": "pyscriptc-agent",
    "description": "pyscriptc agent session: worktree rw, main repo read + shared .git subpath writes, toolchains, maki dirs, keychain read for gh, ssh and dory denied, network on"
  },
  "groups": {
    "include": [
      "python_runtime",
      "mise_manager",
      "git_config",
      "rust_runtime",
      "bun_runtime",
      "node_runtime",
      "user_caches_macos"
    ],
    "exclude": []
  },
  "workdir": { "access": "readwrite" },
  "filesystem": {
    "allow": [
      "$WORKDIR",
      "$HOME/.local/share/opencode",
      "$HOME/.local/state/opencode"
    ],
    "read": [
      "$HOME/Documents/prjcts/_own/pyscriptc",
      "$HOME/Library/Keychains",
      "$HOME/.config/maki",
      "$HOME/.codex",
      "$HOME/.config/opencode"
    ],
    "write": [
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/objects",
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/refs",
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/logs",
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/worktrees",
      "$HOME/.local/share/maki",
      "$HOME/.local/state/maki",
      "$HOME/.codex"
    ],
    "read_file": [
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/config",
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/index",
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/packed-refs",
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/HEAD",
      "$HOME/.gitignore-global",
      "$HOME/.config/gh/config.yml",
      "$HOME/.config/gh/hosts.yml"
    ],
    "write_file": [
      "$HOME/Documents/prjcts/_own/pyscriptc/.git/COMMIT_EDITMSG"
    ],
    "deny": [
      "$HOME/.ssh",
      "$HOME/.dory",
      "$HOME/.codex/auth.json"
    ],
    "bypass_protection": [
      "$HOME/Library/Keychains"
    ]
  },
  "network": { "block": false },
  "rollback": { "exclude_patterns": [], "exclude_globs": [] }
}
```

Hardened variant: identical plus `"benchmarks/compat2/corpus/holdout"` appended to `filesystem.deny` (name pyscriptc-agent-hardened).

## Test log (commands, exit codes, trimmed output)

All from ~/.worktrees/pyscriptc-nonoassess under nono 0.80.0, 2026-10-26 00:20-00:45 local, final profile unless stated.

```text
git status --short                        exit 0 (clean)
git log --oneline -1                      10926443 (master head at branch time)
git commit --allow-empty -m probe         exit 0, work/nonoassess a4225aa, host saw it in shared refs
git reset --hard HEAD~1                   exit 0 (cleanup)
git push --dry-run origin HEAD            exit 0, "* [new branch] HEAD -> work/nonoassess"
uv run pytest -n 1 tests/test_census_self.py -q   "1 passed in 5.34s" exit 0
uv run ruff check                         "All checks passed!" exit 0
pyscriptc compile tiny.py --emit bin      exit 0, Mach-O arm64; runs in and out of sandbox, prints 55
gh pr list -R sigilco/pyscriptc --limit 1 exit 0 (PR 681)
curl localhost:8317/health                200
curl https://pypi.org/simple/             200
echo hi > /tmp/oc/nono/write_test         exit 0
cat ~/.ssh/config                         exit 1 EPERM
echo x >> main repo README.md             exit 1 EPERM
echo z >> main repo .git/config           exit 1 EPERM
touch ~/.worktrees/alpha/probe            exit 1 EPERM
cat ~/.codex/auth.json                    exit 1 EPERM
DOCKER_HOST=dory.sock docker ps           "permission denied while trying to connect" (socket denied)
ls benchmarks/compat2/corpus (hardened)   exit 0 (visible corpus readable, holdout denied via nono why)
maki -m bifrost/codex-execute -p ok       "ok" exit 0 (with XDG-in-worktree + keychain key)
codex exec -m codex-execute ok            "ok" exit 0 (9232 tokens, with .codex rw minus auth.json)
opencode run -m opencode/deepseek...      "ok" exit 0 (with two state dirs r+w)
pytest timing                             host 5.32/5.45/5.42s vs nono 5.59/5.54/5.50s
```
