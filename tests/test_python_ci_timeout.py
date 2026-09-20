"""Contract for the opt-in Python CI job timeout (not a pytest timeout)."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader preserves GitHub's `on` key instead of YAML 1.1's boolean.
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


class PythonTimeoutContract(unittest.TestCase):
    def test_existing_callers_keep_twenty_minutes(self):
        inputs = workflow("python-ci.yml")["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["timeout-minutes"]["type"], "number")
        self.assertEqual(inputs["timeout-minutes"]["default"], "20")
        self.assertNotEqual(inputs["timeout-minutes"].get("required"), "true")

    def test_job_consumes_timeout_input(self):
        job = workflow("python-ci.yml")["jobs"]["ci"]
        self.assertEqual(job["timeout-minutes"], "${{ inputs.timeout-minutes }}")
        self.assertEqual(job["name"], "ci")

    def test_live_selftest_exercises_default_and_override(self):
        jobs = workflow("selftest.yml")["jobs"]
        default = jobs["ci"]
        override = jobs["ci_timeout_override"]
        self.assertNotIn("timeout-minutes", default["with"])
        self.assertEqual(override["uses"], default["uses"])
        self.assertEqual(override["with"]["timeout-minutes"], "40")
        self.assertNotEqual(
            default["with"]["concurrency-suffix"],
            override["with"]["concurrency-suffix"],
        )


if __name__ == "__main__":
    unittest.main()
