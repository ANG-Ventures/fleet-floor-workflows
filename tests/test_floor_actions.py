"""Steps-mode composite actions for the sub-minute floor lints (card t_a9a0e768).

Blacksmith bills each JOB as ceil(runtime) x vCPU, so the ~10 s `ne_pair_floor` and `override_lint`
reusable-workflow jobs each bill a full 2 vCPU-minute. .github/actions/{ne-pair-floor,override-lint}
let a caller run the same lint as steps of a job it already pays for. This pins that:

  * the reusable workflows are untouched entry points (callers that do not opt in see no change);
  * the action runs the SAME scanner bytes as the reusable workflow (scripts/*.py == embedded copy);
  * the action's commands are the reusable workflow's commands (only /tmp -> $RUNNER_TEMP);
  * the action keeps its teeth: run on the red-team tree it fails, on the clean sample it passes;
  * the selftest exercises both actions live in ONE job.
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
ACTIONS = ROOT / ".github/actions"
WORKFLOWS = ROOT / ".github/workflows"


def load(p):
    return yaml.load(p.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def action(name):
    return load(ACTIONS / name / "action.yml")


def heredoc(run):
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", run, re.S)
    assert m, "heredoc not found"
    return m.group(1) + "\n"


def wf_step(wf, job, name):
    steps = [s for s in load(WORKFLOWS / wf)["jobs"][job]["steps"] if s.get("name") == name]
    assert len(steps) == 1, (wf, name)
    return steps[0]


def act_step(name, step):
    steps = [s for s in action(name)["runs"]["steps"] if s.get("name") == step]
    assert len(steps) == 1, (name, step)
    return steps[0]


def run_action_step(name, step, env, cwd):
    """Run a composite step's `run:` the way the runner does: bash -e, with github.action_path set."""
    s = act_step(name, step)
    e = dict(os.environ)
    for k, v in s.get("env", {}).items():
        e[k] = v.replace("${{ github.action_path }}", str(ACTIONS / name))
    e.update(env)
    return subprocess.run(["bash", "-e", "-c", s["run"]], cwd=cwd, env=e, capture_output=True, text=True, timeout=120)


class Contract(unittest.TestCase):
    def test_both_are_composite_and_resolve_the_script(self):
        for name, script in (("ne-pair-floor", "ne_pair_floor.py"), ("override-lint", "override_lint.py")):
            a = action(name)
            self.assertEqual(a["runs"]["using"], "composite", name)
            for s in a["runs"]["steps"]:
                self.assertEqual(s.get("shell"), "bash", (name, s.get("name")))
            path = (ACTIONS / name / "../../../scripts" / script).resolve()
            self.assertEqual(path, ROOT / "scripts" / script)
            self.assertTrue(path.is_file(), path)

    def test_override_lint_script_is_the_embedded_step(self):
        embedded = heredoc(wf_step("override-lint.yml", "override_lint", "Override lint")["run"])
        self.assertEqual((ROOT / "scripts/override_lint.py").read_text(encoding="utf-8"), embedded)

    def test_ne_pair_script_is_the_embedded_step(self):
        embedded = heredoc(wf_step("ne-pair-floor.yml", "ne_pair_floor", "NE pair floor")["run"])
        self.assertEqual((ROOT / "scripts/ne_pair_floor.py").read_text(encoding="utf-8"), embedded)

    def test_override_collect_step_is_the_workflow_step(self):
        wf = wf_step("override-lint.yml", "override_lint", "Collect PR diff and body")
        act = act_step("override-lint", "Collect PR diff and body")
        self.assertEqual(act["if"], wf["if"])
        want = wf["run"].replace("/tmp/override-lint.diff", '"$RUNNER_TEMP/override-lint.diff"')
        want = want.replace("/tmp/override-lint.body", '"$RUNNER_TEMP/override-lint.body"')
        self.assertEqual(act["run"], want)
        self.assertEqual(set(act["env"]), set(wf["env"]))
        self.assertEqual(act["env"]["GH_TOKEN"], "${{ inputs.github-token }}")

    def test_override_lint_step_gates_on_pr_events_like_the_workflow(self):
        wf = wf_step("override-lint.yml", "override_lint", "Override lint")
        self.assertEqual(act_step("override-lint", "Override lint")["if"], wf["if"])

    def test_inputs_mirror_the_workflow_defaults(self):
        for name, wf in (("ne-pair-floor", "ne-pair-floor.yml"), ("override-lint", "override-lint.yml")):
            w = load(WORKFLOWS / wf)["on"]["workflow_call"]["inputs"]
            a = action(name)["inputs"]
            for k, v in a.items():
                if k == "github-token":
                    continue
                self.assertIn(k, w, (name, k))
                self.assertEqual(v.get("default"), w[k].get("default"), (name, k))

    def test_reusable_entry_points_keep_their_frozen_jobs(self):
        self.assertEqual(list(load(WORKFLOWS / "ne-pair-floor.yml")["jobs"]), ["ne_pair_floor"])
        self.assertEqual(list(load(WORKFLOWS / "override-lint.yml")["jobs"]), ["override_lint"])

    def test_selftest_runs_both_actions_in_one_job(self):
        job = load(WORKFLOWS / "selftest.yml")["jobs"]["floor_steps"]
        uses = [s.get("uses") for s in job["steps"]]
        self.assertIn("./.github/actions/ne-pair-floor", uses)
        self.assertIn("./.github/actions/override-lint", uses)
        red = [s for s in job["steps"] if s.get("id") == "ne_red"][0]
        self.assertEqual(red["with"]["path"], "ne-pair-sample-redteam")


class Teeth(unittest.TestCase):
    def test_ne_pair_action_green_on_clean_sample(self):
        r = run_action_step("ne-pair-floor", "NE pair floor",
                            {"NE_PATH": "ne-pair-sample/good", "NE_WINDOW": "40", "NE_PAIR_FLOOR_ALLOW": ""}, ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 FAIL", r.stdout)

    def test_ne_pair_action_red_on_redteam(self):
        r = run_action_step("ne-pair-floor", "NE pair floor",
                            {"NE_PATH": "ne-pair-sample-redteam", "NE_WINDOW": "40", "NE_PAIR_FLOOR_ALLOW": ""}, ROOT)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ne_alone.sh:3 ne_tt_reminder", r.stdout)

    def _override(self, added, body):
        with tempfile.TemporaryDirectory() as d:
            diff = "\n".join(["diff --git a/hooks/x.py b/hooks/x.py", "new file mode 100644", "--- /dev/null",
                              "+++ b/hooks/x.py", "@@ -0,0 +1,%d @@" % len(added)] + ["+" + l for l in added]) + "\n"
            Path(d, "override-lint.diff").write_text(diff)
            Path(d, "override-lint.body").write_text(body)
            inputs = action("override-lint")["inputs"]
            return run_action_step("override-lint", "Override lint", {
                "RUNNER_TEMP": d, "OVERRIDE_LINT_SCOPE": inputs["scope"]["default"],
                "OVERRIDE_LINT_ALLOWLIST": "", "OVERRIDE_LINT_DOCS": d}, ROOT)

    def test_override_action_red_without_override_line(self):
        r = self._override(['raise RuntimeError("workers never pin")'], "no line here")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("override-lint FAIL", r.stdout)

    def test_override_action_green_with_override_line(self):
        r = self._override(['raise RuntimeError("workers never pin")'], "Override: none by design — fleet invariant")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
