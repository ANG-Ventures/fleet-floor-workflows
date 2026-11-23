"""Contract for python-ci.yml's opt-in pytest-dist, pytest-durations and test-timeout-minutes (t_ff163170).

The test step is extracted from the workflow and run with a stub `python` on PATH that echoes its
argv, so the exact pytest command line for each value is asserted. Defaults leave the command and the
step budget exactly as before.
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
STEP = "Run tests (pytest) or import-smoke"


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

    def run(self, workers="", dist="", durations="0"):
        env = dict(os.environ, PATH=self.path, TESTS="tests", IMPORT="pkg", PYTEST_WORKERS=workers,
                   PYTEST_DIST=dist, PYTEST_DURATIONS=durations, SHARD_TOTAL="1", SHARD_INDEX="1")
        r = subprocess.run(["bash", "-e", "-c", step(STEP)["run"]], cwd=self.d, env=env,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def close(self):
        shutil.rmtree(self.d)


class PytestDistDurationsContract(unittest.TestCase):
    def setUp(self):
        self.t = Tree()

    def tearDown(self):
        self.t.close()

    def test_inputs_are_optional_and_default_off(self):
        inputs = workflow("python-ci.yml")["on"]["workflow_call"]["inputs"]
        for name, typ, default in (("pytest-dist", "string", ""), ("pytest-durations", "number", "0"),
                                   ("test-timeout-minutes", "number", "0")):
            self.assertEqual(inputs[name]["type"], typ, name)
            self.assertEqual(inputs[name]["default"], default, name)
            self.assertNotEqual(inputs[name].get("required"), "true", name)

    def test_step_receives_the_inputs(self):
        env = step(STEP)["env"]
        self.assertEqual(env["PYTEST_DIST"], "${{ inputs.pytest-dist }}")
        self.assertEqual(env["PYTEST_DURATIONS"], "${{ inputs.pytest-durations }}")

    def test_step_timeout_falls_back_to_the_job_budget(self):
        self.assertEqual(
            step(STEP)["timeout-minutes"],
            "${{ inputs.test-timeout-minutes > 0 && inputs.test-timeout-minutes || inputs.timeout-minutes }}")

    def test_defaults_keep_the_command_unchanged(self):
        rc, out = self.t.run(workers="4")
        self.assertEqual(rc, 0, out)
        self.assertIn("STUB-PYTHON -m pytest tests -q -n 4\n", out)

    def test_dist_and_durations_are_passed(self):
        rc, out = self.t.run(workers="4", dist="worksteal", durations="30")
        self.assertEqual(rc, 0, out)
        self.assertIn("STUB-PYTHON -m pytest tests -q -n 4 --dist worksteal --durations=30\n", out)

    def test_durations_work_serially(self):
        rc, out = self.t.run(durations="10")
        self.assertEqual(rc, 0, out)
        self.assertIn("STUB-PYTHON -m pytest tests -q --durations=10\n", out)

    def test_dist_without_workers_fails_closed(self):
        rc, out = self.t.run(dist="worksteal")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("pytest-dist needs pytest-workers", out)
        self.assertNotIn("STUB-PYTHON -m pytest", out)

    def test_invalid_values_fail_closed(self):
        for kw, msg in (({"dist": "each"}, "pytest-dist must be"), ({"dist": "worksteal --pdb"}, "pytest-dist must be"),
                        ({"durations": "-1"}, "pytest-durations must be"), ({"durations": "07"}, "pytest-durations must be"),
                        ({"durations": "5 --pdb"}, "pytest-durations must be")):
            rc, out = self.t.run(workers="2", **kw)
            self.assertNotEqual(rc, 0, f"{kw}: {out}")
            self.assertIn(msg, out)
            self.assertNotIn("STUB-PYTHON -m pytest", out)

    def test_step_timeout_must_be_a_whole_number_of_minutes(self):
        # The runner truncates step timeouts to whole minutes: 0.5 would silently mean no step timeout.
        script = step("Install package")["run"]
        for good in ("0", "1", "150"):
            env = dict(os.environ, PATH=self.t.path, INSTALL_CMD="true", PYTEST_WORKERS="",
                       TEST_TIMEOUT_MINUTES=good)
            r = subprocess.run(["bash", "-e", "-c", script], cwd=self.t.d, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, f"{good}: {r.stdout}{r.stderr}")
        for bad in ("0.5", "-1", "1.5", "07", "1e2"):
            env = dict(os.environ, PATH=self.t.path, INSTALL_CMD="true", PYTEST_WORKERS="",
                       TEST_TIMEOUT_MINUTES=bad)
            r = subprocess.run(["bash", "-e", "-c", script], cwd=self.t.d, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("test-timeout-minutes must be 0 or a positive integer", r.stdout + r.stderr)
        self.assertEqual(step("Install package")["env"]["TEST_TIMEOUT_MINUTES"],
                         "${{ inputs.test-timeout-minutes }}")

    def test_live_selftest_exercises_the_inputs(self):
        job = workflow("selftest.yml")["jobs"]["ci_pytest_workers"]
        self.assertEqual(job["with"]["pytest-dist"], "worksteal")
        self.assertEqual(job["with"]["pytest-durations"], "5")
        self.assertEqual(job["with"]["test-timeout-minutes"], "15")


if __name__ == "__main__":
    unittest.main()
