#!/usr/bin/env python3
"""select-tests: pick the pytest files a pull request can reach through Python's static import graph.

Embedded byte-for-byte in .github/workflows/select-tests.yml (tests/test_select_tests.py enforces it), so a
caller's SHA pin on that workflow pins this selector too: a PR in the calling repo cannot change it.

Rules (card t_4c317916, spec 2026-11-14 design C):
  * a changed test file selects itself;
  * a changed .py file selects every test file that imports it, directly or transitively (cycles are fine);
    importing `a.b.c` also imports `a/__init__.py` and `a/b/__init__.py`, so those are edges too;
  * every test file depends on each conftest.py in its directory and the directories above it, so a changed
    conftest.py (or a module a conftest imports) selects the tests under that conftest's directory;
  * a deleted file selects the tests that imported it at the BASE commit (the ones still present at head);
  * a changed lockfile / requirements / pyproject selects the files that import a distribution whose pinned
    version or requirement line changed (third-party imports are matched by normalised top-level name);
  * a changed non-Python file selects the .py files that name its path (string literal / text mention);
  * a non-test module's function-level (lazy) imports are not edges unless --lazy-imports follow;
  * a test that reads the whole checkout (rglob / os.walk / `**` glob / git ls-files) is always selected:
    no import edge points at it and any diff can turn it red;
  * never zero: when the repo has tests and the graph selects none, the tests in the changed files' own
    packages run (nearest enclosing directory below the repo root that has test files under it); with none,
    the scanners above stand; a repo with neither runs its root package (the suite).

Output: the selected test files, one per line (--out, default stdout). A one-line JSON summary goes to
--summary-out (default stderr). Python 3.8+, stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import warnings
from collections import defaultdict, deque

LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".nox", "build", "dist",
             ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages"}
# distribution name -> import name, for the common cases where they differ
DIST_ALIASES = {
    "pyyaml": "yaml", "beautifulsoup4": "bs4", "pillow": "PIL", "python-dateutil": "dateutil",
    "scikit-learn": "sklearn", "opencv-python": "cv2", "opencv-python-headless": "cv2", "protobuf": "google",
    "attrs": "attr", "python-dotenv": "dotenv", "pyjwt": "jwt", "msgpack-python": "msgpack",
    "google-cloud-storage": "google", "typing-extensions": "typing_extensions", "pygithub": "github",
    "python-multipart": "multipart", "websocket-client": "websocket", "pyserial": "serial",
    "pycryptodome": "Crypto", "tomli-w": "tomli_w", "ruamel.yaml": "ruamel", "psycopg2-binary": "psycopg2",
}


def is_test_file(path: str) -> bool:
    b = os.path.basename(path)
    return b.endswith(".py") and (b.startswith("test_") or b.endswith("_test.py"))


def norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def import_names_for_dist(name: str) -> set:
    n = norm_dist(name)
    out = {n.replace("-", "_")}
    if n in DIST_ALIASES:
        out.add(DIST_ALIASES[n])
    return {x.lower() for x in out}


class Tree:
    """A file tree: the working checkout, or a git commit read with `git show`."""

    def __init__(self, root: str, rev: str | None = None):
        self.root, self.rev = root, rev

    def files(self) -> list:
        if self.rev is None:
            out = []
            for d, dirs, names in os.walk(self.root):
                dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info"))
                rel = os.path.relpath(d, self.root)
                for n in names:
                    out.append(n if rel == "." else os.path.join(rel, n).replace(os.sep, "/"))
            return sorted(out)
        r = subprocess.run(["git", "-C", self.root, "ls-tree", "-r", "--name-only", self.rev],
                           capture_output=True, text=True, check=True)
        return sorted(p for p in r.stdout.splitlines()
                      if not (set(p.split("/")[:-1]) & SKIP_DIRS))

    def read(self, path: str) -> str | None:
        cache = self.__dict__.setdefault("_cache", {})
        if path not in cache:
            cache[path] = self._read(path)
        return cache[path]

    def _read(self, path: str) -> str | None:
        try:
            if self.rev is None:
                with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as fh:
                    return fh.read()
            r = subprocess.run(["git", "-C", self.root, "show", "%s:./%s" % (self.rev, path)],
                               capture_output=True, check=True)
            return r.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.CalledProcessError):
            return None


def _fixture(stmt):
    """None for a non-fixture statement; else True/False = autouse. Matches @pytest.fixture / @fixture(...)."""
    if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for dec in stmt.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        f = call.func if call else dec
        name = getattr(f, "attr", getattr(f, "id", ""))
        if name in ("fixture", "yield_fixture"):
            return any(k.arg == "autouse" and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in (call.keywords if call else []))
    return None


def _is_type_checking(test) -> bool:
    return getattr(test, "id", getattr(test, "attr", "")) == "TYPE_CHECKING"


def _module_scope(stmts):
    """Statements that run at import time: not function bodies, not `if TYPE_CHECKING:`; class bodies do run."""
    for st in stmts:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(st, ast.If) and _is_type_checking(st.test):
            yield from _module_scope(st.orelse)
        elif isinstance(st, ast.ClassDef):
            yield from _module_scope(st.body)
        elif isinstance(st, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)) \
                or type(st).__name__ in ("TryStar", "Match"):
            for field in ("body", "orelse", "finalbody"):
                yield from _module_scope(getattr(st, field, []) or [])
            for h in getattr(st, "handlers", []) or []:
                yield from _module_scope(h.body)
            for c in getattr(st, "cases", []) or []:
                yield from _module_scope(c.body)
        else:
            yield st


_EXTRACT_CACHE = {}


def _import_records(nodes) -> list:
    """(module, level, names) per import; names None for `import a.b` and import_module("a.b")."""
    out = []
    for node in nodes:
        if isinstance(node, ast.Import):
            out += [(a.name, 0, None) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level or 0, [a.name for a in node.names if a.name != "*"]))
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in (
                "import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            out.append((node.args[0].value, 0, None))
    return out


def _extract(p: str, text: str, lazy_imports: str):
    """[(node id, fixture meta or None, import records)] for one file, or None when it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text, filename=p)
    except (SyntaxError, ValueError):
        return None
    if os.path.basename(p) == "conftest.py":
        recs = []
        for stmt in tree.body:
            fx = _fixture(stmt)
            if fx is None:
                recs.append((p, None, _import_records(ast.walk(stmt))))
            else:  # a fixture: its body's imports reach only the tests that use it
                recs.append(("%s::%s" % (p, stmt.name), (fx, [a.arg for a in stmt.args.args]),
                             _import_records(ast.walk(stmt))))
        return recs
    if lazy_imports == "follow" or is_test_file(p):
        return [(p, None, _import_records(ast.walk(tree)))]
    # a non-test module: the imports that run when it is imported
    return [(p, None, _import_records(n for st in _module_scope(tree.body) for n in ast.walk(st)))]


SCANNER_RE = re.compile(r"\.rglob\(|os\.walk\(|ls-files|glob\([^)\n]*\*\*")


class Graph:
    def __init__(self, tree: Tree, tests_paths: list, source_roots: list, aliases=None, lazy_imports="skip"):
        self.tree = tree
        # "skip": a non-test module's function-level (lazy) imports are not edges. Following them makes most
        # modules depend on most others (hermes-agent measured fan-in 1502/1935, scripts/ci/test_impact.py);
        # the trunk run, which is never narrowed, covers them. "follow": every import is an edge.
        self.lazy_imports = lazy_imports
        self.aliases = dict(aliases or {})  # import prefix -> real prefix (a runtime alias package)
        self.all_files = tree.files()
        self.py = [p for p in self.all_files if p.endswith(".py")]
        self.pyset = set(self.py)
        under = lambda p, d: d in ("", ".") or p == d or p.startswith(d.rstrip("/") + "/")
        self.tests = sorted(p for p in self.py if is_test_file(p)
                            and any(under(p, t) for t in tests_paths))
        self.conftests = [p for p in self.py if os.path.basename(p) == "conftest.py"]
        roots = {""} | {r.strip("/") for r in source_roots if r.strip("/")}
        if any(p.startswith("src/") for p in self.py):
            roots.add("src")
        # pytest's default (prepend) import mode puts each test file's first non-package ancestor on sys.path
        for t in self.tests + self.conftests:
            d = os.path.dirname(t)
            while d and (d + "/__init__.py") in self.pyset:
                d = os.path.dirname(d)
            roots.add(d)
        self.modules = defaultdict(set)  # dotted name -> files
        for p in self.py:
            for r in roots:
                if r and not p.startswith(r + "/"):
                    continue
                rel = p[len(r) + 1:] if r else p
                parts = rel[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if parts and all(x.isidentifier() for x in parts):
                    self.modules[".".join(parts)].add(p)
        self.edges = defaultdict(set)  # file -> files it depends on
        self.third_party = defaultdict(set)  # file -> top-level names it imports that are not in-repo
        self.parse_errors = []
        self.fixtures = defaultdict(dict)  # conftest -> {fixture name: (autouse, arg names)}
        for p in self.py:
            self._scan(p)
        for c in self.conftests:  # a fixture that takes another fixture depends on it
            chain = self._conftest_chain(c)
            for name, (_, args) in self.fixtures[c].items():
                for arg in args:
                    for c2 in chain:
                        if arg in self.fixtures[c2] and (c2, arg) != (c, name):
                            self.edges["%s::%s" % (c, name)].add("%s::%s" % (c2, arg))
        for t in self.tests:
            text = None
            for c in self._conftest_chain(t):
                if c == t:
                    continue
                self.edges[t].add(c)
                for name, (autouse, _) in self.fixtures[c].items():
                    if not autouse:
                        if text is None:
                            text = self.tree.read(t) or ""
                        if not re.search(r"\b%s\b" % re.escape(name), text):
                            continue
                    self.edges[t].add("%s::%s" % (c, name))
        # tests that read the whole checkout (ratchets, lints): no import edge points at them, any diff can turn them red
        self.scanners = sorted(t for t in self.tests if SCANNER_RE.search(self.tree.read(t) or ""))
        self.rev = defaultdict(set)
        for a, bs in self.edges.items():
            for b in bs:
                self.rev[b].add(a)

    def _conftest_chain(self, path: str) -> list:
        """conftest.py files that apply to `path`: its own directory and every directory above it."""
        out, d = [], os.path.dirname(path)
        while True:
            c = (d + "/conftest.py") if d else "conftest.py"
            if c in self.pyset:
                out.append(c)
            if not d:
                return out
            d = os.path.dirname(d)

    def _package_of(self, p: str) -> str | None:
        if not hasattr(self, "_names_of"):
            self._names_of = defaultdict(list)
            for name, files in self.modules.items():
                for f in files:
                    self._names_of[f].append(name)
        names = sorted(self._names_of.get(p, []), key=len)  # shortest = the innermost import root
        if not names:
            return None
        return names[0] if p.endswith("__init__.py") else names[0].rpartition(".")[0]

    def _resolve(self, src: str, dotted: str) -> set:
        """Files for `dotted` plus the __init__.py of every enclosing package."""
        out = set()
        names = [dotted]
        for old, new in self.aliases.items():
            if dotted == old or dotted.startswith(old + "."):
                names.append(new + dotted[len(old):])
        for name in names:
            parts = name.split(".")
            for i in range(1, len(parts) + 1):
                out |= self.modules.get(".".join(parts[:i]), set())
        return out - {src}

    def _scan(self, p: str) -> None:
        text = self.tree.read(p)
        if text is None:
            return
        key = (p, self.lazy_imports, hash(text))
        if key not in _EXTRACT_CACHE:
            _EXTRACT_CACHE[key] = _extract(p, text, self.lazy_imports)
        recs = _EXTRACT_CACHE[key]
        if recs is None:
            self.parse_errors.append(p)
            return
        for node_id, fixture, imports in recs:
            if fixture is not None:
                self.fixtures[p][node_id.split("::", 1)[1]] = fixture
            self._add_imports(p, node_id, imports)

    def _add_imports(self, p: str, node_id: str, imports) -> None:
        pkg = None
        for module, level, names in imports:
            if level:
                if pkg is None:
                    pkg = self._package_of(p) or ""
                up = pkg.split(".") if pkg else []
                up = up[:len(up) - (level - 1)] if level > 1 else up
                base = ".".join([x for x in up + ([module] if module else []) if x])
            else:
                base = module
            if names is None:  # `import a.b` / import_module("a.b")
                targets = [base]
            else:
                targets = ([base] if base else []) + [(base + "." + n) if base else n for n in names]
            for t in targets:
                hit = self._resolve(p, t)
                if hit:
                    self.edges[node_id] |= hit
                elif not level:
                    self.third_party[node_id].add(t.split(".")[0].lower())

    def dependents(self, seeds) -> set:
        seen, q = set(seeds), deque(seeds)
        while q:
            for nxt in self.rev.get(q.popleft(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        return seen

    def importers_of_dists(self, dists) -> set:
        names = set()
        for d in dists:
            names |= import_names_for_dist(d)
        return {p for p, tops in self.third_party.items() if tops & names}

    def mentioning(self, path: str) -> set:
        needles = {path}
        b = os.path.basename(path)
        if len(b) >= 6 and "." in b:  # bare basenames like `a.json` match too much
            needles.add(b)
        out = set()
        for p in self.py:
            text = self.tree.read(p) or ""
            if any(n in text for n in needles):
                out.add(p)
        return out


def lock_versions(text: str | None, name: str) -> dict:
    out = {}
    if not text:
        return out
    if name.endswith(".lock"):
        for m in re.finditer(r'(?ms)^\[\[package\]\]\s*\nname\s*=\s*"([^"]+)"\s*\nversion\s*=\s*"([^"]+)"', text):
            out[norm_dist(m.group(1))] = m.group(2)
        if not out:  # Pipfile.lock is JSON
            try:
                for sect in json.loads(text).values():
                    if isinstance(sect, dict):
                        for k, v in sect.items():
                            if isinstance(v, dict) and "version" in v:
                                out[norm_dist(k)] = v["version"]
            except (ValueError, AttributeError):
                pass
        return out
    for line in text.splitlines():  # requirements*.txt, pyproject.toml: one requirement per line
        m = re.match(r'''^\s*["']?([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([<>=!~].*?)?["']?,?\s*(#.*)?$''',
                     line)
        if m and (m.group(3) or name.endswith(".txt")):
            out[norm_dist(m.group(1))] = (m.group(3) or "").strip()
    return out


def changed_dists(path: str, head: Tree, base: Tree | None) -> set:
    name = os.path.basename(path)
    new = lock_versions(head.read(path), name)
    old = lock_versions(base.read(path), name) if base else {}
    return {k for k in set(new) | set(old) if new.get(k) != old.get(k)}


def is_dep_manifest(path: str) -> bool:
    b = os.path.basename(path)
    return b in LOCKFILES or b == "pyproject.toml" or (b.startswith("requirements") and b.endswith(".txt"))


def own_package_tests(path: str, tests: list, allow_root: bool) -> set:
    """Tests under the nearest enclosing directory of `path` that has any; the repo root only if allow_root."""
    d = os.path.dirname(path)
    while d:
        hit = {t for t in tests if t.startswith(d + "/")}
        if hit:
            return hit
        d = os.path.dirname(d)
    return set(tests) if allow_root else set()


def select(root, changed, deleted, base_rev=None, tests_paths=("tests",), source_roots=(), aliases=None,
           graphs=None, lazy_imports="skip"):
    head_tree = Tree(root)
    head = Graph(head_tree, list(tests_paths), list(source_roots), aliases, lazy_imports)
    if graphs is not None:
        graphs["head"] = head
    base_tree = Tree(root, base_rev) if base_rev else None
    base = None
    tests_at_head = set(head.tests)
    why = defaultdict(set)  # test -> reasons
    seeds = set()
    unreached = []
    for p in changed:
        if is_test_file(p) and p in tests_at_head:
            why[p].add("changed")
        if p.endswith(".py") and p in head.pyset:
            seeds.add(p)
        elif is_dep_manifest(p):
            dists = changed_dists(p, head_tree, base_tree)
            hit = head.importers_of_dists(dists)
            seeds |= hit
            if not hit:
                unreached.append(p)
        elif not p.endswith(".py"):
            hit = head.mentioning(p)
            seeds |= hit
            if not hit:
                unreached.append(p)
    for s in seeds:
        for t in head.dependents([s]) & tests_at_head:
            why[t].add("graph")
    if deleted:
        if base_tree is None:
            unreached.extend(deleted)
        else:
            base = Graph(base_tree, list(tests_paths), list(source_roots), aliases, lazy_imports)
            for p in deleted:
                if p in base.pyset:
                    hit = base.dependents([p]) & tests_at_head
                elif not p.endswith(".py"):  # an asset: the tests of the modules that named it at the base
                    hit = base.dependents(base.mentioning(p)) & tests_at_head
                else:
                    hit = set()
                for t in hit:
                    why[t].add("deleted")
                if not hit:
                    unreached.append(p)
    # Never zero. Order: the graph; else the changed files' own packages (below the repo root); else the
    # repo-wide scanners (the tests that read every file, so the only ones a file nobody names can turn red);
    # only a repo with none of those runs its root package, i.e. the suite.
    fallback = False
    if not why and head.tests and (changed or deleted):
        fallback = True
        for p in list(changed) + list(deleted):
            for t in own_package_tests(p, head.tests, allow_root=False):
                why[t].add("own-package")
        if not why and not head.scanners:
            for t in head.tests:
                why[t].add("own-package")
    if changed or deleted:
        for t in head.scanners:
            why[t].add("scanner")
    selected = sorted(why)
    summary = {
        "selected": len(selected), "total": len(head.tests), "changed": len(changed), "deleted": len(deleted),
        "fallback_own_package": fallback, "unreached": sorted(unreached)[:20],
        "parse_errors": head.parse_errors[:20],
        "by_reason": {r: sum(1 for t in selected if r in why[t])
                      for r in ("changed", "graph", "deleted", "own-package", "scanner")},
    }
    return selected, summary


def read_list(path):
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--changed-file", help="file listing added/modified paths, one per line")
    ap.add_argument("--deleted-file", help="file listing deleted paths, one per line")
    ap.add_argument("--base", help="git rev of the PR base (deleted files' importers, lockfile diff)")
    ap.add_argument("--tests-path", action="append", default=None,
                    help="directory holding the suite (repeatable; default tests; '.' = whole repo)")
    ap.add_argument("--source-root", action="append", default=[], help="extra import root (src/ is automatic)")
    ap.add_argument("--alias", action="append", default=[], metavar="OLD=NEW",
                    help="runtime alias package: `import OLD.x` loads NEW.x (repeatable)")
    ap.add_argument("--lazy-imports", choices=("skip", "follow"), default="skip",
                    help="follow function-level imports of non-test modules (default skip)")
    ap.add_argument("--all", action="store_true", help="print the full suite (the CI_PR_SELECTION=off list)")
    ap.add_argument("--out")
    ap.add_argument("--summary-out")
    a = ap.parse_args(argv)
    tests_paths = a.tests_path or ["tests"]
    aliases = dict(x.split("=", 1) for x in a.alias if "=" in x)
    if a.all:
        g = Graph(Tree(a.root), tests_paths, a.source_root, aliases, a.lazy_imports)
        selected, summary = g.tests, {"selected": len(g.tests), "total": len(g.tests), "mode": "full"}
    else:
        selected, summary = select(a.root, read_list(a.changed_file), read_list(a.deleted_file), a.base,
                                   tests_paths, a.source_root, aliases, lazy_imports=a.lazy_imports)
        summary["mode"] = "selected"
    text = "".join(t + "\n" for t in selected)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    line = json.dumps(summary, sort_keys=True)
    if a.summary_out:
        with open(a.summary_out, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
    else:
        sys.stderr.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
