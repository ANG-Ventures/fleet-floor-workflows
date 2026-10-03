"""ne-pair-floor caller `paths:` must cover every file the scanner opens (t_62fba842, Prism fe507cbf05a1).

A skipped run is only safe when the commit touches no file the scanner reads. The scanner's eligibility is
`is_candidate()` (suffix, `.env*`, or ext-less shebang), so this test feeds paths that ARE eligible
through the template's filter and fails on any one the filter drops. Base had `**/.env*` only, so a
shebang script named exactly `.txt` (splitext -> ext "") was scanned but skipped by `!**.txt`.

Filter semantics follow GitHub's: minimatch with dot matching, `**` crosses `/`, last matching pattern wins.
"""
from pathlib import Path
import re
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/ne-pair-floor.yml"
sys.path.insert(0, str(ROOT / "scripts"))
import ne_pair_floor as npf  # noqa: E402

SHEBANG = b"#!/bin/sh\nexport DISABLE_TELEMETRY=1\nclaude -p hi\n"


def glob_rx(pat):
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return re.compile(out + r"\Z")


def triggers(patterns, path):
    hit = False
    for p in patterns:
        neg = p.startswith("!")
        if glob_rx(p[1:] if neg else p).match(path):
            hit = not neg
    return hit


def filters():
    on = yaml.load(TEMPLATE.read_text(), Loader=yaml.BaseLoader)["on"]
    return {ev: on[ev]["paths"] for ev in ("pull_request", "push")}


def excluded_suffixes(patterns):
    return [p[len("!**"):] for p in patterns if p.startswith("!**.")]


class NePairPathFilterCoversScanner(unittest.TestCase):
    def candidates(self, patterns):
        """Paths the scanner would open: every excluded suffix as a dot-basename, `.env.<sfx>`,
        each SUFFIXES type, and ext-less scripts, at the root and nested."""
        names = [".ne-pair-floor-allow", "spawn", ".env"] + ["x" + s for s in sorted(npf.SUFFIXES)]
        for sfx in excluded_suffixes(patterns):
            names += [sfx, ".env" + sfx]
        return names

    def test_every_scanned_path_triggers_the_workflow(self):
        for ev, patterns in filters().items():
            self.assertTrue(excluded_suffixes(patterns), "template lost its exclusions; test is vacuous")
            with tempfile.TemporaryDirectory() as d:
                for name in self.candidates(patterns):
                    f = Path(d) / name
                    f.write_bytes(SHEBANG)
                    if not npf.is_candidate(str(f), name):
                        continue  # e.g. .ne-pair-floor-allow: no shebang-interpreter match needed
                    for rel in (name, "a/b/" + name, ".github/" + name):
                        self.assertTrue(triggers(patterns, rel),
                                        "%s: scanner opens %r but paths: skips it" % (ev, rel))

    def test_docs_still_skip(self):
        # The filter must still do its job: plain docs/data never trigger (and the scanner never opens them).
        for ev, patterns in filters().items():
            with tempfile.TemporaryDirectory() as d:
                for sfx in excluded_suffixes(patterns):
                    name = "notes" + sfx
                    (Path(d) / name).write_bytes(SHEBANG)
                    self.assertFalse(npf.is_candidate(str(Path(d) / name), name), name)
                    self.assertFalse(triggers(patterns, "docs/" + name), "%s: %s" % (ev, name))

    def test_allow_file_triggers(self):
        # `.ne-pair-floor-allow` is read by the scanner (opt-outs) whatever is_candidate says.
        for ev, patterns in filters().items():
            self.assertTrue(triggers(patterns, ".ne-pair-floor-allow"), ev)


if __name__ == "__main__":
    unittest.main()
