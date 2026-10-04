"""override-lint caller must not path-filter (Prism 7782a5f9dbf6, t_0e51de69).

GitHub matches `paths:` against at most the first 3,000 files of a diff; past that, a workflow whose
matching files sort late does not run at all. The lint skips non-code/test files itself, so the caller
fires on every PR event and declares that with `# path-filter-exempt:` for fleet R14 (sast.yml).
"""
from pathlib import Path
import unittest

import yaml

from test_workflow_path_filter_lint import run_lint

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/override-lint.yml"


class OverrideLintCaller(unittest.TestCase):
    def test_no_path_filter_on_any_event(self):
        on = yaml.load(TEMPLATE.read_text(), Loader=yaml.BaseLoader)["on"]
        for ev, spec in on.items():
            spec = spec if isinstance(spec, dict) else {}
            self.assertFalse({"paths", "paths-ignore"} & set(spec),
                             "%s: a path filter skips >3,000-file PRs whose code files sort late" % ev)

    def test_pull_request_types_include_edited(self):
        types = yaml.load(TEMPLATE.read_text(), Loader=yaml.BaseLoader)["on"]["pull_request"]["types"]
        self.assertIn("edited", types)

    def test_passes_fleet_r14_as_declared_exempt(self):
        rc, out = run_lint({"override-lint.yml": (TEMPLATE.read_text(), 0)})
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
