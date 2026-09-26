"""Fleet R14 in the floor: sast.yml fails a PR that adds/modifies an unfiltered push/PR workflow.

Runs the EXACT `Workflow path-filter lint (fleet R14)` step script from sast.yml (extracted from the
YAML, executed with bash) against red and green workflow fixtures. Card t_e47ee8bf.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
STEP = "Workflow path-filter lint (fleet R14)"

CASES = {
    "red_push.yml": ("on:\n  push:\n    branches: [main]\njobs: {}\n", 1),
    "red_list.yml": ("on: [push, pull_request]\njobs: {}\n", 1),
    "red_prt.yml": ("on: pull_request_target\njobs: {}\n", 1),
    "red_half.yml": ("on: {push: {paths: [a]}, pull_request: {}}\njobs: {}\n", 1),
    "red_bare_marker.yml": ("# path-filter-exempt:\non: push\njobs: {}\n", 1),
    "green_filtered.yml": ("on:\n  push:\n    paths: ['src/**']\n  pull_request:\n"
                           "    paths-ignore: ['docs/**']\njobs: {}\n", 0),
    "green_exempt.yml": ("# path-filter-exempt: required check\non: [push, pull_request]\njobs: {}\n", 0),
    "green_tags.yml": ("on: {push: {tags: ['v*']}}\njobs: {}\n", 0),
    "green_other.yml": ("on: {workflow_run: {workflows: [CI]}, schedule: [{cron: '0 0 * * *'}]}\njobs: {}\n", 0),
}


def step_script():
    wf = yaml.load((ROOT / ".github/workflows/sast.yml").read_text(), Loader=yaml.BaseLoader)
    steps = [s for s in wf["jobs"]["sast"]["steps"] if s.get("name") == STEP]
    assert len(steps) == 1, "sast.yml must carry exactly one %r step" % STEP
    return steps[0]


def run_lint(files):
    run = step_script()["run"].replace("pip install -q \"PyYAML>=6\"", "true")
    with tempfile.TemporaryDirectory() as d:
        lst = Path(d) / "wf-changed.txt"
        names = []
        for name, (text, _) in files.items():
            (Path(d) / name).write_text(text)
            names.append(str(Path(d) / name))
        lst.write_text("\n".join(names) + "\n")
        r = subprocess.run(["bash", "-c", run.replace("/tmp/wf-changed.txt", str(lst))],
                           capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class WorkflowPathFilterLint(unittest.TestCase):
    def test_each_case_red_or_green(self):
        for name, case in CASES.items():
            rc, out = run_lint({name: case})
            self.assertEqual(rc, case[1], "%s -> rc %d\n%s" % (name, rc, out))

    def test_step_is_pr_only_and_default_on(self):
        s = step_script()
        self.assertIn("github.event_name == 'pull_request'", s["if"])
        self.assertIn("inputs.workflow-path-filter-lint", s["if"])
        inp = yaml.load((ROOT / ".github/workflows/sast.yml").read_text(),
                        Loader=yaml.BaseLoader)["on"]["workflow_call"]["inputs"]
        self.assertEqual(inp["workflow-path-filter-lint"]["default"], "true")

    def test_floor_repo_own_workflows_pass(self):
        files = {p.name: (p.read_text(), 0) for p in (ROOT / ".github/workflows").glob("*.yml")}
        rc, out = run_lint(files)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
