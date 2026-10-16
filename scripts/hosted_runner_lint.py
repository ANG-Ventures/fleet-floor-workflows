#!/usr/bin/env python3
# fleet-floor copy of ANG-Ventures/hermes-home scripts/lints/hosted-runner-lint.py (card t_a8dc95de);
# embedded base64 in .github/workflows/hosted-runner-lint.yml (tests/test_hosted_runner_lint.py).
# Only change vs the hermes-home copy: --root defaults to the current directory (the caller checkout).
"""hosted-runner-lint -- no workflow job may resolve to a GitHub-hosted (metered) runner label (t_a8dc95de).

WHY. 2026-10-16 05:0x, orgs/ANG-Ventures/settings/billing/usage: Oct 1-16 Actions GROSS $2,730.90, net $296.36
(Sep gross $2,485, net $155.75). The net stayed small, so nobody looked. 39 hermes-home workflows ran
`vars.CI_LIGHT_RUNNER || 'ubuntu-latest'` with the variable unset (hosted since day one) and hermes-agent
carried ~70 hosted literals. Ace ruling 2026-10-16 04:25: NO metered GitHub-hosted minutes; everything runs
on the local runners.

RULE. In every .github/workflows/*.yml|*.yaml file, a CODE line (comments stripped, `run:` / `script:` block
scalars and `name:` / `description:` values skipped) that names a GitHub-hosted label is a FINDING:
  ubuntu-latest, ubuntu-24.04, ubuntu-24.04-arm, ubuntu-latest-8-cores, windows-latest, windows-2022,
  windows-11-arm, macos-latest, macos-15, macos-15-intel, ... (`(ubuntu|windows|macos)-(latest|<digit>...)`)
That covers a literal `runs-on:`, a matrix value (`os: [ubuntu-latest, windows-latest]`), the fallback of
`${{ vars.X || 'ubuntu-latest' }}` / `fromJSON(vars.X || '["ubuntu-latest"]')` (an unset variable = billed),
a reusable caller's `with: runner: ubuntu-latest`, and a reusable workflow's `inputs.<x>.default`.
A Blacksmith label (`blacksmith-4vcpu-ubuntu-2404`) never matches (its `ubuntu-` follows a `-`).

WAIVER. `# hosted-ok: <why>` (non-empty reason) on the finding's own line, or anywhere in the enclosing JOB
(from its `  <job_id>:` key to the next job key), e.g. a Windows/macOS build matrix with no self-hosted
runner. A line outside any job (an `on.workflow_call.inputs` default) takes the waiver on its own line or
the line directly above. An empty reason is itself a finding.

SCOPE. PRIVATE repos only (Apollo/Ace 2026-10-16 05:5x: "we take advantage of free stuff obviously but not
billed stuff"; a PUBLIC repo's hosted minutes are $0, e.g. hermes-agent Oct gross $2,412 / net $0).
`--repo-private false` (or env HOSTED_RUNNER_LINT_REPO_PRIVATE=false; in a workflow pass
`${{ github.event.repository.private }}`) prints `skipped: public repo` and exits 0. Unset or any other
value = private (fail closed: an unknown visibility is graded as billed).

OUTPUT. One `FINDING <path>:<line>: <label> in job <job>: <the line>` per hit; exit 1 with findings, 0 clean
(silent unless --verbose), 2 when no workflow file was read (blind is never clean).

  hosted-runner-lint.py [--root DIR] [--verbose]   lint DIR/.github/workflows (default: repo root)
  hosted-runner-lint.py --self-test                fixture mutants; rc 0 only if every one is classified right

Stdlib only, single file: fleet-floor-workflows embeds this file byte-for-byte in its reusable
hosted-runner-lint.yml (its tests/test_hosted_runner_lint.py enforces the copy).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

LABEL = re.compile(r"(?<![\w.-])((?:ubuntu|windows|macos)-(?:latest|slim|\d[\w.]*)(?:-[\w.]+)*)(?![\w.-])", re.I)
WAIVER = re.compile(r"#\s*hosted-ok\s*:(.*)$")
KEY = re.compile(r"^(\s*)(-\s+)?([\w.-]+|\"[^\"]*\"|'[^']*')\s*:(?:\s|$)")
# `|`, `>`, with chomping and indentation indicators in either legal order (`|2-`, `|-2`) (Prism e583356c7d8a).
BLOCK_SCALAR = re.compile(r":\s*[|>](?:[1-9][+-]?|[+-][1-9]?)?\s*(#.*)?$")
PROSE_KEYS = {"name", "description", "run-name"}
SCRIPT_KEYS = {"run", "script", "shell-script"}


def strip_comment(line: str) -> tuple[str, str]:
    """(code, comment). A `#` starts a comment at line start or after whitespace, outside quotes."""
    quote, esc = None, False
    for i, ch in enumerate(line):
        if quote:
            if esc:
                esc = False
            elif ch == "\\" and quote == '"':
                esc = True                          # YAML double-quoted escape: `\"` does not close (Prism 4405154e4c45)
            elif ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i], line[i:]
    return line, ""


def waiver_of(comment: str) -> str | None:
    """None: no waiver on this comment. '': waiver with an empty reason. Else the reason."""
    m = WAIVER.search(comment)
    return None if not m else m.group(1).strip()


def lint_text(text: str, rel: str) -> list[str]:
    lines = text.splitlines()
    # Pass 1: job spans. `jobs:` at column 0; a job key is the first deeper indent below it.
    jobs: list[tuple[int, int, str]] = []          # (start_idx, end_idx_exclusive, job_id)
    in_jobs, job_indent = False, None
    cur = None
    for i, raw in enumerate(lines):
        code, _ = strip_comment(raw)
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip())
        if indent == 0:
            if cur:
                jobs.append((cur[0], i, cur[1]))
                cur = None
            in_jobs = code.strip().startswith("jobs:")
            job_indent = None
            continue
        if not in_jobs:
            continue
        m = KEY.match(code)
        if job_indent is None and m:
            job_indent = indent
        if m and indent == job_indent:
            if cur:
                jobs.append((cur[0], i, cur[1]))
            cur = (i, m.group(3).strip("'\""))
    if cur:
        jobs.append((cur[0], len(lines), cur[1]))

    def job_at(i: int):
        for s, e, j in jobs:
            if s <= i < e:
                return s, e, j
        return None

    # Pass 2: masks.
    #   body[i]  -- line i is inside ANY block scalar (`key: |` ...): string data, so it never carries a waiver
    #               (Prism 56c3ec3f0c88, 7e9cf82a1f86) and, outside a `matrix:`, is never graded.
    #   skip[i]  -- line i is not graded: a block-scalar body as above, or an INLINE script/prose value
    #               (`run: echo ubuntu-latest`, `name: ...`). A BARE `run:` / `name:` key opens a mapping (a job id,
    #               an input declaration), never prose, so its children stay graded (Prism 6051d2f0bb8a).
    #   Inside a `matrix:` mapping every key is data: a dimension called `name` is graded (Prism e8a94753e3db).
    skip = [False] * len(lines)
    body = [False] * len(lines)
    block_indent = None                             # inside a block scalar body deeper than this
    block_graded = False                            # that block scalar sits in a matrix: grade its lines
    matrix_indent = None                            # inside a `matrix:` mapping deeper than this
    for i, raw in enumerate(lines):
        indent = len(raw) - len(raw.lstrip())
        if block_indent is not None:
            if raw.strip() == "" or indent > block_indent:
                body[i] = True
                skip[i] = not block_graded
                continue
            block_indent = None
        code, _ = strip_comment(raw)
        if not code.strip():
            continue
        if matrix_indent is not None and indent <= matrix_indent:
            matrix_indent = None
        m = KEY.match(code)
        key = m.group(3).strip("'\"") if m else None
        if key == "matrix" and matrix_indent is None:
            matrix_indent = len(m.group(1)) + (len(m.group(2)) if m.group(2) else 0)
            continue
        if m and BLOCK_SCALAR.search(raw.rstrip()):
            block_indent = len(m.group(1)) + (len(m.group(2)) if m.group(2) else 0)
            block_graded = matrix_indent is not None
            skip[i] = matrix_indent is None and key in SCRIPT_KEYS | PROSE_KEYS
            continue
        if matrix_indent is None and key in SCRIPT_KEYS | PROSE_KEYS and not code.rstrip().endswith(":"):
            skip[i] = True                           # inline script/prose value

    job_waiver: dict[str, str | None] = {}
    for s, e, j in jobs:
        w = None
        for k in range(s, e):
            if body[k]:
                continue
            r = waiver_of(strip_comment(lines[k])[1])
            if r is not None:
                w = r if w is None or (r and not w) else w
        job_waiver[j] = w

    findings: list[str] = []
    for i, raw in enumerate(lines):
        if skip[i]:
            continue
        code, comment = strip_comment(raw)
        if body[i]:
            code, comment = raw, ""                   # a matrix block-scalar body: data, no comments
        if not code.strip():
            continue
        for lm in LABEL.finditer(code):
            label = lm.group(1)
            if label.lower().startswith("ubuntu-") and code[max(0, lm.start() - 1)] == "/":
                continue                             # e.g. an image path `.../ubuntu-24.04`, not a label
            line_w = waiver_of(comment)
            span = job_at(i)
            if span:
                w = line_w if line_w is not None else job_waiver.get(span[2])
                where = "job " + span[2]
            else:
                prev = waiver_of(strip_comment(lines[i - 1])[1]) if i > 0 and not body[i - 1] else None
                w = line_w if line_w is not None else prev
                where = "workflow scope"
            if w:
                continue
            why = " (`# hosted-ok:` has an empty reason)" if w == "" else ""
            findings.append("FINDING {}:{}: {} in {}{}: {}".format(rel, i + 1, label, where, why, raw.strip()))
            break                                    # one finding per line
    return findings


def lint_root(root: Path) -> tuple[int, list[str]]:
    wf = root / ".github" / "workflows"
    files = sorted(p for p in wf.glob("*") if p.suffix in (".yml", ".yaml") and p.is_file()) if wf.is_dir() else []
    out: list[str] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out.append("FINDING {}: unreadable ({})".format(p.relative_to(root), exc))
            continue
        out.extend(lint_text(text, str(p.relative_to(root))))
    return len(files), out


HEAD = "name: t\non: [push]\njobs:\n"
CASES = [
    # (label, workflow text, expected finding count)
    ("literal ubuntu-latest", HEAD + "  a:\n    runs-on: ubuntu-latest\n    steps: [{run: 'true'}]\n", 1),
    ("literal ubuntu-24.04", HEAD + "  a:\n    runs-on: ubuntu-24.04\n", 1),
    ("vars fallback", HEAD + "  a:\n    runs-on: ${{ vars.CI_LIGHT_RUNNER || 'ubuntu-latest' }}\n", 1),
    ("fromJSON fallback", HEAD + "  a:\n    runs-on: ${{ fromJSON(vars.CI_RUNNER_LABELS || '[\"ubuntu-latest\"]') }}\n", 1),
    ("self-hosted fromJSON fallback", HEAD + "  a:\n    runs-on: ${{ fromJSON(vars.CI_RUNNER_LABELS || '[\"self-hosted\",\"hermes-ci\"]') }}\n", 0),
    ("pool label", HEAD + "  a:\n    runs-on: ${{ vars.CI_LIGHT_RUNNER || 'hermes-ci' }}\n", 0),
    ("blacksmith label", HEAD + "  a:\n    runs-on: blacksmith-4vcpu-ubuntu-2404\n", 0),
    ("matrix flow list", HEAD + "  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest, windows-latest]\n"
     "    runs-on: ${{ matrix.os }}\n", 1),
    ("matrix block list", HEAD + "  a:\n    strategy:\n      matrix:\n        os:\n          - macos-15\n          - windows-2022\n"
     "    runs-on: ${{ matrix.os }}\n", 2),
    ("matrix include runner", HEAD + "  a:\n    strategy:\n      matrix:\n        include:\n          - runner: windows-11-arm\n", 1),
    ("reusable with runner", HEAD + "  a:\n    uses: o/r/.github/workflows/x.yml@abc\n    with:\n      runner: ubuntu-latest\n", 1),
    ("reusable input default", "on:\n  workflow_call:\n    inputs:\n      runner:\n        type: string\n"
     "        default: \"ubuntu-latest\"\njobs:\n  a:\n    runs-on: ${{ inputs.runner }}\n", 1),
    ("input default waived above", "on:\n  workflow_call:\n    inputs:\n      runner:\n        # hosted-ok: public caller\n"
     "        default: \"ubuntu-latest\"\njobs:\n  a:\n    runs-on: ${{ inputs.runner }}\n", 0),
    ("ternary macos", HEAD + "  a:\n    runs-on: ${{ inputs.arch == 'x64' && 'macos-15-intel' || 'macos-15' }}\n", 1),
    ("line waiver", HEAD + "  a:\n    runs-on: windows-latest  # hosted-ok: no self-hosted Windows runner (Ace)\n", 0),
    ("job waiver covers matrix", HEAD + "  win:\n    # hosted-ok: Windows build matrix, no self-hosted runner\n    strategy:\n"
     "      matrix:\n        os: [windows-latest, windows-2022]\n    runs-on: ${{ matrix.os }}\n", 0),
    ("job waiver does not leak", HEAD + "  win:\n    # hosted-ok: Windows build\n    runs-on: windows-latest\n"
     "  lin:\n    runs-on: ubuntu-latest\n", 1),
    ("empty waiver", HEAD + "  a:\n    runs-on: ubuntu-latest  # hosted-ok:\n", 1),
    ("other waiver word", HEAD + "  a:\n    runs-on: ubuntu-latest  # github-hosted: free\n", 1),
    ("comment only", HEAD + "  a:\n    # was ubuntu-latest until 10-16\n    runs-on: hermes-ci  # not ubuntu-24.04 any more\n", 0),
    ("run block mentions label", HEAD + "  a:\n    runs-on: hermes-ci\n    steps:\n      - run: |\n"
     "          echo ubuntu-latest\n          docker pull ubuntu-24.04\n      - run: echo windows-latest\n", 0),
    ("name/description prose", HEAD + "  a:\n    name: runs like ubuntu-latest\n    runs-on: hermes-ci\n", 0),
    ("after run block resumes", HEAD + "  a:\n    runs-on: hermes-ci\n    steps:\n      - run: |\n          echo hi\n"
     "  b:\n    runs-on: ubuntu-latest\n", 1),
    ("label in quotes with hash", HEAD + "  a:\n    runs-on: ${{ vars.X || 'ubuntu-latest' }} # c#d\n", 1),
    ("arm label", HEAD + "  a:\n    runs-on: ubuntu-24.04-arm\n", 1),
    ("ubuntu-slim (Prism 4f0b5bb8672b)", HEAD + "  a:\n    runs-on: ubuntu-slim\n", 1),
    ("heredoc waiver ignored (Prism 56c3ec3f0c88)", HEAD + "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
     "          cat > f.yml <<EOF\n          # hosted-ok: fixture\n          EOF\n", 1),
    ("matrix dim named name (Prism e8a94753e3db)", HEAD + "  a:\n    strategy:\n      matrix:\n        name: [ubuntu-latest]\n"
     "    runs-on: ${{ matrix.name }}\n", 1),
    ("matrix block dim named description", HEAD + "  a:\n    strategy:\n      matrix:\n        description:\n"
     "          - windows-latest\n    runs-on: ${{ matrix.description }}\n", 1),
    ("name after matrix still prose", HEAD + "  a:\n    strategy:\n      matrix:\n        os: [x]\n"
     "    name: like ubuntu-latest\n    runs-on: hermes-ci\n", 0),
    ("job id named run (Prism 6051d2f0bb8a)", "on: [push]\njobs:\n  run:\n    runs-on: ubuntu-latest\n"
     "    steps: [{run: 'true'}]\n", 1),
    ("job id named name", "jobs:\n  name:\n    runs-on: ubuntu-latest\n", 1),
    ("input named description keeps default", "on:\n  workflow_call:\n    inputs:\n      description:\n"
     "        type: string\n        default: ubuntu-latest\njobs:\n  a:\n    runs-on: hermes-ci\n", 1),
    ("run |2- body waiver ignored (Prism e583356c7d8a)", HEAD + "  a:\n    runs-on: ubuntu-latest\n    steps:\n"
     "      - run: |2-\n          # hosted-ok: fixture\n          true\n", 1),
    ("run |-2 body waiver ignored", HEAD + "  a:\n    runs-on: ubuntu-latest\n    steps:\n"
     "      - run: >-2\n          # hosted-ok: fixture\n          true\n", 1),
    ("env block waiver ignored (Prism 7e9cf82a1f86)", HEAD + "  a:\n    runs-on: ubuntu-latest\n    env:\n"
     "      EXAMPLE: |\n        # hosted-ok: fixture\n    steps:\n      - run: 'true'\n", 1),
    ("escaped quote waiver ignored (Prism 4405154e4c45)", HEAD + "  a:\n    runs-on: ubuntu-latest\n    env:\n"
     "      NOTE: \"\\\" # hosted-ok: fixture\"\n    steps: [{run: true}]\n", 1),
    ("real waiver after quoted value", HEAD + "  a:\n    env:\n      NOTE: \"a \\\" b\"  # hosted-ok: Windows build\n"
     "    runs-on: windows-latest\n", 0),
    ("image path not label", HEAD + "  a:\n    runs-on: hermes-ci\n    container: ghcr.io/x/ubuntu-24.04\n", 0),
]


def is_public(flag: str) -> bool:
    """Only an explicit 'false' (GitHub's `repository.private` rendering) means public. Fail closed."""
    return str(flag).strip().lower() == "false"


def self_test() -> int:
    bad = 0
    for flag, want in (("false", True), ("False", True), ("true", False), ("", False), ("garbage", False)):
        ok = is_public(flag) == want
        bad += not ok
        print("{} {:32s} want public={} got {}".format("ok  " if ok else "FAIL", "repo-private=%r" % flag, want,
                                                       is_public(flag)))
    for label, text, want in CASES:
        got = lint_text(text, "wf.yml")
        ok = len(got) == want
        bad += not ok
        print("{} {:32s} want {} got {}{}".format("ok  " if ok else "FAIL", label, want, len(got),
                                                 "" if ok else "  " + " | ".join(got)))
    with tempfile.TemporaryDirectory() as d:
        n, f = lint_root(Path(d))
        ok = n == 0
        bad += not ok
        print("{} {:32s} want 0 files read got {}".format("ok  " if ok else "FAIL", "blind (no workflows dir)", n))
        os.makedirs(os.path.join(d, ".github", "workflows"))
        Path(d, ".github", "workflows", "a.yml").write_text(CASES[0][1])
        Path(d, ".github", "workflows", "b.yaml").write_text(CASES[5][1])
        n, f = lint_root(Path(d))
        ok = n == 2 and len(f) == 1 and "a.yml:5" in f[0]
        bad += not ok
        print("{} {:32s} want 2 files/1 finding at a.yml:5 got {}/{}".format("ok  " if ok else "FAIL", "root walk", n, f))
    print("self-test: {}".format("PASS" if not bad else "FAIL ({} case(s))".format(bad)))
    return 0 if not bad else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--root", default=None, help="repo root (default: this script's repo)")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--repo-private", default=os.environ.get("HOSTED_RUNNER_LINT_REPO_PRIVATE", ""),
                    help="'false' = public repo (hosted minutes free): skip. Anything else = private (default).")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if is_public(a.repo_private):
        print("hosted-runner-lint: skipped: public repo (GitHub-hosted minutes are $0 there)")
        return 0
    root = Path(a.root) if a.root else Path.cwd()
    n, findings = lint_root(root)
    if n == 0:
        print("hosted-runner-lint: BLIND: no workflow files under {}/.github/workflows".format(root), file=sys.stderr)
        return 2
    for f in findings:
        print(f)
    if findings:
        print("hosted-runner-lint: {} finding(s) in {} workflow file(s). Fix: runs-on the local pool "
              "(`${{{{ vars.CI_LIGHT_RUNNER || 'hermes-ci' }}}}`, or `fromJSON(vars.CI_RUNNER_LABELS || "
              "'[\"self-hosted\",\"hermes-ci\"]')`); a job that truly needs GitHub-hosted carries "
              "`# hosted-ok: <why>`.".format(len(findings), n))
        return 1
    if a.verbose:
        print("hosted-runner-lint: clean ({} workflow file(s))".format(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
