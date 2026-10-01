"""Contract for the opt-in pytest-xdist worker count in python-ci.yml (t_90c7065d).

The test step is extracted from the workflow and run with a stub `python` on PATH that echoes its
argv, so the exact pytest command line for each `pytest-workers` value is asserted.
"""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def step(name):
    steps = workflow("python-ci.yml")["jobs"]["ci"]["steps"]
    return next(s for s in steps if s.get("name") == name)


class Tree:
    def __init__(self):
        self.d = tempfile.mkdtemp()
        tdir = Path(self.d, "tests")
        tdir.mkdir()
        (tdir / "test_a.py").write_text("")
        bindir = Path(self.d, "bin")
        bindir.mkdir()
        for exe in ("python", "pip"):
            stub = bindir / exe
            stub.write_text(f'#!/bin/sh\necho "STUB-{exe.upper()} $*"\nexit 0\n')
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.path = f"{bindir}{os.pathsep}{os.environ['PATH']}"

    def run(self, script, workers):
        env = dict(os.environ, PATH=self.path, TESTS="tests", IMPORT="pkg",
                   INSTALL_CMD="", PYTEST_WORKERS=workers)
        r = subprocess.run(["bash", "-e", "-c", script], cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        shutil.rmtree(self.d)


class PytestWorkersContract(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.close()

    def test_input_is_optional_and_defaults_serial(self):
        inp = workflow("python-ci.yml")["on"]["workflow_call"]["inputs"]["pytest-workers"]
        self.assertEqual(inp["type"], "string")
        self.assertEqual(inp["default"], "")
        self.assertNotEqual(inp.get("required"), "true")

    def test_both_steps_receive_the_input(self):
        for name in ("Install package", "Run tests (pytest) or import-smoke"):
            self.assertEqual(step(name)["env"]["PYTEST_WORKERS"], "${{ inputs.pytest-workers }}", name)

    def test_empty_runs_serial_pytest_unchanged(self):
        rc, out = self.t.run(step("Run tests (pytest) or import-smoke")["run"], "")
        self.assertEqual(rc, 0, out)
        self.assertIn("STUB-PYTHON -m pytest tests -q\n", out)
        self.assertNotIn("-n", out.split("STUB-PYTHON", 1)[1])

    def test_auto_and_integer_pass_dash_n(self):
        script = step("Run tests (pytest) or import-smoke")["run"]
        for workers, want in (("auto", "-n auto"), ("4", "-n 4")):
            rc, out = self.t.run(script, workers)
            self.assertEqual(rc, 0, out)
            self.assertIn(f"STUB-PYTHON -m pytest tests -q {want}\n", out)

    def test_invalid_values_fail_closed(self):
        script = step("Run tests (pytest) or import-smoke")["run"]
        for bad in ("0", "-1", "two", "4 --pdb", "08"):
            rc, out = self.t.run(script, bad)
            self.assertNotEqual(rc, 0, f"{bad!r}: {out}")
            self.assertIn("pytest-workers must be", out)
            self.assertNotIn("STUB-PYTHON -m pytest", out)

    def test_xdist_installed_only_when_opted_in(self):
        script = step("Install package")["run"]
        rc, out = self.t.run(script, "auto")
        self.assertEqual(rc, 0, out)
        self.assertIn("STUB-PIP install pytest-xdist", out)
        rc, out = self.t.run(script, "")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("pytest-xdist", out)

    def test_live_selftest_exercises_workers(self):
        jobs = workflow("selftest.yml")["jobs"]
        job = jobs["ci_pytest_workers"]
        self.assertEqual(job["uses"], jobs["ci"]["uses"])
        self.assertEqual(job["with"]["pytest-workers"], "auto")
        self.assertNotIn("pytest-workers", jobs["ci"]["with"])


if __name__ == "__main__":
    unittest.main()
