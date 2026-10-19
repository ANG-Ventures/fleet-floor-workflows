"""templates/floors.yml: one caller for the lint floors (t_8fc09421).

Locks: the caller job ids equal the separate callers' ids (so check contexts `<job> / <job>` do not
change), every `uses:` is a pinned reusable workflow of this repo, `edited` stays in the trigger
(override-lint + regression-test-lint read the PR body), the tree-scan floor skips `edited`, the
body-reading floors are PR-only, and a same-head twin / push:main run never shares a cancel group.
"""
from pathlib import Path
import re
import unittest

import yaml

from test_workflow_path_filter_lint import run_lint

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/floors.yml"
JOBS = {
    "override_lint": "override-lint.yml",
    "regression_test_lint": "regression-test-lint.yml",
    "ne_pair_floor": "ne-pair-floor.yml",
}


def load():
    return yaml.load(TEMPLATE.read_text(), Loader=yaml.BaseLoader)


class FloorsCaller(unittest.TestCase):
    def test_job_ids_match_reusable_job_names(self):
        jobs = load()["jobs"]
        self.assertEqual(set(jobs), set(JOBS))
        for job, wf in JOBS.items():
            uses = jobs[job]["uses"]
            self.assertEqual(
                uses, "ANG-Ventures/fleet-floor-workflows/.github/workflows/%s@__FLOOR_SHA__" % wf)
            reusable = yaml.load((ROOT / ".github/workflows" / wf).read_text(), Loader=yaml.BaseLoader)
            self.assertIn(job, reusable["jobs"], "%s: context would become %s / <other>" % (wf, job))

    def test_trigger_is_union_with_edited_and_no_filter(self):
        on = load()["on"]
        self.assertEqual(set(on), {"pull_request", "push"})
        self.assertIn("edited", on["pull_request"]["types"])
        self.assertEqual(on["push"]["branches"], ["main"])
        for ev, spec in on.items():
            self.assertFalse({"paths", "paths-ignore"} & set(spec or {}), ev)

    def test_edited_runs_only_body_readers(self):
        jobs = load()["jobs"]
        self.assertEqual(jobs["ne_pair_floor"]["if"], "github.event.action != 'edited'")
        for job in ("override_lint", "regression_test_lint"):
            self.assertEqual(jobs[job]["if"], "github.event_name == 'pull_request'", job)

    def test_concurrency_never_shares_a_group_across_heads_or_push(self):
        conc = load()["concurrency"]
        group = conc["group"]
        self.assertIn("github.run_id", group)
        self.assertEqual(set(re.findall(r"action == '(\w+)'", group)), {"opened", "synchronize"})
        self.assertEqual(conc["cancel-in-progress"], "${{ github.event_name == 'pull_request' }}")

    def test_passes_fleet_r14(self):
        rc, out = run_lint({"floors.yml": (TEMPLATE.read_text(), 0)})
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
