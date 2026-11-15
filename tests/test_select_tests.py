"""select-tests: fixture repos for the import-graph selector (card t_4c317916).

Each case builds a tiny git repo, commits a base, applies a change, and asserts the exact test list
scripts/select_tests.py prints. Also pins the copy embedded in .github/workflows/select-tests.yml
byte-identical to scripts/select_tests.py (the caller's SHA pin is what pins the selector).
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "select_tests.py"
WF = ROOT / ".github" / "workflows" / "select-tests.yml"

BASE = {
    "pkg/__init__.py": "",
    "pkg/core.py": "VALUE = 1\n",
    "pkg/leaf.py": "from pkg.core import VALUE\n",
    "pkg/other.py": "X = 2\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/deep.py": "from ..core import VALUE\n",
    "pkg/cyc_a.py": "import pkg.cyc_b\n",
    "pkg/cyc_b.py": "from pkg import cyc_a\n",
    "pkg/data.json": "{}\n",
    "pkg/loader.py": "PATH = 'pkg/data.json'\n",
    "tests/conftest.py": "import pytest\n\n@pytest.fixture\ndef heavy():\n    from pkg import other\n    return other\n",
    "tests/test_leaf.py": "from pkg.leaf import VALUE\n",
    "tests/test_core.py": "import pkg.core\n",
    "tests/test_other_user.py": "def test_x(heavy):\n    assert heavy\n",
    "tests/test_deep.py": "from pkg.sub.deep import VALUE\n",
    "tests/test_cycle.py": "import pkg.cyc_a\n",
    "tests/test_loader.py": "from pkg.loader import PATH\n",
    "tests/test_plain.py": "def test_ok():\n    pass\n",
    "tests/api/conftest.py": "",
    "tests/api/test_api.py": "def test_api():\n    pass\n",
    "docs/readme.md": "hi\n",
    "uv.lock": '[[package]]\nname = "requests"\nversion = "2.0"\n\n[[package]]\nname = "PyYAML"\nversion = "6.0"\n',
    "pkg/net.py": "import requests\n",
    "tests/test_net.py": "from pkg import net\n",
}


def git(d, *args):
    subprocess.run(["git", "-C", d, *args], check=True, capture_output=True)


class Repo:
    def __init__(self, files=BASE):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        git(self.d, "init", "-q")
        git(self.d, "config", "user.email", "t@t")
        git(self.d, "config", "user.name", "t")
        self.write(files)
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "base")
        self.base = subprocess.run(["git", "-C", self.d, "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip()
        self.changed, self.deleted = [], []

    def write(self, files):
        for p, body in files.items():
            f = Path(self.d, p)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body)

    def change(self, path, body):
        self.write({path: body})
        self.changed.append(path)

    def delete(self, path):
        os.remove(os.path.join(self.d, path))
        self.deleted.append(path)

    def select(self, *extra):
        cf = Path(self.d, ".changed")
        df = Path(self.d, ".deleted")
        cf.write_text("".join(p + "\n" for p in self.changed))
        df.write_text("".join(p + "\n" for p in self.deleted))
        r = subprocess.run([sys.executable, str(SCRIPT), "--root", self.d, "--base", self.base,
                            "--changed-file", str(cf), "--deleted-file", str(df), *extra],
                           capture_output=True, text=True, check=True)
        self.summary = json.loads(r.stderr.strip().splitlines()[-1])
        return r.stdout.split()


class SelectTests(unittest.TestCase):
    def test_leaf_change_selects_only_its_importers(self):
        r = Repo()
        r.change("pkg/leaf.py", "from pkg.core import VALUE\nY = 3\n")
        self.assertEqual(r.select(), ["tests/test_leaf.py"])

    def test_shared_module_selects_transitive_importers(self):
        r = Repo()
        r.change("pkg/core.py", "VALUE = 2\n")
        # leaf (direct import of a module that imports core), core, deep (relative import `..core`)
        self.assertEqual(r.select(), ["tests/test_core.py", "tests/test_deep.py", "tests/test_leaf.py"])

    def test_package_init_change_selects_every_importer_of_the_package(self):
        r = Repo()
        r.change("pkg/__init__.py", "Z = 1\n")
        sel = r.select()
        self.assertIn("tests/test_leaf.py", sel)
        self.assertIn("tests/test_cycle.py", sel)
        self.assertNotIn("tests/test_plain.py", sel)
        self.assertNotIn("tests/api/test_api.py", sel)

    def test_changed_test_file_selects_itself(self):
        r = Repo()
        r.change("tests/test_plain.py", "def test_ok():\n    assert 1\n")
        self.assertEqual(r.select(), ["tests/test_plain.py"])

    def test_conftest_change_selects_the_tests_under_its_directory(self):
        r = Repo()
        r.change("tests/api/conftest.py", "X = 1\n")
        self.assertEqual(r.select(), ["tests/api/test_api.py"])
        r2 = Repo()
        r2.change("tests/conftest.py", BASE["tests/conftest.py"] + "\n# touched\n")
        sel = r2.select()
        self.assertEqual(len(sel), r2.summary["total"])
        self.assertIn("tests/api/test_api.py", sel)

    def test_module_imported_inside_a_fixture_selects_only_that_fixtures_users(self):
        r = Repo()
        r.change("pkg/other.py", "X = 3\n")
        self.assertEqual(r.select(), ["tests/test_other_user.py"])

    def test_autouse_fixture_import_reaches_every_test_under_it(self):
        files = dict(BASE)
        files["tests/conftest.py"] = ("import pytest\n\n@pytest.fixture(autouse=True)\ndef heavy():\n"
                                      "    from pkg import other\n    return other\n")
        r = Repo(files)
        r.change("pkg/other.py", "X = 3\n")
        sel = r.select()
        self.assertEqual(len(sel), r.summary["total"])

    def test_deleted_file_selects_the_tests_that_imported_it_at_base(self):
        r = Repo()
        r.delete("pkg/leaf.py")
        self.assertEqual(r.select(), ["tests/test_leaf.py"])
        self.assertEqual(r.summary["by_reason"]["deleted"], 1)

    def test_deleted_file_under_a_subdirectory_root_reads_base_from_that_root(self):
        r = Repo({"sub/" + p: b for p, b in BASE.items()})
        sub = os.path.join(r.d, "sub")
        os.remove(os.path.join(sub, "pkg/leaf.py"))
        Path(sub, "pkg/other.py").write_text("X = 3\n")
        cf, df = Path(r.d, ".changed"), Path(r.d, ".deleted")
        cf.write_text("pkg/other.py\n")
        df.write_text("pkg/leaf.py\n")
        out = subprocess.run([sys.executable, str(SCRIPT), "--root", sub, "--base", r.base,
                              "--changed-file", str(cf), "--deleted-file", str(df)],
                             capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, ["tests/test_leaf.py", "tests/test_other_user.py"])

    def test_deleted_asset_selects_the_tests_of_modules_that_named_it_at_base(self):
        r = Repo()
        r.delete("pkg/data.json")
        r.change("pkg/other.py", "X = 3\n")
        self.assertEqual(r.select(), ["tests/test_loader.py", "tests/test_other_user.py"])
        self.assertEqual(r.summary["unreached"], [])

    def test_deleted_test_file_is_not_selected(self):
        r = Repo()
        r.delete("tests/test_leaf.py")
        r.change("pkg/other.py", "X = 4\n")
        self.assertEqual(r.select(), ["tests/test_other_user.py"])

    def test_import_cycle_terminates_and_selects_both_sides(self):
        r = Repo()
        r.change("pkg/cyc_b.py", "from pkg import cyc_a\nQ = 1\n")
        self.assertEqual(r.select(), ["tests/test_cycle.py"])

    def test_non_python_file_selects_the_modules_that_name_it(self):
        r = Repo()
        r.change("pkg/data.json", "{\"a\": 1}\n")
        self.assertEqual(r.select(), ["tests/test_loader.py"])

    def test_lockfile_change_selects_importers_of_the_bumped_distribution(self):
        r = Repo()
        r.change("uv.lock", BASE["uv.lock"].replace('version = "2.0"', 'version = "2.1"'))
        self.assertEqual(r.select(), ["tests/test_net.py"])

    def test_never_zero_falls_back_to_the_changed_files_own_package(self):
        r = Repo()
        r.change("tests/api/fixtures.txt", "x\n")  # nothing names it, no import reaches it
        self.assertEqual(r.select(), ["tests/api/test_api.py"])
        self.assertTrue(r.summary["fallback_own_package"])

    def test_alias_package_resolves_to_the_real_module(self):
        files = dict(BASE)
        files["legacy/__init__.py"] = ""
        files["tests/test_alias.py"] = "import legacy.core\n"
        r = Repo(files)
        r.change("pkg/core.py", "VALUE = 9\n")
        self.assertNotIn("tests/test_alias.py", r.select())
        r2 = Repo(files)
        r2.change("pkg/core.py", "VALUE = 9\n")
        self.assertIn("tests/test_alias.py", r2.select("--alias", "legacy=pkg"))

    def test_lazy_import_in_a_module_is_skipped_unless_followed(self):
        files = dict(BASE)
        files["pkg/lazy.py"] = "def f():\n    from pkg import other\n    return other\n"
        files["tests/test_lazy.py"] = "import pkg.lazy\n"
        r = Repo(files)
        r.change("pkg/other.py", "X = 5\n")
        self.assertNotIn("tests/test_lazy.py", r.select())
        r2 = Repo(files)
        r2.change("pkg/other.py", "X = 5\n")
        self.assertIn("tests/test_lazy.py", r2.select("--lazy-imports", "follow"))

    def test_type_checking_import_is_not_an_edge_but_class_body_import_is(self):
        files = dict(BASE)
        files["pkg/typed.py"] = ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import other\n"
                                 "class C:\n    from pkg import leaf\n")
        files["tests/test_typed.py"] = "import pkg.typed\n"
        r = Repo(files)
        r.change("pkg/other.py", "X = 5\n")
        self.assertNotIn("tests/test_typed.py", r.select())
        r2 = Repo(files)
        r2.change("pkg/leaf.py", "Y = 1\n")
        self.assertIn("tests/test_typed.py", r2.select())

    def test_repo_scanner_is_always_selected_and_stands_in_for_an_unnamed_root_file(self):
        files = dict(BASE)
        files["tests/test_ratchet.py"] = "from pathlib import Path\nFILES = list(Path('.').rglob('*.py'))\n"
        r = Repo(files)
        r.change("pkg/leaf.py", "from pkg.core import VALUE\nY = 3\n")
        self.assertEqual(r.select(), ["tests/test_leaf.py", "tests/test_ratchet.py"])
        r2 = Repo(files)
        r2.change("NOTES.txt", "x\n")  # root-level, nothing names it: the scanner, not the whole suite
        self.assertEqual(r2.select(), ["tests/test_ratchet.py"])

    def test_root_file_with_no_scanner_runs_the_root_package(self):
        r = Repo()
        r.change("NOTES.txt", "x\n")
        sel = r.select()
        self.assertEqual(len(sel), r.summary["total"])
        self.assertTrue(r.summary["fallback_own_package"])

    def test_all_prints_the_full_suite(self):
        r = Repo()
        out = subprocess.run([sys.executable, str(SCRIPT), "--root", r.d, "--all"], capture_output=True,
                             text=True, check=True).stdout.split()
        self.assertEqual(len(out), 9)
        self.assertNotIn("tests/conftest.py", out)


class WorkflowStep(unittest.TestCase):
    """Runs the exact `Select tests` step script with bash against a fixture merge commit."""

    def run_step(self, event="pull_request", selection="", max_legs="6", per_leg="2", change=True):
        r = Repo()
        git(r.d, "checkout", "-qb", "pr")
        if change:
            r.change("pkg/core.py", "VALUE = 2\n")
            git(r.d, "commit", "-qam", "pr")
        git(r.d, "checkout", "-q", "-")
        git(r.d, "merge", "-q", "--no-ff", "-m", "merge", "pr")
        wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
        step = [s for s in wf["jobs"]["select"]["steps"] if s.get("name") == "Select tests"][0]
        with tempfile.TemporaryDirectory() as t:
            out, summ = Path(t, "out"), Path(t, "summary")
            env = dict(os.environ, EVENT=event, SELECTION=selection, WD=".", TESTS_PATH="tests", SOURCE_ROOTS="",
                       ALIASES="", LAZY="skip", MAX_LEGS=max_legs, PER_LEG=per_leg, PR="1", REPO="o/r", GH_TOKEN="",
                       RUNNER_TEMP=t, GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(summ))
            p = subprocess.run(["bash", "-c", step["run"]], cwd=r.d, env=env, capture_output=True, text=True)
            outputs = dict(l.split("=", 1) for l in out.read_text().splitlines()) if out.exists() else {}
            return p, outputs

    def test_pull_request_selects_from_the_merge_parent_diff(self):
        p, o = self.run_step()
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(o["mode"], "selected")
        self.assertEqual(json.loads(o["tests"]), ["tests/test_core.py", "tests/test_deep.py", "tests/test_leaf.py"])
        self.assertEqual(json.loads(o["legs"]), [1, 2])
        self.assertTrue(o["reason"].startswith("selection: 3 of 9 test files"))
        self.assertIn("tests/test_leaf.py", p.stdout)

    def test_switch_off_runs_the_full_suite(self):
        p, o = self.run_step(selection="off")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(o["mode"], "full")
        self.assertEqual(o["count"], "9")
        self.assertEqual(json.loads(o["legs"]), [1, 2, 3, 4, 5, 6])
        self.assertIn("CI_PR_SELECTION=off", o["reason"])

    def test_change_outside_the_working_directory_still_runs_tests(self):
        r = Repo()
        Path(r.d, "other").mkdir()
        git(r.d, "checkout", "-qb", "pr")
        Path(r.d, "other", "x.txt").write_text("x\n")
        git(r.d, "add", "-A")
        git(r.d, "commit", "-qm", "pr")
        git(r.d, "checkout", "-q", "-")
        git(r.d, "merge", "-q", "--no-ff", "-m", "merge", "pr")
        wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
        step = [s for s in wf["jobs"]["select"]["steps"] if s.get("name") == "Select tests"][0]
        sub = Path(r.d, "sub")
        sub.mkdir()
        for p in ("pkg", "tests"):
            subprocess.run(["cp", "-r", str(Path(r.d, p)), str(sub)], check=True)
        with tempfile.TemporaryDirectory() as t:
            out = Path(t, "out")
            env = dict(os.environ, EVENT="pull_request", SELECTION="", WD="sub", TESTS_PATH="tests", SOURCE_ROOTS="",
                       ALIASES="", LAZY="skip", MAX_LEGS="1", PER_LEG="0", PR="1", REPO="o/r", GH_TOKEN="",
                       RUNNER_TEMP=t, GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(Path(t, "s")))
            p = subprocess.run(["bash", "-c", step["run"]], cwd=r.d, env=env, capture_output=True, text=True)
            o = dict(l.split("=", 1) for l in out.read_text().splitlines())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotEqual(o["count"], "0")

    def test_push_runs_the_full_suite(self):
        p, o = self.run_step(event="push")
        self.assertEqual((o["mode"], o["count"]), ("full", "9"))

    def test_unreadable_diff_fails_instead_of_running_everything(self):
        r = Repo()  # single commit: no HEAD^1; REST unreachable (no token, fake repo)
        wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
        step = [s for s in wf["jobs"]["select"]["steps"] if s.get("name") == "Select tests"][0]
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, EVENT="pull_request", SELECTION="", WD=".", TESTS_PATH="tests",
                       SOURCE_ROOTS="", ALIASES="", LAZY="skip", MAX_LEGS="1", PER_LEG="0", PR="1", REPO="o/nonexistent-zz",
                       GH_TOKEN="x", RUNNER_TEMP=t, GITHUB_OUTPUT=str(Path(t, "o")),
                       GITHUB_STEP_SUMMARY=str(Path(t, "s")), PATH="/usr/bin:/bin")
            p = subprocess.run(["bash", "-c", step["run"]], cwd=r.d, env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not running everything", p.stdout + p.stderr)


class EmbeddedCopy(unittest.TestCase):
    def test_workflow_embeds_the_selector_byte_for_byte(self):
        wf = yaml.load(WF.read_text(), Loader=yaml.BaseLoader)
        steps = [s for s in wf["jobs"]["select"]["steps"] if s.get("name") == "Select tests"]
        self.assertEqual(len(steps), 1)
        run = steps[0]["run"]
        start = run.index("<<'PY'\n") + len("<<'PY'\n")
        end = run.index("\nPY\n", start)
        self.assertEqual(run[start:end] + "\n", SCRIPT.read_text())

    def test_selector_never_contains_an_expression_opener(self):
        self.assertNotIn("${" + "{", SCRIPT.read_text())  # GitHub would expand it inside run:


if __name__ == "__main__":
    unittest.main()
