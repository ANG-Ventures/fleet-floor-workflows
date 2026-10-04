"""Contract: a reusable floor workflow never cancels a caller's push:main backstop (t_05a330da).

A reusable workflow's `concurrency` applies to every caller run. Keyed on `github.ref` with
`cancel-in-progress: true`, the next push to main cancelled the previous push:main run of every
caller (hermes-home 10-03: 10/10 cancelled push:main runs in 24 h were ne-pair-floor; ci-speed-lint
R18). PR runs still cancel superseded PR runs; any other event gets a per-run group.
"""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PER_EVENT_KEY = "${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
PR_ONLY_CANCEL = "${{ github.event_name == 'pull_request' }}"


def reusable_workflows():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        on = wf.get("on") or {}
        if isinstance(on, dict) and "workflow_call" in on:
            yield path.name, wf


class ConcurrencyBackstopContract(unittest.TestCase):
    def test_every_reusable_workflow_is_covered(self):
        names = [n for n, _ in reusable_workflows()]
        self.assertGreaterEqual(len(names), 15, names)

    def test_group_is_per_run_off_pull_request(self):
        for name, wf in reusable_workflows():
            conc = wf.get("concurrency")
            if conc is None:
                continue
            with self.subTest(workflow=name):
                group = conc["group"]
                self.assertTrue(group.endswith(PER_EVENT_KEY), group)
                self.assertIn("${{ inputs.concurrency-suffix }}", group)
                self.assertNotIn("${{ github.ref }}", group)

    def test_cancel_only_on_pull_request(self):
        for name, wf in reusable_workflows():
            conc = wf.get("concurrency")
            if conc is None:
                continue
            with self.subTest(workflow=name):
                self.assertEqual(conc["cancel-in-progress"], PR_ONLY_CANCEL)

    def test_no_job_level_ref_keyed_cancel(self):
        for name, wf in reusable_workflows():
            for jid, job in (wf.get("jobs") or {}).items():
                conc = job.get("concurrency") if isinstance(job, dict) else None
                if not isinstance(conc, dict):
                    continue
                with self.subTest(workflow=name, job=jid):
                    self.assertNotEqual(str(conc.get("cancel-in-progress")).lower(), "true")


if __name__ == "__main__":
    unittest.main()
