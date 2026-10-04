"""Floor caller templates must not path-filter pull_request (t_5df3de69, Prism 7782a5f9dbf6).

GitHub matches a path filter against at most the first 3,000 files of a diff; past that, a workflow whose
matching files sort late does not run at all, so a big merge skips the floor with no check. Rule (README
"Caller path filters"): no filter on `pull_request` for any floor caller; `push` may carry `paths-ignore`
only, and ne-pair-floor (security lint) takes no filter on any event. Every template passes fleet R14.
"""
from pathlib import Path
import unittest

import yaml

from test_workflow_path_filter_lint import run_lint

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((ROOT / "templates").glob("*.yml"))
NO_FILTER_AT_ALL = {"ne-pair-floor.yml", "override-lint.yml"}


def triggers(path):
    on = yaml.load(path.read_text(), Loader=yaml.BaseLoader)["on"]
    if isinstance(on, str):
        on = {on: {}}
    elif isinstance(on, list):
        on = {ev: {} for ev in on}
    return {ev: (spec if isinstance(spec, dict) else {}) for ev, spec in on.items()}


class CallerTemplatesPaths(unittest.TestCase):
    def test_templates_found(self):
        names = {p.name for p in TEMPLATES}
        self.assertTrue(NO_FILTER_AT_ALL <= names, names)
        self.assertIn("test-ci.yml", names)

    def test_pull_request_never_filtered(self):
        for p in TEMPLATES:
            spec = triggers(p).get("pull_request")
            if spec is None:
                continue
            self.assertFalse({"paths", "paths-ignore"} & set(spec),
                             "%s: a PR path filter skips >3,000-file diffs whose matches sort late" % p.name)

    def test_push_filter_is_paths_ignore_only(self):
        for p in TEMPLATES:
            spec = triggers(p).get("push", {})
            self.assertNotIn("paths", spec, "%s: push `paths:` skips a big merge whose matches sort late" % p.name)
            if p.name in NO_FILTER_AT_ALL:
                self.assertNotIn("paths-ignore", spec, p.name)

    def test_every_template_passes_fleet_r14(self):
        for p in TEMPLATES:
            rc, out = run_lint({p.name: (p.read_text(), 0)})
            self.assertEqual(rc, 0, "%s\n%s" % (p.name, out))


if __name__ == "__main__":
    unittest.main()
