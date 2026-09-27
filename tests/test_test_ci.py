"""Contract for test-ci.yml, the default language-detected `test` floor component (t_1f7fff87).

The step script is extracted from the workflow and run with bash against throwaway trees:
FLOOR_TEST_DRY_RUN=1 proves which branch each tree takes; the shell branch also runs for real
to prove a syntax error turns the job red.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.load((ROOT / ".github/workflows/test-ci.yml").read_text(encoding="utf-8"),
                     Loader=yaml.BaseLoader)


def step_script():
    steps = workflow()["jobs"]["test"]["steps"]
    return next(s["run"] for s in steps if s.get("name") == "Detect and run")


def run_tree(files, dry=True, install=""):
    d = tempfile.mkdtemp()
    try:
        for rel, body in files.items():
            p = Path(d, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        env = dict(os.environ, FLOOR_TEST_DRY_RUN="1" if dry else "0", INSTALL_CMD=install)
        r = subprocess.run(["bash", "-e", "-c", step_script()], cwd=d, env=env,
                           capture_output=True, text=True, timeout=60)
        plan = [l.split(": ", 1)[1].split(" -> ")[0] for l in r.stdout.splitlines()
                if l.startswith("floor-test: ")]
        return r.returncode, plan, r.stdout + r.stderr
    finally:
        shutil.rmtree(d)


class Contract(unittest.TestCase):
    def test_job_name_and_inputs(self):
        wf = workflow()
        job = wf["jobs"]["test"]
        self.assertEqual(job["name"], "test")
        self.assertEqual(job["runs-on"], "${{ inputs.runner }}")
        self.assertEqual(job["timeout-minutes"], "${{ inputs.timeout-minutes }}")
        inputs = wf["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["runner"]["default"], "ubuntu-latest")
        self.assertIn("concurrency-suffix", inputs)

    def test_selftest_calls_it(self):
        jobs = yaml.load((ROOT / ".github/workflows/selftest.yml").read_text(encoding="utf-8"),
                         Loader=yaml.BaseLoader)["jobs"]
        uses = [j.get("uses") for j in jobs.values()]
        self.assertGreaterEqual(uses.count("./.github/workflows/test-ci.yml"), 3)


class Detection(unittest.TestCase):
    def test_python_with_tests_runs_pytest(self):
        rc, plan, out = run_tree({"pyproject.toml": "", "pkg/a.py": "x = 1\n",
                                  "tests/test_a.py": "def test_a():\n    pass\n"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(plan, ["python-install", "python-test"])

    def test_python_requirements_and_test_dir_named_test(self):
        rc, plan, out = run_tree({"requirements.txt": "", "app.py": "",
                                  "test/foo_test.py": ""})
        self.assertEqual(plan, ["python-install", "python-test"], out)

    def test_python_without_tests_builds_and_lints(self):
        rc, plan, out = run_tree({"tool.py": "print(1)\n", "tests/conftest.py": ""})
        self.assertEqual(plan, ["python-build", "python-lint"], out)

    def test_node_test_script(self):
        rc, plan, out = run_tree({"package.json": '{"scripts": {"test": "node --test"}}',
                                  "package-lock.json": "{}"})
        self.assertEqual(plan, ["node-install", "node-test"], out)

    def test_node_default_stub_is_not_a_test_script(self):
        stub = '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1", "build": "tsc"}}'
        rc, plan, out = run_tree({"package.json": stub})
        self.assertEqual(plan, ["node-install", "node-build"], out)

    def test_node_no_scripts_checks_syntax(self):
        rc, plan, out = run_tree({"package.json": "{}", "index.js": "1\n"})
        self.assertEqual(plan, ["node-install", "node-lint"], out)

    def test_shell_only(self):
        rc, plan, out = run_tree({"install.sh": "echo hi\n"})
        self.assertEqual(plan, ["shell-lint"], out)

    def test_vendored_trees_ignored(self):
        rc, plan, out = run_tree({"node_modules/x/y.py": "", "node_modules/x/z.sh": "",
                                  "README.md": ""})
        self.assertEqual(rc, 0, out)
        self.assertEqual(plan, ["none"], out)
        self.assertIn("::warning::", out)

    def test_polyglot_runs_every_branch(self):
        rc, plan, out = run_tree({"a.py": "", "package.json": '{"scripts":{"lint":"eslint ."}}',
                                  "bin/run.sh": ""})
        self.assertEqual(plan, ["python-build", "python-lint", "node-install", "node-lint",
                                "shell-lint"], out)


class RealRun(unittest.TestCase):
    def test_shell_syntax_error_is_red(self):
        rc, plan, out = run_tree({"ok.sh": "echo ok\n", "bad.sh": "if then fi (\n"}, dry=False)
        self.assertNotEqual(rc, 0, out)

    def test_shell_clean_is_green(self):
        rc, plan, out = run_tree({"ok.sh": "echo ok\n", "sub/two.sh": "for i in 1; do :; done\n"},
                                 dry=False)
        self.assertEqual(rc, 0, out)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_check_syntax_error_is_red(self):
        rc, plan, out = run_tree({"package.json": "{}", "bad.js": "function (\n"},
                                 dry=False, install="true")
        self.assertEqual(plan, ["install", "node-lint"], out)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("bad.js", out)


if __name__ == "__main__":
    unittest.main()
