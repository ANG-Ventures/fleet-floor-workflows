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

### Caller path filters (`paths:`)

A reusable workflow cannot carry `paths:`; the **caller** decides when it fires (card t_a1dd1955:
>= 6 repos fired every floor job on every PR and push). The rule, per trigger:

- **`pull_request`: no `paths:` / `paths-ignore:` on any floor caller.** Mark the caller
  `# path-filter-exempt: <why>` so fleet R14 (`sast.yml`) passes it.
- **`push: main`**: `paths-ignore` for docs (`'**.md'`, `'docs/**'`) is allowed on a test floor. The PR
  run already gated the change, so push is only a backstop. `ne-pair-floor` takes none.

**The 3,000-file limit.** GitHub matches a path filter against at most the first 3,000 files of the
diff. When a diff has more than 3,000 files and none of the first 3,000 match, the workflow does **not**
run (docs: "Workflow syntax", *Git diff comparisons*). A large merge (an upstream parity merge) whose
matching files sort late is then skipped with no check at all, so a filter can hide a finding exactly
where the most code changes (Prism 7782a5f9dbf6, t_0e51de69, t_5df3de69).

| Floor | Caller filter | Why |
|---|---|---|
| `ne-pair-floor.yml` | **none**, either event | security lint; the scanner opens only `.sh .bash .zsh .py .js .mjs .cjs .ts .tsx .yml .yaml .plist .service .env*` and extension-less shebang scripts itself, and a full-tree run takes ~25 s on hermes-home |
| `override-lint.yml` | **none** | the lint itself skips everything but ADDED lines in non-test files with a code extension (`.py .sh .bash .js .mjs .cjs .ts .tsx .go .rb .pl`) or none, so a docs-only PR finds no hits and passes in seconds |
| `js-ci.yml`, `python-ci.yml`, `test-ci.yml` | PR: **none**. push:main: `paths-ignore` docs | a skipped PR test gate reports nothing; a `<working-directory>/**` filter skips a big PR whose package files sort late |
| `secret-scan.yml`, `sast.yml` | **none: whole tree** | a secret or a vulnerable pattern can land in any path; `sast` also lints every workflow file |

To skip a costly test floor on a PR that cannot affect it, gate the **job**, not the trigger: a
`changes` job lists the PR's files over REST (`pulls/{n}/files`, paginated; GitHub returns at most
3,000) and sets the output to run when any file matches, when the list holds 3,000 files, or when the
call fails (fail open). ha-command-router's `changes` job + `if:` is the working example. Two constraints:

- `paths:` is **workflow-level**. `fleet-floor.yml` usually hosts `secret_scan` and `sast` next to
  `ci`, and those need the whole tree.
- **Required checks.** A path-filtered workflow that is a *required* status check never reports on a
  PR it skips, and the PR sits pending forever. A skipped job reports success, but a skipped
  reusable-workflow call reports under the caller job's name only, so check the required context names.

`tests/test_caller_paths.py` holds every template to this table.

### Sharding JS tests (`js-ci.yml` input `shards`)

`shards: N` (1-16, default 1) splits the test run into N parallel jobs named `ci (1/N)` ... `ci (N/N)`.
Each job runs node's built-in `--test-shard=i/N`, which partitions the test files, so together the
shards run the whole suite. With a package.json test script the flag is passed through as
`npm test -- --test-shard=i/N`, so a custom runner (claude-bpx `scripts/run-tests.js`) has to accept
it before its caller opts in. The default `shards: 1` runs one job named `ci` with the same commands
as before. On Blacksmith, more jobs do not cost more, because billing is in vCPU-minutes. Shard until
the per-job setup (checkout + install) is about 30% of a shard's wall time.

```yaml
  ci:
    uses: ANG-Ventures/fleet-floor-workflows/.github/workflows/js-ci.yml@<sha>
    with:
      working-directory: bridge
      shards: 6
```

Changing from 1 to N > 1 renames the check from `ci` to `ci (i/N)`. If `ci / ci` is a required check,
update that requirement in the same caller PR.

### Sharding Python tests (`python-ci.yml` input `shards`)

Same contract as js-ci's `shards`: `shards: N` (1-16, default 1) runs the pytest step as N jobs named
`ci (1/N)` ... `ci (N/N)`. Each job collects the whole suite as before and keeps every N-th collected
test (an inline plugin over pytest's collection order, no new dependency), so the shards together run
every test exactly once. A shard that ends up with no tests is red (pytest exit 5). It composes with
`pytest-workers` (xdist inside each shard). The default `shards: 1` runs one job named `ci` with the
same command as before. Contract: `tests/test_python_ci_shards.py`.

Sharding renames the check from `ci` to `ci (i/N)`. A caller whose required check is `ci / ci` keeps
it with a fan-in job named exactly that (claude-bpx's `fleet-floor.yml` is the working example):

```yaml
  ci_shards:
    uses: ANG-Ventures/fleet-floor-workflows/.github/workflows/python-ci.yml@<sha>
    with:
      shards: 2
  ci:
    name: ci / ci
    needs: ci_shards
    if: ${{ always() }}
    runs-on: ${{ vars.CI_RUNNER || 'blacksmith-2vcpu-ubuntu-2404' }}
    timeout-minutes: 5
    steps:
      - env: { SHARDS_RESULT: "${{ needs.ci_shards.result }}" }
        run: test "$SHARDS_RESULT" = success
```

### Steps mode for the sub-minute lints (`.github/actions/ne-pair-floor`, `.github/actions/override-lint`)

Blacksmith bills every job as `ceil(runtime)` minutes x vCPU (measured 2026-10-03 with `blacksmith
usage`), so a ~10 s `ne_pair_floor` or `override_lint` job bills a full 2 vCPU-minute on every event:
the cost of a tiny lint is the job's existence. The two composite actions run the same lints as
**steps** of a job the caller already pays for (card t_a9a0e768). The reusable workflows stay as they
are; a caller that does not opt in sees no change.

- Same bytes: the actions run `scripts/ne_pair_floor.py` and `scripts/override_lint.py` from the
  pinned commit, and `tests/test_floor_actions.py` pins both byte-identical to the scripts embedded in
  the reusable workflows. override-lint's collect step is the workflow's step with `/tmp` moved to
  `$RUNNER_TEMP`; both steps are no-ops on non-PR events, as in the workflow.
- Caller contract: check out first (override-lint needs `fetch-depth: 2` for `HEAD^1`), grant
  `pull-requests: read`, pin the action to a 40-hex SHA. The job id is the caller's, so the check name
  becomes the host job's name; move a lint this way only when it is not a required check.
- Path filters move with it: the host job runs on the host workflow's trigger, so gate each step with
  the host's change classifier (fail open) or accept the wider trigger. The README table above lists
  what each scanner reads.

```yaml
      - uses: actions/checkout@<sha>
        with: { fetch-depth: 2 }
      - uses: ANG-Ventures/fleet-floor-workflows/.github/actions/ne-pair-floor@<sha>
      - uses: ANG-Ventures/fleet-floor-workflows/.github/actions/override-lint@<sha>
        with: { github-token: "${{ github.token }}" }
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
