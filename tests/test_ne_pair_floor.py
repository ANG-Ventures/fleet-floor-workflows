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

    # --- Prism t_b83e184e (PR #18 @6050df1c): each test below is RED on that head. ---
    FULL = "export %s=1\nexport %s=1\nexport %s=off\n" % (NE, GB, TT)

    def test_seed_name_in_comment_is_not_a_seed(self):
        for src, ext in ((self.FULL + "# TODO: seed_gb_cache\nexec claude -p hi\n", ".sh"),
                         (self.FULL + "exec claude -p hi  # seed_gb_cache later\n", ".sh"),
                         ('import subprocess\nENV = {"%s": "1", "%s": "1", "%s": "off"}\n'
                          '# cachedGrowthBookFeatures\nsubprocess.run(["claude", "-p", "x"], env=ENV)\n' % (NE, GB, TT),
                          ".py"),
                         ('const e = {%s: "1", %s: "1", %s: "off"}; // seed_gb_cache\nspawn("claude", [], {env: e});\n'
                          % (NE, GB, TT), ".js"),
                         ('/* cfp_gb_seed_gate */\nconst e = {%s: "1", %s: "1", %s: "off"};\nspawn("claude", []);\n'
                          % (NE, GB, TT), ".js")):
            self.assertEqual(checks(src, ext), ["ne_seed_missing"], src)
        # the seed as code still satisfies it
        self.assertEqual(checks('seed_gb_cache "$D"  # seed\n' + self.FULL + "exec claude -p hi\n"), [])

    def test_trailing_comment_is_not_an_env(self):
        self.assertEqual(checks("true  # export %s=1\n" % NE), [])
        self.assertEqual(checks("x = 1  # %s=1\n" % NE, ".py"), [])
        self.assertEqual(checks('echo "a # b"\nexport %s=1\n' % NE), ["ne_gb_pair", "ne_tt_reminder"])

    def test_dotenv_files_are_scanned(self):
        for name in (".env", ".env.local"):
            with tempfile.TemporaryDirectory() as d:
                Path(d, name).write_text("%s=1\n" % NE)
                r = run_embedded(d)
                self.assertEqual(r.returncode, 1, name + r.stdout)
                self.assertIn("%s:1 ne_tt_reminder" % name, r.stdout)
                self.assertIn("1 files scanned", r.stdout)

    def test_python_string_payload_is_code_docstring_is_not(self):
        q3 = '"' * 3
        src = "import subprocess\nsubprocess.run(%sexport %s=1\nexec claude -p hi%s, shell=True)\n" % (q3, NE, q3)
        self.assertEqual(checks(src, ".py"), ["ne_gb_pair", "ne_seed_missing", "ne_tt_reminder"])
        src = "CMD = %s\nexport %s=1\nclaude --continue\n%s\n" % ("'" * 3, NE, "'" * 3)
        self.assertEqual(checks(src, ".py"), ["ne_gb_pair", "ne_seed_missing", "ne_tt_reminder"])
        doc = "def f():\n    %s%s=1 prose%s\n    return 1\n" % (q3, NE, q3)
        self.assertEqual(checks(doc, ".py"), [])

    def test_any_claude_launch_is_a_spawn(self):
        for launch, ext in (('exec claude "$@"', ".sh"), ("claude", ".sh"), ("claude --continue", ".sh"),
                            ('"$HOME/.local/bin/claude" --continue', ".sh"), ("FOO=1 claude", ".sh"),
                            ("out=$(claude --version)", ".sh"), ("true && claude", ".sh"),
                            ("nohup claude --continue &", ".sh"), ("timeout 30 claude", ".sh"),
                            ("ExecStart=/usr/local/bin/claude --continue", ".service"),
                            ("  run: claude --continue", ".yml"),
                            ('subprocess.run(["claude", "--continue"])', ".py"),
                            ('subprocess.run("claude --continue", shell=True)', ".py"),
                            ('os.system("claude")', ".py"),
                            ('execSync("claude --continue")', ".js"),
                            ("<key>Program</key><string>/usr/local/bin/claude</string>", ".plist")):
            env = self.FULL
            if ext == ".plist":
                env = "".join("<key>%s</key><string>%s</string>\n" % kv for kv in ((NE, "1"), (GB, "1"), (TT, "off")))
            elif ext in (".py", ".js"):
                env = 'E = {"%s": "1", "%s": "1", "%s": "off"}\n' % (NE, GB, TT)
            elif ext in (".service", ".yml"):
                env = "Environment=%s=1\nEnvironment=%s=1\nEnvironment=%s=off\n" % (NE, GB, TT)
            self.assertEqual(checks(env + launch + "\n", ext), ["ne_seed_missing"], launch)

    def test_mentions_of_claude_are_not_spawns(self):
        for line, ext in (("echo claude", ".sh"), ("brew install claude", ".sh"), ("claude = Client()", ".py"),
                          ('x = {"claude": 1}', ".py"), ("claude.messages.create()", ".js"),
                          ("<string>/usr/local/bin/claude-lane</string>", ".plist")):
            self.assertEqual(checks(self.FULL + line + "\n", ext), [], line)

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
