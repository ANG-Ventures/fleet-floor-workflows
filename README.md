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
