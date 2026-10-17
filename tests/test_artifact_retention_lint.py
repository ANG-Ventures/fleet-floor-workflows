"""Artifact retention lint in the floor: sast.yml fails a PR whose changed workflow uploads an artifact
with no retention-days (the repo default, 90 days here), 0, or > 7 days. Card t_eade26e1.

Runs the EXACT `Artifact retention lint` step script from sast.yml (extracted from the YAML, executed
with bash) against red and green workflow fixtures, the same way test_workflow_path_filter_lint.py
runs the R14 step.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
STEP = "Artifact retention lint"

HEAD = "on: [push]\njobs:\n  a:\n    runs-on: x\n    steps:\n"
UP = "      - name: up\n        uses: actions/upload-artifact@v4\n        with:\n          name: j\n          path: j.xml\n"

CASES = {
    # the 2026-10-16 shape: a junit upload with no retention-days sat for the repo default
    "red_missing.yml": (HEAD + UP, 1),
    "red_missing_dash_uses.yml": (HEAD + "      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4\n"
                                  "        with: {name: j, path: j.xml}\n", 1),
    "red_no_with.yml": (HEAD + "      - uses: actions/upload-artifact@v4\n      - run: echo next\n", 1),
    "red_30.yml": (HEAD + UP + "          retention-days: 30\n", 1),
    "red_zero.yml": (HEAD + UP + "          retention-days: 0\n", 1),
    "red_only_in_comment.yml": (HEAD + UP + "          # retention-days: 2\n", 1),
    "red_next_step_has_it.yml": (HEAD + UP + "      - uses: actions/upload-artifact@v4\n        with:\n"
                                 "          retention-days: 2\n", 1),
    "red_empty_waiver.yml": (HEAD + UP + "          retention-days: 30  # artifact-retention-ok:\n", 1),
    # Prism 5e3bf8caa26f: a flow-style step mapping is a step
    "red_flow_step_missing.yml": (HEAD + "      - {uses: actions/upload-artifact@v4, with: {name: logs, path: logs}}\n", 1),
    # Prism 85420210ccdb: retention text in the step NAME is not the input
    "red_name_says_2.yml": (HEAD + "      - name: 'Upload logs, retention-days: 2'\n        uses: actions/upload-artifact@v4\n"
                            "        with:\n          name: j\n          retention-days: 30\n", 1),
    "red_name_says_2_no_input.yml": (HEAD + "      - name: 'Upload logs, retention-days: 2'\n"
                                     "        uses: actions/upload-artifact@v4\n        with: {name: j}\n", 1),
    "red_unparsable.yml": (HEAD + "      - uses: actions/upload-artifact@v4\n        with: [\n", 1),
    "green_flow_step.yml": (HEAD + "      - {uses: actions/upload-artifact@v4, with: {name: logs, retention-days: 2}}\n", 0),
    "green_2.yml": (HEAD + UP + "          retention-days: 2\n", 0),
    "green_7_quoted.yml": (HEAD + UP + "          retention-days: '7'  # dist; GHCR holds it\n", 0),
    "green_flow.yml": (HEAD + "      - uses: actions/upload-artifact@v4\n        with: {name: j, path: j.xml, retention-days: 1}\n", 0),
    "green_expr.yml": (HEAD + UP + "          retention-days: ${{ inputs.keep }}\n", 0),
    "green_waived.yml": (HEAD + UP + "          retention-days: 30  # artifact-retention-ok: cad.ace reads the newest green main run\n", 0),
    "green_download_only.yml": (HEAD + "      - uses: actions/download-artifact@v4\n        with: {name: j}\n", 0),
    "green_no_steps.yml": ("on: [push]\njobs: {}\n", 0),
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


class ArtifactRetentionLint(unittest.TestCase):
    def test_each_case_red_or_green(self):
        for name, case in CASES.items():
            rc, out = run_lint({name: case})
            self.assertEqual(rc, case[1], "%s -> rc %d\n%s" % (name, rc, out))

    def test_finding_names_the_line(self):
        rc, out = run_lint({"red_missing.yml": CASES["red_missing.yml"]})
        self.assertEqual(rc, 1, out)
        self.assertIn("red_missing.yml,line=6::upload-artifact step: no with.retention-days", out)
        self.assertIn("artifact retention lint: 1 finding(s)", out)

    def test_step_is_pr_only_and_default_on(self):
        s = step_script()
        self.assertIn("github.event_name == 'pull_request'", s["if"])
        self.assertIn("inputs.artifact-retention-lint", s["if"])
        wf = yaml.load((ROOT / ".github/workflows/sast.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(wf["on"]["workflow_call"]["inputs"]["artifact-retention-lint"]["default"], "true")
        # the changed-file list it reads is written even when R14 is switched off
        lst = [x for x in wf["jobs"]["sast"]["steps"] if x.get("name") == "Changed workflow files (path-filter lint input)"]
        self.assertIn("inputs.artifact-retention-lint", lst[0]["if"])

    def test_floor_repo_own_workflows_pass(self):
        files = {p.name: (p.read_text(), 0) for p in (ROOT / ".github/workflows").glob("*.yml")}
        rc, out = run_lint(files)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
