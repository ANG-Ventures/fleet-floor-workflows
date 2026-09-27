"""override-lint: a PR that adds a refusal must name its override (or say none by design) in its body.

Runs the EXACT `Override lint` step script from override-lint.yml (extracted from the YAML, executed
with bash) against synthetic diff + body fixtures. Card t_8f526b34.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/override-lint.yml"
STEP = "Override lint"


def load():
    return yaml.load(WF.read_text(), Loader=yaml.BaseLoader)


def step():
    steps = [s for s in load()["jobs"]["override_lint"]["steps"] if s.get("name") == STEP]
    assert len(steps) == 1, "override-lint.yml must carry exactly one %r step" % STEP
    return steps[0]


def diff_of(files):
    """files: {path: [added lines]} -> a unified diff adding them to new files."""
    out = []
    for path, lines in files.items():
        out += ["diff --git a/%s b/%s" % (path, path), "new file mode 100644", "--- /dev/null",
                "+++ b/%s" % path, "@@ -0,0 +1,%d @@" % len(lines)] + ["+" + l for l in lines]
    return "\n".join(out) + "\n"


def run(files, body, allowlist="", docs=None):
    inputs = load()["on"]["workflow_call"]["inputs"]
    with tempfile.TemporaryDirectory() as d:
        dp, bp = Path(d) / "pr.diff", Path(d) / "pr.body"
        dp.write_text(diff_of(files))
        bp.write_text(body)
        env = dict(os.environ, OVERRIDE_LINT_SCOPE=inputs["scope"]["default"],
                   OVERRIDE_LINT_ALLOWLIST=allowlist, OVERRIDE_LINT_DOCS=docs if docs is not None else d)
        script = step()["run"].replace("/tmp/override-lint.diff", str(dp)).replace("/tmp/override-lint.body", str(bp))
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


# The #1116 shape: a blanket refusal string in a scoped path, raised from a helper.
REFUSAL = {"hermes_cli/model_policy.py": [
    "def pin_error(name):",
    "    # comment: workers never pin (comments are not hits)",
    "    return f\"provider '{name}' pins one Claude subscription; workers never pin \"",
    "def check(name):",
    "    raise ValueError(pin_error(name))",
]}


class OverrideLint(unittest.TestCase):
    def assertRc(self, want, files, body, **kw):
        rc, out = run(files, body, **kw)
        self.assertEqual(rc, want, out)
        return out

    # --- rule 2/3: FAIL without an Override line (born-red, #1116 shape) ---
    def test_refusal_without_override_line_fails_with_hits_and_template(self):
        out = self.assertRc(1, REFUSAL, "Refuses single-sub pins. Tests: 25 pass.")
        self.assertIn("model_policy.py:3", out)
        self.assertIn("Override: none by design", out)  # template printed
        self.assertNotIn("model_policy.py:2 ", out)      # the comment line is not a hit

    def test_override_none_by_design_passes(self):
        self.assertRc(0, REFUSAL, "x\n\nOverride: none by design — pool routing is the only worker lane\n")

    def test_override_none_by_design_ascii_dash_and_bold_passes(self):
        self.assertRc(0, REFUSAL, "**Override:** none by design - pool routing only\n")

    def test_bare_none_fails(self):
        out = self.assertRc(1, REFUSAL, "Override: none\n")
        self.assertIn("names nothing", out)

    def test_override_naming_nothing_fails(self):
        self.assertRc(1, REFUSAL, "Override: see the card\n")

    def test_override_naming_a_flag_passes(self):
        self.assertRc(0, REFUSAL, "Override: `--allow-pin \"<reason>\"` (audit comment)\n")

    def test_override_naming_an_env_passes(self):
        self.assertRc(0, REFUSAL, "- Override: KANBAN_PIN_GUARD=0 (global kill switch)\n")

    # --- prose fallback: #823 (names the flag the diff adds) and #877 (states no override) ---
    def test_prose_flag_added_by_diff_passes(self):
        files = dict(REFUSAL, **{"hermes_cli/kanban.py": ["    p.add_argument('--allow-flagship', default='')"]})
        out = self.assertRc(0, files, "Explicit `--allow-flagship` reasons are persisted as audit comments.")
        self.assertIn("--allow-flagship", out)

    def test_prose_flag_not_in_diff_fails(self):
        self.assertRc(1, REFUSAL, "Use --allow-flagship to bypass.")

    def test_prose_non_override_flag_does_not_count(self):
        files = dict(REFUSAL, **{"hermes_cli/kanban.py": ["    p.add_argument('--model')"]})
        self.assertRc(1, files, "`hermes kanban create --model x` succeeds on main.")

    def test_prose_states_no_override_passes(self):
        self.assertRc(0, REFUSAL, "There is no off-switch file and no env override.")

    # --- rule 1: what is a hit ---
    def test_tests_comments_and_docs_are_not_hits(self):
        files = {"tests/hermes_cli/test_x.py": ["    raise ValueError('refused')"],
                 "hermes_cli/test_y.py": ["X = 'never'"],
                 "hooks/README.md": ["Workers never pin."],
                 "hooks/g.py": ["# refused: never", "    // denied"]}
        self.assertIn("0 refusal hit(s)", self.assertRc(0, files, ""))

    def test_out_of_scope_string_is_not_a_hit_but_anchor_is(self):
        self.assertRc(0, {"lib/a.py": ["MSG = 'never do this'"]}, "")
        self.assertRc(1, {"lib/a.py": ["    raise PermissionError(", "        'refusing: orchestrator-only')"]}, "")
        self.assertRc(1, {"svc/api.py": ["    raise HTTPException(409, 'review send-back is not allowed')"]}, "")
        self.assertRc(1, {"bin/tool.py": ["    sys.exit('denied: workers never merge')"]}, "")
        self.assertRc(1, {"lib/h.py": ["    emit_block('forbidden verb')"]}, "")

    def test_scope_dirs_match_at_depth_and_listed_files(self):
        self.assertRc(1, {"profiles/x/hooks/p.py": ["M = 'BLOCKED: never'"]}, "")
        self.assertRc(1, {"scripts/fleet-merge.sh": ["echo \"refusing merge on red CI\" >&2"]}, "")
        self.assertRc(0, {"scripts/other.sh": ["echo \"refusing merge on red CI\" >&2"]}, "")

    def test_default_scope_is_the_card_list(self):
        scope = load()["on"]["workflow_call"]["inputs"]["scope"]["default"].split()
        self.assertEqual(scope, ["hermes_cli/", "tools/", "gateway/", "cron/", "hooks/",
                                 "scripts/fleet-merge.sh", "scripts/gh-shim.py"])

    # --- allowlist: register class A seed + caller entries ---
    def test_seed_allowlist_exempts_class_a(self):
        for path in ("profiles/momus/hooks/momus_policy.py", "hooks/live_tree_branch_policy.py",
                     "hooks/live_tree_bare_init_policy.py", "hooks/guard_self_disable_policy.py"):
            self.assertRc(0, {path: ["M = 'BLOCKED: never'"]}, "")
        self.assertRc(0, {"hermes_cli/kanban_db.py": [
            "        f\"kanban live-system guard: refusing to {action}\""]}, "")

    def test_caller_allowlist_entry(self):
        files = {"hooks/p.py": ["M = 'denied'"]}
        self.assertRc(1, files, "")
        self.assertRc(0, files, "", allowlist="path:^hooks/p\\.py$ reviewed: none by design")
        self.assertRc(0, files, "", allowlist="line:M = 'denied' reviewed")

    # --- rule 4: documented-override check is WARNING only ---
    def test_undocumented_override_warns_but_passes(self):
        with tempfile.TemporaryDirectory() as docs:
            Path(docs, "SKILL.md").write_text("nothing here\n")
            out = self.assertRc(0, REFUSAL, "Override: `--allow-pin`", docs=docs)
            self.assertIn("::warning::", out)
            Path(docs, "SKILL.md").write_text("use --allow-pin \"<reason>\"\n")
            out = self.assertRc(0, REFUSAL, "Override: `--allow-pin`", docs=docs)
            self.assertIn("documented: --allow-pin", out)

    def test_step_is_pr_only(self):
        self.assertIn("pull_request", step()["if"])


if __name__ == "__main__":
    unittest.main()
