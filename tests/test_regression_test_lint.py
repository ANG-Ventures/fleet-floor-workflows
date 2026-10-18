"""regression-test-lint: a FIX PR that changes code must change a test, or carry `no-regression-test: <reason>`.

Runs the EXACT `Regression test lint` step script from regression-test-lint.yml (extracted from the YAML,
executed with bash) against synthetic PR fixtures, and pins scripts/regression_test_lint.py to the embedded
copy. Card t_d1ac622f.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest

import yaml

from test_workflow_path_filter_lint import run_lint

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/regression-test-lint.yml"
TEMPLATE = ROOT / "templates/regression-test-lint.yml"
SCRIPT = ROOT / "scripts/regression_test_lint.py"
STEP = "Regression test lint"


def load(p=WF):
    return yaml.load(p.read_text(), Loader=yaml.BaseLoader)


def step(name=STEP):
    steps = [s for s in load()["jobs"]["regression_test_lint"]["steps"] if s.get("name") == name]
    assert len(steps) == 1, "regression-test-lint.yml must carry exactly one %r step" % name
    return steps[0]


def run(title, files, body="", branch="feature/x"):
    """files: list of paths (status modified) or (path, status[, patch_head]) tuples."""
    fl = []
    for f in files:
        if isinstance(f, str):
            f = (f,)
        fl.append({"path": f[0], "status": f[1] if len(f) > 1 else "modified",
                   "patch_head": f[2] if len(f) > 2 else ""})
    with tempfile.TemporaryDirectory() as d:
        jp, sp = Path(d) / "pr.json", Path(d) / "summary.md"
        jp.write_text(json.dumps({"title": title, "body": body, "branch": branch, "files": fl}))
        script = step()["run"].replace("/tmp/regression-test-lint.json", str(jp))
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env=dict(os.environ, GITHUB_STEP_SUMMARY=str(sp)))
        summary = sp.read_text() if sp.exists() else ""
    return r.returncode, r.stdout + r.stderr, summary


def mod():
    spec = importlib.util.spec_from_file_location("rtl", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Contract(unittest.TestCase):
    """The five card fixtures (t_d1ac622f)."""

    def test_fix_code_no_test_is_red(self):
        rc, out, summary = run("fix(router): drop stale lease on restart", ["router/lease.py"])
        self.assertEqual(rc, 1, out)
        self.assertIn("::error::regression-test-lint FAIL", out)
        self.assertIn("no-regression-test: <why", out)
        self.assertIn("FAIL", summary)

    def test_exemption_line_is_green_and_echoed(self):
        body = "Summary\n\nno-regression-test: pure log-message wording change, no behavior\n"
        rc, out, summary = run("fix: typo in error log", ["router/lease.py"], body)
        self.assertEqual(rc, 0, out)
        self.assertIn("::notice::no-regression-test: pure log-message wording change", out)
        self.assertIn("`no-regression-test:` pure log-message wording change", summary)

    def test_fix_with_test_is_green(self):
        rc, out, _ = run("fix: lease reaper", ["router/lease.py", "tests/test_lease.py"])
        self.assertEqual(rc, 0, out)

    def test_non_fix_is_green(self):
        rc, out, _ = run("feat: new lease API", ["router/lease.py"])
        self.assertEqual(rc, 0, out)

    def test_docs_only_fix_is_green(self):
        rc, out, _ = run("fix: README typo", ["README.md", "docs/setup.md", "config/x.yaml"])
        self.assertEqual(rc, 0, out)
        self.assertIn("changes no code", out)


class Exemption(unittest.TestCase):
    def test_short_reason_is_red(self):
        rc, out, _ = run("fix: x", ["a.py"], "no-regression-test: trivial\n")
        self.assertEqual(rc, 1, out)
        self.assertIn("< 15", out)

    def test_markdown_decorated_line(self):
        for line in ("- **no-regression-test:** config default only, no code path changes",
                     "`no-regression-test: config default only, no code path changes`",
                     "> No-Regression-Test: config default only, no code path changes"):
            rc, out, _ = run("fix: x", ["a.py"], "body\n" + line + "\n")
            self.assertEqual(rc, 0, (line, out))

    def test_mid_sentence_mention_is_not_an_exemption(self):
        rc, out, _ = run("fix: x", ["a.py"], "I did not add a no-regression-test: line because reasons here\n")
        self.assertEqual(rc, 1, out)


class Classification(unittest.TestCase):
    def setUp(self):
        self.m = mod()

    def fix(self, title, branch=""):
        return self.m.classify(title, branch)[0]

    def test_fix_titles(self):
        for t in ("fix(gw): x", "hotfix: y", "Fix stale lease", "bugfix: z", "[t_abc] fix: x",
                  "restore contracts regressed by parity merge", "repair broken symlink", "regression in wake",
                  "upstream parity merge 2026-10-17", "lease broke after restart", "Fixes #12"):
            self.assertTrue(self.fix(t), t)

    def test_non_fix_titles(self):
        for t in ("feat: add fixtures for router", "prefix all env vars", "docs: bug report template",
                  "add restore drill to runbook", "chore: bump deps", "suffix handling"):
            self.assertFalse(self.fix(t), t)

    def test_branch_classifies(self):
        self.assertTrue(self.fix("lease reaper", "daedalus/t_1-fix-lease"))
        self.assertTrue(self.fix("lease reaper", "hotfix/lease"))
        self.assertFalse(self.fix("lease reaper", "daedalus/t_1-prefix-env"))


class FileKinds(unittest.TestCase):
    def setUp(self):
        self.m = mod()

    def test_test_layouts(self):
        for p in ("tests/test_a.py", "server/tests/x.py", "scripts/tests/test_b.sh", "pkg/a_test.go",
                  "web/a.test.ts", "web/a.spec.js", "hooks/tests/fixtures/x.json", "selftest/run.sh",
                  "scripts/lint-selftest.sh", "tests/suites.json", ".github/ci/suites.json", "conftest.py",
                  "scripts/test-wake.sh", "__tests__/a.js", "test/golden/out.txt"):
            self.assertTrue(self.m.is_test(p), p)
        for p in ("scripts/lint.py", "router/lease.py", "fixture.py", "latest/a.py", "contest.py"):
            self.assertFalse(self.m.is_test(p), p)

    def test_code_detection(self):
        code = lambda p, head="": self.m.is_code({"path": p, "patch_head": head})
        for p in ("a.py", "x/b.sh", "c.ts", "d.go", "e.rs", "f.mjs", "bin/tool", "scripts/run"):
            self.assertTrue(code(p), p)
        self.assertTrue(code("libexec2/thing", "@@ -0,0 +1 @@\n+#!/usr/bin/env bash"))
        for p in ("README.md", "a.yaml", "a.json", "LICENSE", "Makefile", ".gitignore", "docs/x.txt",
                  "notes/thing", "tests/test_a.py"):
            self.assertFalse(code(p), p)

    def test_removed_code_does_not_count(self):
        rc, out, _ = run("fix: drop dead script", [("scripts/dead.sh", "removed")])
        self.assertEqual(rc, 0, out)

    def test_extensionless_executable_counts(self):
        rc, out, _ = run("fix: wrapper", [("bin/hermes-wrap", "modified", "@@ -1 +1 @@\n #!/bin/sh")])
        self.assertEqual(rc, 1, out)


class Wiring(unittest.TestCase):
    def test_script_is_the_embedded_step(self):
        m = re.search(r"<<'PY'\n(.*?)\nPY\n", step()["run"], re.S)
        self.assertTrue(m)
        self.assertEqual(SCRIPT.read_text(encoding="utf-8"), m.group(1) + "\n")

    def test_steps_are_pr_only(self):
        for name in ("Collect PR files and body", STEP):
            self.assertIn("pull_request", step(name)["if"])

    def test_job_name_frozen(self):
        self.assertEqual(load()["jobs"]["regression_test_lint"]["name"], "regression_test_lint")

    def test_template_no_path_filter_and_listens_to_edited(self):
        on = load(TEMPLATE)["on"]
        for ev, spec in on.items():
            spec = spec if isinstance(spec, dict) else {}
            self.assertFalse({"paths", "paths-ignore"} & set(spec), ev)
        self.assertIn("edited", on["pull_request"]["types"])
        self.assertEqual(load(TEMPLATE)["permissions"].get("pull-requests"), "read")

    def test_template_passes_fleet_r14(self):
        rc, out = run_lint({"regression-test-lint.yml": (TEMPLATE.read_text(), 0)})
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
