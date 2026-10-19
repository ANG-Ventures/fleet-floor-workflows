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

    def test_parity_merge_titles(self):
        for t in ("parity: upstream sync 2026-10-01 (NousResearch 612d8e44 into fork)",
                  "merge: parity sync with upstream (5,374 commits)", "sync: upstream parity merge 2026-07-23",
                  "Merge upstream NousResearch/main into fork", "parity 2026-10-01 CI round-5 lane L6"):
            self.assertTrue(self.fix(t), t)

    def test_non_fix_titles(self):
        for t in ("feat: add fixtures for router", "prefix all env vars", "docs: bug report template",
                  "add restore drill to runbook", "chore: bump deps", "suffix handling",
                  # replay false positive pcv#1006: "parity" names a feature, not a parity merge
                  "Echo calls-server as a portable container: inventory, Containerfile + compose, parity/cutover scripts",
                  "satellite_parity: LED row - dark ring is drift", "ci-runners image parity: probe imports",
                  # replay false positives on hermes-home (each a name/adjective, not a fix verb)
                  "continuity-regression §5b: gate on kanban wake-contract fork canary",
                  "tcc-process-snapshot: 30 s -> fixed 600 s, no override",
                  "cc-display: USB-C DDC after D6 05 is a transport property, not an mddc bug",
                  "skills: land live-only edits of 7 #4003 skills on main, keep #4003's Prism fixes",
                  "drain: live HEAD b17affb5c + origin/main, 13 conflicts + ups-commissioning restore"):
            self.assertFalse(self.fix(t), t)

    def test_branch_classifies(self):
        self.assertTrue(self.fix("lease reaper", "daedalus/t_1-fix-lease"))
        self.assertTrue(self.fix("lease reaper", "hotfix/lease"))
        self.assertFalse(self.fix("lease reaper", "daedalus/t_1-prefix-env"))
        # replay false positive vh#4118: a runbook branch named after its subject
        self.assertFalse(self.fix("fleet-cameras: Reolink Mac client runbook", "reolink-mac-client-repair"))


class FileKinds(unittest.TestCase):
    def setUp(self):
        self.m = mod()

    def test_test_layouts(self):
        for p in ("tests/test_a.py", "server/tests/x.py", "scripts/tests/test_b.sh", "pkg/a_test.go",
                  "web/a.test.ts", "web/a.spec.js", "hooks/tests/fixtures/x.json", "selftest/run.sh",
                  "scripts/lint-selftest.sh", "tests/suites.json", ".github/ci/suites.json", "conftest.py",
                  "scripts/test-wake.sh", "__tests__/a.js", "test/golden/out.txt",
                  "scripts/windows-display/golden-projection-test.ps1"):
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


class Credits(unittest.TestCase):
    """Test evidence that is not a test FILE (replay false positives on hermes-home)."""

    def setUp(self):
        self.m = mod()

    def pr(self, files):
        return {"title": "fix: x", "body": "", "branch": "b", "files": files}

    def test_registering_a_test_in_ci_counts(self):  # vh#3135
        # the registered test already exists at head (registration needs it to exist; t_23b5215f)
        head = {"scripts/test_audit_fallback_chain_rot.py": "def test_x():\n    pass\n"}
        v = self.m.verdict(self.pr([
            {"path": "scripts/audit.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
            {"path": ".github/workflows/ci.yml", "status": "modified",
             "patch": "@@ -5,0 +6,1 @@\n+          python -m pytest -q scripts/test_audit_fallback_chain_rot.py\n"}]),
            fetch=head.get)
        self.assertEqual(v["result"], "pass", v)
        self.assertIn("registers", v["reason"])

    def test_glob_or_removed_line_is_not_registration(self):
        v = self.m.verdict(self.pr([
            {"path": "scripts/audit.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
            {"path": "scripts/lib/gate.json", "status": "modified",
             "patch": "@@ -1,2 +1,3 @@\n-  \"tests/test_old.py\",\n+  \"tests/**\",\n+  \"scripts/tests/test_*.py\",\n"}]))
        self.assertEqual(v["result"], "fail", v)

    def test_rows_added_inside_selftests_table_count(self):  # vh#4963
        head = "import re\n\nSELF_TESTS = [  # cases\n    (\"a\", 1),\n    (\"b\", 2),\n]\n\ndef run():\n    pass\n"
        f = {"path": "scripts/lints/x_lint.py", "status": "modified", "head_text": head,
             "patch": "@@ -3,3 +3,4 @@\n SELF_TESTS = [  # cases\n     (\"a\", 1),\n+    (\"b\", 2),\n ]\n"}
        v = self.m.verdict(self.pr([f]))
        self.assertEqual(v["result"], "pass", v)
        self.assertIn("SELF_TESTS", v["reason"])

    def test_edit_outside_selftests_table_does_not_count(self):
        head = "import re\n\nSELF_TESTS = [\n    (\"a\", 1),\n]\n\ndef run():\n    return 2\n"
        f = {"path": "scripts/lints/x_lint.py", "status": "modified", "head_text": head,
             "patch": "@@ -7,2 +7,2 @@\n def run():\n-    return 1\n+    return 2\n"}
        self.assertEqual(self.m.verdict(self.pr([f]))["result"], "fail")

    def test_fetch_is_used_for_head_text(self):
        seen = []
        f = {"path": "a.py", "status": "modified", "patch": "@@ -2,0 +3,1 @@\n+    (\"c\", 3),\n"}
        v = self.m.verdict(self.pr([f]), lambda p: seen.append(p) or "x = 1\nCASES_SELFTEST = [\n    1,\n]\n")
        self.assertEqual(seen, ["a.py"])
        self.assertEqual(v["result"], "pass", v)


class PrismRound2(unittest.TestCase):
    """Prism on #35 (t_23b5215f): every credit path must require evidence that a test still runs."""

    def setUp(self):
        self.m = mod()

    def v(self, files, title="fix: correct lease", **kw):
        return self.m.verdict(dict({"title": title, "body": "", "branch": "b", "files": files}, **kw))

    def test_deleted_test_is_not_a_test_change(self):  # 6a41ba6a7713 / 877eb9e5b3e8 / 8c1900e4f037
        v = self.v([{"path": "router/lease.py", "status": "modified"},
                    {"path": "tests/test_lease.py", "status": "removed"}])
        self.assertEqual(v["result"], "fail", v)
        self.assertEqual(v["tests"], [])
        rc, out, _ = run("fix: correct lease", ["router/lease.py", ("tests/test_lease.py", "removed")])
        self.assertEqual(rc, 1, out)

    def test_renamed_test_still_counts(self):
        v = self.v([{"path": "router/lease.py", "status": "modified"},
                    {"path": "tests/test_lease2.py", "status": "renamed"}])
        self.assertEqual(v["result"], "pass", v)

    def test_truncated_inventory_fails_fix_pr(self):  # c53da46cf0f2
        files = [{"path": "docs/%04d.md" % i, "status": "modified"} for i in range(5)]
        v = self.v(files, title="fix: repair calculation", changed_files=6)
        self.assertEqual(v["result"], "fail", v)
        self.assertIn("incomplete", v["reason"])
        self.assertEqual(self.v(files, title="fix: x", changed_files=5)["result"], "pass")
        self.assertEqual(self.v(files, title="feat: x", changed_files=6)["result"], "pass")

    def test_truncated_inventory_is_red_in_the_step(self):
        with tempfile.TemporaryDirectory() as dd:
            jp = Path(dd) / "pr.json"
            jp.write_text(json.dumps({"title": "fix: x", "body": "", "branch": "b", "changed_files": 3001,
                                      "files": [{"path": "docs/a.md", "status": "modified"}]}))
            script = step()["run"].replace("/tmp/regression-test-lint.json", str(jp))
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                               env={k: x for k, x in os.environ.items() if k != "GITHUB_STEP_SUMMARY"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_collect_step_carries_changed_files(self):
        s = step("Collect PR files and body")["run"]
        self.assertIn("changed_files", s)

    def test_comment_mentioning_test_path_is_not_registration(self):  # d079ecdb8371 / b412e9029dee
        for path, line in (("router/lease.py", "# see tests/test_lease.py"),
                           ("README.md", "covered by https://x/tests/test_lease.py"),
                           ("docs/notes.md", "tests/test_lease.py pins this"),
                           (".github/workflows/ci.yml", "# covered by tests/test_lease.py")):
            v = self.v([{"path": "router/lease.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
                        {"path": path, "status": "modified", "patch": "@@ -1,0 +1,1 @@\n+" + line + "\n"}])
            self.assertEqual(v["result"], "fail", (path, v))

    def test_registration_needs_the_test_to_exist_at_head(self):
        files = [{"path": "router/lease.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
                 {"path": ".github/workflows/ci.yml", "status": "modified",
                  "patch": "@@ -5,0 +6,1 @@\n+      run: pytest tests/test_ghost.py\n"}]
        pr = {"title": "fix: x", "body": "", "branch": "b", "files": files}
        self.assertEqual(self.m.verdict(pr, lambda p: None)["result"], "fail")
        self.assertEqual(self.m.verdict(pr, lambda p: "def test_x(): pass\n")["result"], "pass")

    def test_suites_json_registration_counts(self):
        v = self.v([{"path": "router/lease.py", "status": "modified", "patch": "@@ -1 +1 @@\n-a\n+b\n"},
                    {"path": ".github/ci/suites.json", "status": "modified",
                     "patch": "@@ -1,0 +2,1 @@\n+  \"tests/test_lease.py\",\n"}])
        # suites.json is itself a test file, so it passes as a test-file change; registration is moot.
        self.assertEqual(v["result"], "pass", v)

    def test_comment_after_selftests_table_is_not_credit(self):  # f6d1ea95516b / f1074e383840
        head = "SELF_TESTS = [\n    (\"a\", 1),\n]\n# parser behavior explained\ndef parse():\n    return 2\n"
        f = {"path": "app.py", "status": "modified", "head_text": head,
             "patch": "@@ -1,5 +1,6 @@\n SELF_TESTS = [\n     (\"a\", 1),\n ]\n+# parser behavior explained\n"
                      " def parse():\n-    return 1\n+    return 2\n"}
        self.assertEqual(self.v([f], title="fix: correct parser")["result"], "fail")

    def test_prism_repro_single_line_table(self):  # f1074e383840 verbatim repro
        v = self.m.verdict({"title": "fix: startup", "files": [{"path": "a.py", "patch":
            "@@ -1,2 +1,3 @@\n SELF_TESTS = []\n+# startup fix\n-result = 1\n+result = 2\n",
            "head_text": "SELF_TESTS = []\n# startup fix\nresult = 2\n"}]})
        self.assertEqual(v["result"], "fail", v)

    def test_indented_comment_inside_table_is_not_a_case(self):
        head = "SELF_TESTS = [\n    (\"a\", 1),\n    # note\n]\nx = 2\n"
        f = {"path": "app.py", "status": "modified", "head_text": head,
             "patch": "@@ -2,2 +2,3 @@\n     (\"a\", 1),\n+    # note\n ]\n@@ -5 +5 @@\n-x = 1\n+x = 2\n"}
        self.assertEqual(self.v([f])["result"], "fail")

    def test_bash_array_rows_still_count(self):
        head = "#!/bin/bash\ndeclare -a SELF_TESTS=(\n  \"a 1\"\n  \"b 2\"\n)\nrun() { :; }\n"
        f = {"path": "scripts/x.sh", "status": "modified", "head_text": head,
             "patch": "@@ -3,2 +3,3 @@\n   \"a 1\"\n+  \"b 2\"\n )\n"}
        self.assertEqual(self.v([f])["result"], "pass")

    def test_def_selftest_body_counts(self):
        head = "def selftest():\n    assert f(1) == 2\n    assert f(2) == 3\n"
        f = {"path": "a.py", "status": "modified", "head_text": head,
             "patch": "@@ -2,1 +2,2 @@\n     assert f(1) == 2\n+    assert f(2) == 3\n"}
        self.assertEqual(self.v([f])["result"], "pass")


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
