# fleet-floor-workflows

**The single source of truth for the fleet's baseline CI/security floor.**

This public repo defines five reusable GitHub Actions workflows — the *floor* every
fleet-owned repo gets by default under the greploop posture-B auto-merge regime. A floored
repo does **not** copy these files; it adds one thin caller (`.github/workflows/fleet-floor.yml`)
that `uses:` each workflow here, **pinned to an immutable commit SHA**. Tightening a gate = one
change here + a reviewed SHA-bump caller PR in each repo (never a silent moving tag).

## The five floor components

| Workflow | Check-run job | What it gates |
|---|---|---|
| `python-ci.yml` | `ci` | pytest if a tests dir exists, else import-smoke (`weak`) |
| `python-typecheck.yml` | `type_check` | ruff (ERROR) + mypy/ty if configured |
| `sast.yml` | `sast` | semgrep `p/python p/secrets` at `--severity=ERROR` (0-noise on clean code) |
| `secret-scan.yml` | `secret_scan` | gitleaks, diff-scoped & range-independent (changed-file bytes at HEAD) |
| `python-dep-audit.yml` | `dep_audit` | pip-audit on a lockfile/requirements; no manifest → honest `noop` |

The internal job names (`ci`, `type_check`, `sast`, `secret_scan`, `dep_audit`) are a **frozen
contract** — renaming one is a breaking fleet-wide change (it changes the emitted check-run name
that floored repos' branch protection requires; see the floor spec, §D-12).

## Using it (a floored repo's caller)

See [`templates/fleet-floor.yml`](templates/fleet-floor.yml). Pin every `uses:` to a 40-char
commit SHA of this repo, e.g.:

```yaml
jobs:
  ci:
    uses: Kyzcreig/fleet-floor-workflows/.github/workflows/python-ci.yml@<sha>
    with: { import-name: mypkg }
```

Python CI keeps a **20-minute job timeout** by default, including dependency installation.
Large suites can opt into a longer budget in their reviewed caller PR without changing
other repositories or reducing test coverage:

```yaml
    with:
      import-name: mypkg
      timeout-minutes: 40
```

The numeric `timeout-minutes` input is passed directly to the Actions job timeout.
It does not change pytest selection, concurrency, or failure handling.

A suite whose serial runtime approaches the cap can opt into pytest-xdist instead of only
raising the budget. `pytest-workers` takes `auto` or a positive integer; empty (the default)
keeps serial pytest. The workflow installs `pytest-xdist` only when it is set, and any other
value fails the job. Use it only if the suite is safe to run in parallel (no shared fixed paths
or ports across test files):

```yaml
    with:
      import-name: mypkg
      runner: blacksmith-4vcpu-ubuntu-2404
      pytest-workers: auto
```

JS/TS CI (`js-ci.yml`) takes the same numeric `timeout-minutes` input with the same
20-minute default, for a serial test suite that outgrows the floor's hung-test guard:

```yaml
  ci:
    uses: Kyzcreig/fleet-floor-workflows/.github/workflows/js-ci.yml@<sha>
    with:
      working-directory: bridge
      timeout-minutes: 40
```

### Default test CI (`test-ci.yml`, job `test`)

For a repo with **no** test/build CI on pull requests (ci-speed-lint R15 lists them daily). One
job, no per-repo config, language-detected in the working directory:

| Detected | Runs |
|---|---|
| python (`pyproject`/`setup.*`/`requirements.txt`/any `*.py`) | install, then `pytest` if `tests/` or `test/` holds `test_*.py`/`*_test.py`; else `compileall` + `ruff --select E9,F63,F7,F82` |
| node (`package.json`) | install, then `npm test` (npm's "no test specified" stub does not count), else `npm run build`, else `npm run lint`, else `node --check` |
| shell (`*.sh`) | `bash -n` on every script |

Branches are additive (a polyglot repo runs each). A repo with none of them gets a warning and a
green no-op, so only add the caller where a branch applies. Caller: [`templates/test-ci.yml`](templates/test-ci.yml);
private ANG-Ventures repos pass `runner: blacksmith-2vcpu-ubuntu-2404`. Contract + branch
detection: `tests/test_test_ci.py`; the selftest runs it on `sample/`, `js-sample/`, `generic-sample/`.

### Workflow path-filter lint (fleet R14)

`sast.yml` also fails a **pull_request** that adds or modifies a workflow whose `push` /
`pull_request` / `pull_request_target` trigger has no `paths:` / `paths-ignore:` filter: such a
workflow fires on every bot or docs-only commit (hermes-home #833: ~550 wasted runs/week). Only
the files the PR itself changes are checked (`HEAD^1..HEAD` of the PR merge commit), so a repo's
older workflows do not turn red until someone edits them. Opt a workflow out on purpose with a line
`# path-filter-exempt: <why>` (the usual reason: its pull_request run is a required check, and a
path-filtered required check sits pending forever). The same rule runs fleet-wide daily as
ci-speed-lint R14 (Kyzcreig/fleet-ops-scripts). Callers can pass
`workflow-path-filter-lint: false` to the sast component, but that is a reviewed caller change.
Contract + red/green cases: `tests/test_workflow_path_filter_lint.py`.

### Override lint (`override-lint.yml`, job `override_lint`)

A pull request that **adds a refusal** must say how an operator gets past it, or say on purpose
that nobody can (card t_8f526b34, from the operator-capability audit t_674b3ce7: hermes-agent #1116
shipped a blanket "workers never pin" refusal with neither).

- **Hit** = an added, non-test, non-comment code line whose quoted text matches
  `refus|never|not allowed|orchestrator-only|must not|denied|forbidden`, either under a scoped path
  (input `scope`; default `hermes_cli/ tools/ gateway/ cron/ hooks/ scripts/fleet-merge.sh
  scripts/gh-shim.py`, directories at any depth) or anywhere on a new `raise *Error(`,
  `emit_block(`, `sys.exit(<nonzero>)` or `HTTPException(409` (message may follow on the next 3 lines).
- Hits matching the **no-override-by-design allowlist** are exempt. The seed is the register's
  class A rows (gates 3/7/8, Momus, the kanban live-board guard); callers add reviewed entries via
  input `allowlist` (`path:<regex> <why>` or `line:<regex> <why>`).
- Any other hit **fails** the job unless the PR body carries one of:

  ```
  Override: `--flag-name "<reason>"` (or ENV_VAR=1 / a reason field) — <how it is audited>
  Override: none by design — <why no operator may bypass this>
  ```

  Prose is accepted with a notice when the body says "no … override" / "none by design", or names
  an override-shaped `--allow-*`/`--force*`/… flag or `*_OVERRIDE`/`*_ALLOW*`/`NAME=0|1` env that the
  diff itself adds.
- A named override that appears in no `*.md` under input `docs-paths` (default `skills-shared skills`)
  gets a **warning** (not a failure). The body is re-read over REST on each run, so the caller
  template listens to `edited`. Caller: [`templates/override-lint.yml`](templates/override-lint.yml).
  Contract + red/green cases: `tests/test_override_lint.py`.

## Self-test

[`selftest.yml`](.github/workflows/selftest.yml) runs all five against the clean in-repo
[`sample/`](sample) package on every push — the floor's own floor. It must be green, and its
emitted compound check-run names (`<job> / <job>`) are the canonical names a floored repo pins.

## Safety

This repo is public (cross-owner `uses:` requires it) and defines every floor, so it is the
fleet's highest-value target: it is branch-protected, restricted-push, no-tag-moves, and its own
changes are human-reviewed and never auto-merged. A malicious commit here changes nothing until a
reviewed SHA-bump caller PR lands in a target repo.

# floor v1: provisioning + Phase-2 live-proven 2026-06-30

### NE-pair floor (`ne-pair-floor.yml`, job `ne_pair_floor`)

Card t_ad5331c9. Any of the GrowthBook-off family — `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`,
`DISABLE_TELEMETRY`, `DISABLE_GROWTHBOOK`, `DO_NOT_TRACK` (the last two generic names count only in a file that
mentions `claude`) — set in shell/py/js/ts/yaml/plist/systemd FAILs unless the same env block (±40 lines) also sets
`CLAUDE_CODE_TOTAL_TOKENS_REMINDER=off` (`ne_tt_reminder`, billing: Opus + Sonnet 5.5 re-write the conversation
every turn) and `CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1` (`ne_gb_pair`, behaviour flags); a file that also
spawns the claude binary needs a GrowthBook seed step (`ne_seed_missing`). Full-tree scan, not diff-scoped.
Opt-outs: `.ne-pair-floor-allow` at the repo root, `<path-glob> <check|*> <reason>` per line (no reason = FAIL,
unused entry = WARN). Scanner source: `scripts/ne_pair_floor.py`, embedded in the workflow byte-for-byte.
Caller: [`templates/ne-pair-floor.yml`](templates/ne-pair-floor.yml). Contract + red/green cases:
`tests/test_ne_pair_floor.py`. Why: vault note "Claude Code — NONESSENTIAL_TRAFFIC disables prompt caching-
mechanism, cost, the fix (2026-09-30)".
