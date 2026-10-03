"""Contract for the opt-in python-ci `shards` input (t_a9a0e768), mirroring js-ci's (t_a1dd1955).

Why: ha-command-router's `ci / ci` runs one 8-vCPU xdist job (~6.5 min wall, 368 s of it pytest).
Splitting it into N jobs that each run 1/N of the collected tests cuts the wall, and a caller fan-in
job keeps the frozen required `ci / ci` name. Default 1 must keep the single `ci` job and the exact
pre-shard pytest command.

The real step script is extracted from the workflow and run with a REAL pytest (the interpreter
running this test) over a fixture suite, so the partition is proven on pytest's own collection.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
STEP = "Run tests (pytest) or import-smoke"


def workflow(name):
    # BaseLoader preserves GitHub's `on` key instead of YAML 1.1's boolean.
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def step():
    return next(s for s in workflow("python-ci.yml")["jobs"]["ci"]["steps"] if s.get("name") == STEP)


def have_pytest():
    return subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0


class Suite:
    """A fixture repo with 7 tests across 3 files; `python` on PATH is this interpreter."""

    def __init__(self):
        self.d = Path(tempfile.mkdtemp())
        t = self.d / "tests"
        t.mkdir()
        (t / "test_a.py").write_text("def test_a1(): pass\ndef test_a2(): pass\ndef test_a3(): pass\n")
        (t / "test_b.py").write_text("def test_b1(): pass\ndef test_b2(): pass\n")
        (t / "test_c.py").write_text("import pytest\n@pytest.mark.parametrize('x', [1, 2])\ndef test_c(x): pass\n")
        bindir = self.d / "bin"
        bindir.mkdir()
        # A wrapper, not a symlink: a venv's python is itself a symlink, and following it drops the
        # venv (and its pytest).
        py = bindir / "python"
        py.write_text('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        py.chmod(0o755)
        self.path = "%s%s%s" % (bindir, os.pathsep, os.environ["PATH"])

    def run(self, total, index):
        env = dict(os.environ, PATH=self.path, TESTS="tests", IMPORT="", PYTEST_WORKERS="",
                   SHARD_TOTAL=str(total), SHARD_INDEX=str(index), RUNNER_TEMP=str(self.d / "rt"),
                   PYTEST_ADDOPTS="-p no:cacheprovider -rA")
        env.pop("PYTHONPATH", None)
        r = subprocess.run(["bash", "-e", "-c", step()["run"]], cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=120)
        passed = sorted(re.findall(r"^PASSED (\S+)", r.stdout, re.M))
        return r.returncode, passed, r.stdout + r.stderr

    def close(self):
        shutil.rmtree(self.d)


class PythonShardsContract(unittest.TestCase):
    def test_default_is_one_and_optional(self):
        inputs = workflow("python-ci.yml")["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["shards"]["type"], "number")
        self.assertEqual(inputs["shards"]["default"], "1")
        self.assertNotEqual(inputs["shards"].get("required"), "true")

    def test_matrix_table_is_exactly_one_to_n_and_matches_js_ci(self):
        job = workflow("python-ci.yml")["jobs"]["ci"]
        expr = job["strategy"]["matrix"]["shard"]
        self.assertEqual(job["strategy"]["fail-fast"], "false")
        self.assertEqual(expr, workflow("js-ci.yml")["jobs"]["ci"]["strategy"]["matrix"]["shard"])
        start = expr.index("fromJSON('") + len("fromJSON('")
        table = json.loads(expr[start:expr.index("')", start)])
        self.assertEqual(sorted(table, key=int), [str(n) for n in range(1, 17)])
        for n, shards in table.items():
            self.assertEqual(shards, list(range(1, int(n) + 1)), n)

    def test_name_is_ci_at_one_shard(self):
        # GitHub appends the matrix value (`ci (1)`) unless the name references matrix.*, which
        # would rename the frozen `ci` check-run for every existing caller.
        name = workflow("python-ci.yml")["jobs"]["ci"]["name"]
        self.assertIn("matrix.shard", name)
        self.assertIn("inputs.shards > 1", name)
        self.assertTrue(name.rstrip().endswith("|| 'ci' }}"), name)

    def test_one_shard_keeps_the_pre_shard_command(self):
        run = step()["run"]
        self.assertIn('python -m pytest "$TESTS" -q ${xdist[@]+"${xdist[@]}"} ${shard[@]+"${shard[@]}"}', run)
        self.assertIn('if [ "${SHARD_TOTAL:-1}" != "1" ]; then', run)

    @unittest.skipUnless(have_pytest(), "pytest missing")
    def test_shards_partition_the_suite(self):
        s = Suite()
        try:
            rc, whole, out = s.run(1, 1)
            self.assertEqual(rc, 0, out)
            self.assertEqual(len(whole), 7, out)
            self.assertNotIn("shard 1/1", out)
            for total in (2, 3, 7):
                got = []
                for i in range(1, total + 1):
                    rc, part, out = s.run(total, i)
                    self.assertEqual(rc, 0, out)
                    self.assertIn("shard %d/%d" % (i, total), out)
                    self.assertTrue(part, out)
                    got += part
                # union == whole suite, and no test ran twice
                self.assertEqual(sorted(got), whole, total)
        finally:
            s.close()

    @unittest.skipUnless(have_pytest(), "pytest missing")
    def test_empty_shard_is_red(self):
        s = Suite()
        try:
            rc, part, out = s.run(8, 8)  # 7 tests, 8 shards: shard 8 gets nothing -> pytest exit 5
            self.assertNotEqual(rc, 0, out)
            self.assertEqual(part, [])
        finally:
            s.close()

    def test_live_selftest_exercises_shards(self):
        jobs = workflow("selftest.yml")["jobs"]
        default = jobs["ci"]
        sharded = jobs["ci_shards"]
        self.assertNotIn("shards", default["with"])
        self.assertEqual(sharded["uses"], default["uses"])
        self.assertEqual(sharded["with"]["shards"], "2")
        self.assertNotEqual(default["with"]["concurrency-suffix"], sharded["with"]["concurrency-suffix"])


if __name__ == "__main__":
    unittest.main()
