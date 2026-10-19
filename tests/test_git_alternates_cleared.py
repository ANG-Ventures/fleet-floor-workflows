"""Contract: a reusable floor workflow that checks out the caller's repo clears pool git alternates (t_d1f1cac1).

ci-box org pools export GIT_ALTERNATE_OBJECT_DIRECTORIES=<hermes-agent mirror>:<hermes-home mirror> to every
job container. For any other repo the checkout fetch advertises ~100k foreign refs as `have`s: prism-router's
`sast` (52 s) and `secret_scan` (45 s) checkouts, vs 3 s once the caller cleared it (prism-router#617). A reusable
workflow does not inherit the caller's workflow-level env, so each one must clear it itself.
"""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def reusable_with_checkout():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        wf = yaml.load(text, Loader=yaml.BaseLoader)
        on = wf.get("on") or {}
        if isinstance(on, dict) and "workflow_call" in on and "actions/checkout" in text:
            yield path.name, wf


class GitAlternatesCleared(unittest.TestCase):
    def test_covers_the_known_checkout_workflows(self):
        names = {n for n, _ in reusable_with_checkout()}
        self.assertTrue({"sast.yml", "secret-scan.yml"} <= names, names)

    def test_workflow_env_clears_alternates(self):
        for name, wf in reusable_with_checkout():
            with self.subTest(workflow=name):
                env = wf.get("env") or {}
                self.assertIn("GIT_ALTERNATE_OBJECT_DIRECTORIES", env, f"{name}: no workflow-level env clearing it")
                self.assertEqual(env["GIT_ALTERNATE_OBJECT_DIRECTORIES"], "")

    def test_no_job_or_step_reinstates_alternates(self):
        for name, wf in reusable_with_checkout():
            for jid, job in (wf.get("jobs") or {}).items():
                scopes = [job.get("env") or {}] + [(s or {}).get("env") or {} for s in job.get("steps") or []]
                for env in scopes:
                    with self.subTest(workflow=name, job=jid):
                        self.assertEqual(env.get("GIT_ALTERNATE_OBJECT_DIRECTORIES", ""), "")


if __name__ == "__main__":
    unittest.main()
