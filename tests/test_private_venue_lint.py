"""Fleet R15 in the floor: sast.yml flags PRIVATE-repo jobs that can land on GitHub-hosted Linux.

Runs the EXACT `Private-repo runner venue lint (fleet R15)` step script from sast.yml (extracted from
the YAML, executed with bash) against real private-repo workflow bytes (tests/fixtures-r15, fetched
from Kyzcreig/fleet-ops-scripts @ fc95e16 and ANG-Ventures/hermes-home @ 32849577) and synthetic
cases, in warn and fail mode. Same rule as fleet-ops-scripts ci-speed-lint R15. Card t_b38fe79c.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
FX = Path(__file__).resolve().parent / "fixtures-r15"
STEP = "Private-repo runner venue lint (fleet R15)"
BASE = "on: {push: {paths: [x]}}\njobs:\n  j:\n    runs-on: RUNS\n    timeout-minutes: 5\n    steps:\n      - run: echo hi\n"

RED = ["ubuntu-latest", "[self-hosted, linux, ace-ai]", "blacksmith-4vcpu-ubuntu-2404-arm",
       "${{ vars.CI_RUNNER || 'ubuntu-latest' }}",
       "${{ fromJSON(vars.CI_RUNNER_LABELS || '[\"ubuntu-latest\"]') }}",
       "${{ needs.plan.outputs.runner }}", "{group: g}"]
GREEN = ["blacksmith-2vcpu-ubuntu-2404", "ace-ci-box", "[self-hosted, linux, ace-ci-box]", "macos-14",
         "windows-latest", "${{ vars.CI_HOME_RUNNER }}",
         "${{ vars.CI_RUNNER || 'blacksmith-4vcpu-ubuntu-2404' }}",
         "${{ github.event_name == 'push' && 'blacksmith-8vcpu-ubuntu-2404' || 'ace-ci-box' }}"]


def sast():
    return yaml.load((ROOT / ".github/workflows/sast.yml").read_text(), Loader=yaml.BaseLoader)


def step():
    steps = [s for s in sast()["jobs"]["sast"]["steps"] if s.get("name") == STEP]
    assert len(steps) == 1, "sast.yml must carry exactly one %r step" % STEP
    return steps[0]


def run_lint(texts, mode="warn"):
    run = step()["run"].replace("pip install -q \"PyYAML>=6\"", "true")
    with tempfile.TemporaryDirectory() as d:
        lst = Path(d) / "wf-changed.txt"
        names = []
        for i, text in enumerate(texts):
            p = Path(d) / ("w%d.yml" % i)
            p.write_text(text)
            names.append(str(p))
        lst.write_text("\n".join(names) + "\n")
        r = subprocess.run(["bash", "-c", run.replace("/tmp/wf-changed.txt", str(lst))],
                           capture_output=True, text=True, env=dict(os.environ, R15_MODE=mode))
    out = r.stdout + r.stderr
    return r.returncode, out, out.count("::error ") + out.count("::warning ")


class PrivateVenueLint(unittest.TestCase):
    def test_real_fixtures(self):
        fos = (FX / "fleet-ops-scripts_ci.yml@fc95e16.yml").read_text()
        rc, out, n = run_lint([fos], "fail")
        self.assertEqual((rc, n), (1, 4), out)
        rc, out, n = run_lint([(FX / "hermes-home_ci-dead-label-sweep-tests.yml@32849577.yml").read_text()], "fail")
        self.assertEqual((rc, n), (1, 1), out)
        green = (FX / "hermes-home_lost-session-burst-fleet.yml@32849577.yml").read_text()
        self.assertEqual(run_lint([green], "fail")[:3:2], (0, 0))
        mutant = green.replace("'blacksmith-2vcpu-ubuntu-2404'", "'ubuntu-latest'")
        self.assertNotEqual(mutant, green)
        self.assertEqual(run_lint([mutant], "fail")[::2], (1, 1))

    def test_warn_mode_annotates_but_passes(self):
        rc, out, n = run_lint([(FX / "fleet-ops-scripts_ci.yml@fc95e16.yml").read_text()], "warn")
        self.assertEqual((rc, n), (0, 4), out)
        self.assertIn("::warning ", out)
        self.assertNotIn("::error ", out)

    def test_venue_table(self):
        for ro in RED:
            self.assertEqual(run_lint([BASE.replace("RUNS", ro)], "fail")[0], 1, ro)
        for ro in GREEN:
            rc, out, _ = run_lint([BASE.replace("RUNS", ro)], "fail")
            self.assertEqual(rc, 0, ro + "\n" + out)

    def test_reusable_callers_and_matrix(self):
        head = "on: {push: {paths: [x]}}\njobs:\n  ci:\n"
        ext = head + "    uses: Kyzcreig/fleet-floor-workflows/.github/workflows/python-ci.yml@" + "a" * 40 + "\n"
        self.assertEqual(run_lint([ext], "fail")[0], 1)
        self.assertEqual(run_lint([ext + "    with:\n      runner: '[\"blacksmith-2vcpu-ubuntu-2404\"]'\n"], "fail")[0], 0)
        self.assertEqual(run_lint([head + "    uses: ./.github/workflows/t.yml\n"], "fail")[0], 0)
        mx = BASE.replace("RUNS", "${{ matrix.os }}").replace(
            "    timeout-minutes: 5\n", "    timeout-minutes: 5\n    strategy: {matrix: {os: [macos-14, ubuntu-latest]}}\n")
        self.assertEqual(run_lint([mx], "fail")[::2], (1, 1))

    def test_exempt_markers(self):
        red = BASE.replace("RUNS", "ubuntu-latest")
        for m in ("# venue-exempt: docker-in-docker", "# github-hosted: GPU-free smoke"):
            self.assertEqual(run_lint([red.replace("    runs-on:", "    %s\n    runs-on:" % m)], "fail")[0], 0, m)
        self.assertEqual(run_lint([red.replace("    runs-on:", "    # venue-exempt:\n    runs-on:")], "fail")[0], 1)
        self.assertEqual(run_lint(["# venue-exempt: whole file\n" + red], "fail")[0], 0)

    def test_step_gating(self):
        s = step()
        # GitHub evaluates any `${{` inside run: before bash sees it; the script must carry none.
        self.assertNotIn("$" + "{{", s["run"])
        self.assertIn("github.event.repository.private", s["if"])
        self.assertIn("github.event_name == 'pull_request'", s["if"])
        self.assertIn("inputs.private-venue-lint != 'off'", s["if"])
        self.assertEqual(sast()["on"]["workflow_call"]["inputs"]["private-venue-lint"]["default"], "warn")
        changed = [x for x in sast()["jobs"]["sast"]["steps"]
                   if x.get("name") == "Changed workflow files (path-filter lint input)"][0]
        self.assertIn("inputs.private-venue-lint != 'off'", changed["if"])


if __name__ == "__main__":
    unittest.main()
