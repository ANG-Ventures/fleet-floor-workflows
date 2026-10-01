"""python-ci.yml test detection must be SIGPIPE-proof (t_7146a779).

The old probe `find "$TESTS" ... | grep -q .` under `set -euo pipefail` raced: grep -q exits on the
first match, find dies of SIGPIPE (141), pipefail flips the condition FALSE, and a repo WITH tests
took the import-smoke branch -> vacuous green. The step script is extracted from the workflow and run
with a stub `python` on PATH (records argv, exits 0) against a tests dir holding many test files.
"""
from pathlib import Path
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
N_FILES = 3000  # 3000 files reproduced the old race 20/20 locally
RUNS = 20


def step_script():
    wf = yaml.load((WORKFLOWS / "python-ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = wf["jobs"]["ci"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "Run tests (pytest) or import-smoke")


class Tree:
    def __init__(self, n_tests):
        self.d = tempfile.mkdtemp()
        tdir = Path(self.d, "tests")
        tdir.mkdir()
        for i in range(n_tests):
            (tdir / f"test_{i}.py").write_text("")
        (tdir / "conftest.py").write_text("")
        bindir = Path(self.d, "bin")
        bindir.mkdir()
        stub = bindir / "python"
        stub.write_text('#!/bin/sh\necho "STUB-PYTHON $*"\nexit 0\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.path = f"{bindir}{os.pathsep}{os.environ['PATH']}"

    def run(self, script, tests="tests", imp="pkg"):
        env = dict(os.environ, PATH=self.path, TESTS=tests, IMPORT=imp)
        r = subprocess.run(["bash", "-e", "-c", script], cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        shutil.rmtree(self.d)


class Detection(unittest.TestCase):
    def test_many_test_files_take_pytest_branch_every_run(self):
        t = Tree(N_FILES)
        try:
            script = step_script()
            for i in range(RUNS):
                rc, out = t.run(script)
                self.assertEqual(rc, 0, f"run {i}: {out}")
                self.assertIn("[strength=real]", out, f"run {i}: {out}")
                self.assertIn("STUB-PYTHON -m pytest tests -q", out, f"run {i}: {out}")
                self.assertNotIn("import-smoke", out, f"run {i}: {out}")
        finally:
            t.close()

    def test_no_test_files_still_import_smokes(self):
        t = Tree(0)
        try:
            rc, out = t.run(step_script())
            self.assertEqual(rc, 0, out)
            self.assertIn("no tests -> import-smoke: import pkg [strength=weak]", out)
        finally:
            t.close()

    def test_missing_tests_dir_import_smokes(self):
        t = Tree(0)
        try:
            rc, out = t.run(step_script(), tests="nope")
            self.assertEqual(rc, 0, out)
            self.assertIn("[strength=weak]", out)
        finally:
            t.close()

    def test_weak_branch_over_dir_with_tests_is_red(self):
        # Mutation: blind the primary probe; the independent re-check must refuse the weak branch.
        script = step_script()
        blinded = re.sub(r'found="\$\(find .*?\)"', 'found=""', script, count=1)
        self.assertNotEqual(blinded, script, "primary probe shape changed; update this test")
        t = Tree(5)
        try:
            for imp in ("pkg", ""):
                rc, out = t.run(blinded, imp=imp)
                self.assertNotEqual(rc, 0, out)
                self.assertIn("refusing a vacuous green", out)
        finally:
            t.close()


class NoPipeIntoEarlyExitReader(unittest.TestCase):
    """Class detector: no floor workflow pipes into an early-exiting reader (`| grep -q`, `| head`)
    outside comments; under pipefail the writer's SIGPIPE flips the result."""

    def test_no_pipe_into_grep_q_or_head(self):
        bad = re.compile(r"\|\s*(grep\s+-[A-Za-z]*q|head\b)")
        hits = []
        for wf in sorted(WORKFLOWS.glob("*.yml")):
            for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if bad.search(line):
                    hits.append(f"{wf.name}:{n}: {line.strip()}")
        self.assertEqual(hits, [], "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
