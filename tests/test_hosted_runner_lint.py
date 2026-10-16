"""hosted-runner-lint floor: private repos never resolve to a metered GitHub-hosted runner (t_a8dc95de).

Runs the EXACT scanner embedded in hosted-runner-lint.yml (extracted from the YAML): its own fixture self-test,
RED on a planted `runs-on: ubuntu-latest`, GREEN on a pool label / a `# hosted-ok:` waiver, SKIPPED for a public
repo; and pins the embedded copy byte-identical to scripts/hosted_runner_lint.py.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/hosted-runner-lint.yml"
SRC = ROOT / "scripts/hosted_runner_lint.py"


def embedded():
    wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
    steps = [s for s in wf["jobs"]["hosted_runner_lint"]["steps"] if s.get("name") == "Hosted runner lint"]
    assert len(steps) == 1
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", steps[0]["run"], re.S)
    assert m, "heredoc not found"
    return m.group(1) + "\n"


def run_embedded(cwd, *extra, private=None):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(embedded())
    env = dict(os.environ)
    env.pop("HOSTED_RUNNER_LINT_REPO_PRIVATE", None)
    if private is not None:
        env["HOSTED_RUNNER_LINT_REPO_PRIVATE"] = private
    return subprocess.run([sys.executable, f.name, *extra], cwd=cwd, capture_output=True, text=True, env=env)


def repo(tmp, text):
    d = Path(tmp, ".github", "workflows")
    d.mkdir(parents=True)
    (d / "ci.yml").write_text(text)
    return tmp


HEAD = "name: t\non: [push]\njobs:\n  a:\n"


class HostedRunnerLintFloor(unittest.TestCase):
    def test_embedded_copy_is_the_script(self):
        self.assertEqual(embedded(), SRC.read_text())

    def test_job_name_is_the_frozen_contract(self):
        wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(list(wf["jobs"]), ["hosted_runner_lint"])
        self.assertEqual(wf["jobs"]["hosted_runner_lint"]["name"], "hosted_runner_lint")

    def test_embedded_self_test_passes(self):
        r = run_embedded(ROOT, "--self-test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("self-test: PASS", r.stdout)

    def test_planted_hosted_label_is_red_with_the_line(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_embedded(repo(d, HEAD + "    runs-on: ubuntu-latest\n"), private="true")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("FINDING .github/workflows/ci.yml:5: ubuntu-latest in job a", r.stdout)

    def test_unknown_visibility_is_graded_private(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_embedded(repo(d, HEAD + "    runs-on: ${{ vars.R || 'ubuntu-latest' }}\n"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_pool_label_and_waiver_are_green(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_embedded(repo(d, HEAD + "    runs-on: ${{ vars.R || 'hermes-ci' }}\n  w:\n"
                                  "    runs-on: windows-latest  # hosted-ok: no self-hosted Windows runner\n"),
                             private="true")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_public_repo_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_embedded(repo(d, HEAD + "    runs-on: ubuntu-latest\n"), private="false")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped: public repo", r.stdout)

    def test_no_workflows_is_blind(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_embedded(d, private="true")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
