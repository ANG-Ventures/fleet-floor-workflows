"""ne-pair-floor: GrowthBook-off family => TOTAL_TOKENS_REMINDER=off + GB_DISK pair + seed step (t_ad5331c9).

Runs the EXACT scanner embedded in ne-pair-floor.yml (extracted from the YAML) against the clean sample and
the red-team fixtures, and pins the embedded copy byte-identical to scripts/ne_pair_floor.py.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/ne-pair-floor.yml"
SRC = ROOT / "scripts/ne_pair_floor.py"
sys.path.insert(0, str(ROOT / "scripts"))
import ne_pair_floor as npf  # noqa: E402

NE = "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
GB = "CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF"
TT = "CLAUDE_CODE_TOTAL_TOKENS_REMINDER"


def embedded():
    wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
    steps = [s for s in wf["jobs"]["ne_pair_floor"]["steps"] if s.get("name") == "NE pair floor"]
    assert len(steps) == 1
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", steps[0]["run"], re.S)
    assert m, "heredoc not found"
    return m.group(1) + "\n"


def run_embedded(path, *extra):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(embedded())
    return subprocess.run([sys.executable, f.name, str(path), *extra], capture_output=True, text=True)


def checks(src, ext=".sh"):
    return sorted({c for _, c, _ in npf.scan_text(src, 40, ext)})


class Contract(unittest.TestCase):
    def test_embedded_copy_is_the_script(self):
        self.assertEqual(embedded(), SRC.read_text())

    def test_frozen_job_name(self):
        wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(list(wf["jobs"]), ["ne_pair_floor"])


class RedGreen(unittest.TestCase):
    def test_clean_sample_green(self):
        r = run_embedded(ROOT / "ne-pair-sample/good")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 FAIL", r.stdout)

    def test_redteam_red_every_shape(self):
        r = run_embedded(ROOT / "ne-pair-sample-redteam", "--github")
        self.assertEqual(r.returncode, 1, r.stdout)
        for want in ("ne_alone.sh:3 ne_tt_reminder", "ne_alone.sh:3 ne_gb_pair", "ne_alone.sh:3 ne_seed_missing",
                     "pair_no_tt.js:2 ne_tt_reminder", "growthbook_off.yaml:2 ne_gb_pair",
                     "dnt.plist:2 ne_tt_reminder", "no_seed.py:2 ne_seed_missing", "allowlist_bad"):
            self.assertIn(want, r.stdout)
        self.assertIn("::error file=ne_alone.sh,line=3", r.stdout)

    def test_prism_arm_c_shape(self):
        # the live miss (prism-router deploy/claude-clr posture before #308): NE exported alone
        self.assertEqual(checks("export %s=1\n" % NE), ["ne_gb_pair", "ne_tt_reminder"])

    def test_pair_without_tt_is_billing_red(self):
        self.assertEqual(checks("export %s=1\nexport %s=1\n" % (NE, GB)), ["ne_tt_reminder"])

    def test_full_set_green(self):
        self.assertEqual(checks("export %s=1\nexport %s=1\nexport %s=off\n" % (NE, GB, TT)), [])

    def test_family_members(self):
        for name in ("DISABLE_TELEMETRY", "DISABLE_GROWTHBOOK", "DO_NOT_TRACK"):
            self.assertIn("ne_tt_reminder", checks('env = {"%s": "1"}\nx = "claude"\n' % name, ".py"), name)

    def test_generic_names_need_claude(self):
        self.assertEqual(checks("export DO_NOT_TRACK=1\nbrew install jq\n"), [])

    def test_off_values_do_not_count(self):
        self.assertEqual(checks("export %s=0\nunset DISABLE_TELEMETRY\n" % NE), [])

    def test_comments_and_docstrings_do_not_count(self):
        self.assertEqual(checks("# export %s=1\n" % NE), [])
        self.assertEqual(checks('"""%s=1 prose"""\n' % NE, ".py"), [])
        self.assertEqual(checks("/* %s: '1' */\n" % NE, ".js"), [])

    def test_env_block_window(self):
        far = "export %s=1\n" % NE + "true\n" * 60 + "export %s=1\nexport %s=off\n" % (GB, TT)
        self.assertEqual(checks(far), ["ne_gb_pair", "ne_tt_reminder"])

    def test_seed_step_satisfies_spawn(self):
        base = "export %s=1\nexport %s=1\nexport %s=off\nexec claude -p hi\n" % (NE, GB, TT)
        self.assertEqual(checks(base), ["ne_seed_missing"])
        self.assertEqual(checks('seed_gb_cache "$CLAUDE_CONFIG_DIR"\n' + base), [])

    def test_allowlist_suppresses_and_stale_warns(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "p.sh").write_text("export %s=1\n" % NE)
            Path(d, ".ne-pair-floor-allow").write_text(
                "p.sh * single-turn probe: caching cannot matter on turn 1\ngone.sh * reviewed and removed later\n")
            r = run_embedded(d)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("allowlist_stale", r.stdout)
            self.assertIn("2 allowlisted", r.stdout)


if __name__ == "__main__":
    unittest.main()
