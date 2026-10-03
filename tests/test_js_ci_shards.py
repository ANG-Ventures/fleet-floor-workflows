"""Contract for the opt-in js-ci `shards` input (t_a1dd1955).

Why: claude-bpx (p50 963 s) and ha-command-router (p50 1479 s) run one serial js-ci job on every
PR. On Blacksmith fan-out is billed in vCPU-minutes, so splitting the run is free until per-job setup
dominates. Default 1 must keep the single `ci` job and the exact pre-shard commands.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    # BaseLoader preserves GitHub's `on` key instead of YAML 1.1's boolean.
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def run_step():
    for step in workflow("js-ci.yml")["jobs"]["ci"]["steps"]:
        if step.get("name") == "Run tests (path-signal) or smoke":
            return step
    raise AssertionError("test step missing")


class JsShardsContract(unittest.TestCase):
    def test_default_is_one_and_optional(self):
        inputs = workflow("js-ci.yml")["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["shards"]["type"], "number")
        self.assertEqual(inputs["shards"]["default"], "1")
        self.assertNotEqual(inputs["shards"].get("required"), "true")

    def test_matrix_table_is_exactly_one_to_n(self):
        job = workflow("js-ci.yml")["jobs"]["ci"]
        expr = job["strategy"]["matrix"]["shard"]
        self.assertEqual(job["strategy"]["fail-fast"], "false")
        start = expr.index("fromJSON('") + len("fromJSON('")
        table = __import__("json").loads(expr[start:expr.index("')", start)])
        self.assertEqual(sorted(table, key=int), [str(n) for n in range(1, 17)])
        for n, shards in table.items():
            self.assertEqual(shards, list(range(1, int(n) + 1)), n)

    def test_name_is_ci_at_one_shard(self):
        # The name must reference matrix.* or GitHub appends the matrix value (`ci (1)`), which
        # would rename the frozen `ci` check-run for every existing caller.
        name = workflow("js-ci.yml")["jobs"]["ci"]["name"]
        self.assertIn("matrix.shard", name)
        self.assertIn("inputs.shards > 1", name)
        self.assertTrue(name.rstrip().endswith("|| 'ci' }}"), name)

    def _run(self, total, index, has_script):
        """Execute the real step script with stub npm/node that record their argv."""
        script = run_step()["run"]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "test").mkdir()
            (d / "test" / "a.test.js").write_text("", encoding="utf-8")
            if has_script:
                (d / "package.json").write_text('{"scripts":{"test":"x"}}', encoding="utf-8")
            bindir = d / "bin"
            bindir.mkdir()
            log = d / "calls.log"
            for tool in ("npm", "node"):
                stub = bindir / tool
                if tool == "node":
                    # Stub node keeps the package.json probe working: run real node for `-e`.
                    real = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
                    body = '#!/bin/bash\nif [ "$1" = "-e" ]; then exec %s "$@"; fi\necho "node $*" >> %s\n' % (real, log)
                else:
                    body = '#!/bin/bash\necho "npm $*" >> %s\n' % log
                stub.write_text(body, encoding="utf-8")
                stub.chmod(0o755)
            env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]),
                       SHARD_TOTAL=str(total), SHARD_INDEX=str(index))
            r = subprocess.run(["bash", "-c", script], cwd=d, env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            return log.read_text(encoding="utf-8").strip() if log.exists() else ""

    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "node missing")
    def test_one_shard_runs_the_pre_shard_commands(self):
        self.assertEqual(self._run(1, 1, True), "npm test")
        self.assertEqual(self._run(1, 1, False), "node --test")

    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "node missing")
    def test_n_shards_pass_the_node_shard_flag(self):
        self.assertEqual(self._run(3, 2, True), "npm test -- --test-shard=2/3")
        self.assertEqual(self._run(3, 3, False), "node --test --test-shard=3/3")

    def test_live_selftest_exercises_shards(self):
        jobs = workflow("selftest.yml")["jobs"]
        default = jobs["js_ci"]
        sharded = jobs["js_ci_shards"]
        self.assertNotIn("shards", default["with"])
        self.assertEqual(sharded["uses"], default["uses"])
        self.assertEqual(sharded["with"]["shards"], "2")
        self.assertNotEqual(default["with"]["concurrency-suffix"], sharded["with"]["concurrency-suffix"])


if __name__ == "__main__":
    unittest.main()
