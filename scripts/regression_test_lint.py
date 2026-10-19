import json, os, re, sys

# A FIX PR that changes code must change a test, or carry `no-regression-test: <reason>` (>= 15 chars)
# in its body. Input: one JSON object {title, branch, body, files: [{path, status, mode?, patch_head?}]}.
# status: added|modified|renamed|removed|copied|changed (REST names; git letters A/M/R/D/C/T also accepted).
# Rule source: coding-guardrails SKILL.md "Every bug fix ships a regression test" (Ace 2026-10-18).

# --- FIX classification (title or head branch) ---
CONVENTIONAL = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*(fix|hotfix|bugfix|bug)(\([^)]*\))?!?\s*:", re.I)
# A word joined by - _ . to another (continuity-regression.sh, regression-test) is a name, not a verb.
FIX_WORDS = re.compile(r"(?<![-_.\w])(fix(?:es|ed|ing)?|hotfix(?:es)?|bug(?:fix)?s?|regress(?:ion|ions|ed|es)?"
                       r"|broke|broken|repair(?:s|ed|ing)?|restore[sd]?|restoring)(?![-_.]?\w)", re.I)
# Phrases where the word is about something else ("fixture", "prefix" are already excluded by the
# boundaries). Each shape is a replay false positive or its obvious sibling.
NOT_FIX = re.compile(r"\bfixed[- ](?:\d\S*|ip|roi|route|point|width|size|rate|price)\b"  # adjective: "fixed 600 s"
                     r"|\bfix[- ](?:point|width|size)\b"
                     r"|\bbug[- ]?(?:report|bounty|tracker)s?\b"
                     r"|\bnot\s+(?:a|an)\s+(?:[\w-]+\s+){0,2}bug\b"                    # "not an mddc bug"
                     r"|\S+'s\s+(?:[\w-]+\s+){0,2}fix(?:es)?\b"                       # "#4003's Prism fixes"
                     r"|\brestore[sd]?[- ]?(?:drill|test|point)s?\b|\bbackup and restore\b", re.I)
# A home-autocommit drain PR lands whatever the live tree held; its words describe other work.
DRAIN = re.compile(r"^\s*drain:|\[home-autocommit drain-pr\]", re.I)
# Parity = an upstream parity MERGE/SYNC, not any title with the word (pipecat's satellite_parity and
# fleet-ops' "image parity" are features). Shapes from hermes-agent history: "parity: upstream sync ...",
# "merge: parity sync with upstream", "sync: upstream parity merge", "parity 2026-10-01 CI round-5 ...".
PARITY = re.compile(r"\bparity[ :-]+(?:sync|merge)\b|\bupstream[ -](?:sync|parity|merge)\b|\bmerge\s+upstream\b"
                    r"|\bsync(?:ing|ed)?\s+(?:with|from)\s+upstream\b|^\s*parity\s+\d{4}-\d\d-\d\d\b", re.I)
# Branch: only unambiguous segments. "repair"/"restore" in a branch name described the subject
# (reolink-mac-client-repair = a runbook), so they classify from the title only.
BRANCH_FIX = re.compile(r"(?:^|[/_.-])(fix|hotfix|bugfix|bug|regress(?:ion)?|parity-(?:sync|merge))"
                        r"(?:$|[/_.-])", re.I)

# --- code files ---
CODE_EXT = {".py", ".pyi", ".sh", ".bash", ".zsh", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs",
            ".go", ".rs", ".rb", ".pl", ".pm", ".php", ".lua", ".swift", ".m", ".mm", ".c", ".h", ".cc", ".cpp",
            ".hpp", ".cxx", ".java", ".kt", ".kts", ".scala", ".cs", ".ps1", ".psm1", ".applescript", ".vue",
            ".svelte", ".sql", ".awk", ".fish"}
# Extensionless files that are never code even when executable-looking.
NON_CODE_BASENAMES = {"license", "licence", "readme", "codeowners", "notice", "authors", "changelog", "makefile",
                      "dockerfile", "procfile", "gemfile", "brewfile", "justfile", "version", "owners", "copying"}
EXEC_DIRS = re.compile(r"(?:^|/)(?:bin|sbin|libexec|scripts?|hooks|githooks|\.githooks|tools|cron)/")

# --- test files (each fleet repo's real layout) ---
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testdata", "test_data", "fixtures", "selftest",
             "selftests", "e2e", "golden", "goldens", "testing"}
TEST_BASENAME = re.compile(r"^(?:test_.+|.+_test\.[^.]+|.+\.(?:test|spec)\.[^.]+|conftest\.py|.*selftest.*"
                           r"|test-.+\.[^.]+|.+-test\.[^.]+|suites\.json)$", re.I)  # golden-projection-test.ps1

EXEMPT_LINE = re.compile(r"^[ \t>*_`-]*no-regression-test[ \t*_`]*:[ \t]*(.*?)[ \t*_`]*$", re.I | re.M)
MIN_REASON = 15


def ext(p):
    b = p.rsplit("/", 1)[-1]
    if b.startswith(".") and b.count(".") == 1:
        return None  # dotfile (.gitignore, .env.example): never code
    return "" if "." not in b else "." + b.rsplit(".", 1)[-1].lower()


def is_test(path):
    parts = path.split("/")
    return (any(s.lower() in TEST_DIRS for s in parts[:-1])
            or TEST_BASENAME.match(parts[-1]) is not None)


def is_code(f):
    """Code file, excluding tests. Extensionless counts when executable (mode 100755), shebang in the
    patch head, or under a bin/scripts/hooks/tools dir."""
    p = f["path"]
    if is_test(p):
        return False
    e = ext(p)
    if e is None:
        return False
    if e in CODE_EXT:
        return True
    if e != "":
        return False
    if p.rsplit("/", 1)[-1].lower() in NON_CODE_BASENAMES:
        return False
    if str(f.get("mode") or "").endswith("755"):
        return True
    head = f.get("patch_head") or ""
    if re.search(r"^[+ ]#!", head, re.M) or head.startswith("#!"):
        return True
    return EXEC_DIRS.search(p) is not None


def norm_status(s):
    s = (s or "modified").lower()
    return {"a": "added", "m": "modified", "r": "renamed", "d": "removed", "c": "copied", "t": "changed"}.get(s[:1] if len(s) <= 4 else s, s)


def classify(title, branch):
    """-> (is_fix, why)"""
    t = title or ""
    m = CONVENTIONAL.match(t)
    if m:
        return True, "conventional %s:" % m.group(1).lower()
    if PARITY.search(t):
        return True, "parity/sync title"
    if DRAIN.search(t):
        return False, "drain PR (lands live-tree edits)"
    scrubbed = NOT_FIX.sub(" ", t)
    m = FIX_WORDS.search(scrubbed)
    if m:
        return True, "title word %r" % m.group(1)
    m = BRANCH_FIX.search(branch or "")
    if m:
        return True, "branch %r" % m.group(1)
    return False, "not a fix (title/branch)"


def exemption(body):
    """-> (state, reason): state is 'ok', 'short' or 'absent'."""
    best = None
    for m in EXEMPT_LINE.finditer(body or ""):
        r = m.group(1).strip()
        if len(r) >= MIN_REASON:
            return "ok", r
        best = r
    return ("short", best) if best is not None else ("absent", "")


PATH_TOKEN = re.compile(r"[\w.-]+(?:/[\w.-]+)+|[\w.-]+\.(?:py|sh|bash|js|ts|mjs|go|rs)\b")
# A top-level name that holds in-file test cases (hermes-home lints carry `SELF_TESTS = [...]` run by
# `--self-test`; vh#4963 added 7 fixtures there and no test file).
SELFTEST_OWNER = re.compile(r"^(?:def\s+|class\s+|async\s+def\s+|(?:readonly\s+|declare\s+-a\s+)?)?"
                            r"(\w*(?:self_?test|selftest|test_?cases|fixtures|goldens?)\w*)\b", re.I)


def added_lines(patch):
    """-> [(new line number, text)] for every '+' line of a unified-diff patch."""
    out, ln = [], 0
    for raw in (patch or "").splitlines():
        m = re.match(r"@@ -\S+ \+(\d+)(?:,\d+)? @@", raw)
        if m:
            ln = int(m.group(1)); continue
        if raw.startswith("+"):
            out.append((ln, raw[1:])); ln += 1
        elif not raw.startswith("-"):
            ln += 1
    return out


# Files where an added line naming a test REGISTERS it (CI workflow, suite manifest, task runner). A comment,
# docstring or README line that mentions a test path is not a registration (Prism #35, t_23b5215f).
REGISTRY_FILE = re.compile(r"(?:^|/)(?:\.github/(?:workflows|ci)/[^/]+|\.gitlab-ci\.ya?ml|\.circleci/[^/]+|suites\.json"
                           r"|makefile|justfile|tox\.ini|pytest\.ini|noxfile\.py|package\.json|run-checks[^/]*)$", re.I)
COMMENT_LINE = re.compile(r"^\s*(?:#|//|;|<!--)")
MAX_REG_FETCH = 20


def registered_tests(files, fetch=None):
    """Test files an ADDED, non-comment line of a CI/registry file names (a CI step, suites.json row): registering
    an existing test so CI runs it is the fix (vh#3135 put test_audit_fallback_chain_rot.py into the lane). The
    named test must exist at head: a file the PR adds/keeps, or one fetch(path) can read. No fetch = no credit.
    A glob (`tests/**`, `tests/test_*.py`) yields no token with a file extension, so it names no test."""
    live = {f["path"] for f in files if f.get("status") != "removed"}
    found, checked = [], 0
    for f in files:
        if f.get("status") == "removed" or not REGISTRY_FILE.search(f["path"]):
            continue
        for _, text in added_lines(f.get("patch")):
            if COMMENT_LINE.match(text):
                continue
            for tok in PATH_TOKEN.findall(text):
                tok = tok.strip("./")
                if "." not in tok.rsplit("/", 1)[-1] or not is_test(tok) or tok in found:
                    continue
                if tok not in live:
                    if fetch is None or checked >= MAX_REG_FETCH:
                        continue
                    checked += 1
                    if not fetch(tok):
                        continue
                found.append(tok)
    return found


def inline_selftests(files, fetch):
    """Code files whose ADDED lines sit inside a top-level self-test/fixture table of the head file.
    fetch(path) -> head file text or None (REST in CI; `head_text` on the fixture in tests).
    Scope: an added, non-comment line is credited when it is itself a table's opening line, or it is indented
    and the nearest column-0 line above it opens the table. A column-0 closer (`]`, `)`, `}`) above it ends
    the table, so a line after the table gets nothing (Prism #35, t_23b5215f)."""
    found = []
    for f in files:
        if not is_code(f) or f["status"] == "removed":
            continue
        adds = [ln for ln, t in added_lines(f.get("patch")) if t.strip() and not COMMENT_LINE.match(t)]
        if not adds:
            continue
        text = f.get("head_text")
        if text is None and fetch is not None:
            text = fetch(f["path"])
        if not text:
            continue
        lines = text.splitlines()
        for ln in adds:
            if not 1 <= ln <= len(lines):
                continue
            cur = lines[ln - 1]
            if COMMENT_LINE.match(cur) or not cur.strip():
                continue
            owner = None
            if not cur[0].isspace():
                owner = cur
            else:
                for k in range(ln - 2, -1, -1):
                    s = lines[k]
                    if s and not s[0].isspace() and not COMMENT_LINE.match(s):
                        owner = s
                        break
            m = SELFTEST_OWNER.match(owner) if owner and not owner.startswith((")", "]", "}")) else None
            if m:
                found.append("%s:%d in %s" % (f["path"], ln, m.group(1)))
                break
    return found


def verdict(pr, fetch=None):
    """-> dict(result in {pass, fail}, reason, details...). The replay and the unit tests call it.
    fetch(path) reads a head file; it is only called for a fix PR that would otherwise fail."""
    is_fix, why = classify(pr.get("title"), pr.get("branch"))
    files = [dict(f, status=norm_status(f.get("status"))) for f in pr.get("files") or []]
    live = [f for f in files if f["status"] != "removed"]
    code = [f["path"] for f in live if is_code(f)]
    # A removed test is not a test change: deleting the failing test must not satisfy the rule.
    tests = [f["path"] for f in live if is_test(f["path"])]
    out = {"fix": is_fix, "why": why, "code": code, "tests": tests}
    if not is_fix:
        return dict(out, result="pass", reason=why)
    # The PR-files endpoint stops at 3,000 files; a capped list cannot prove "no code" or "no test".
    total = pr.get("changed_files")
    if isinstance(total, int) and total > len(files):
        state, reason = exemption(pr.get("body"))
        if state == "ok":
            return dict(out, exemption=reason, result="pass", reason="no-regression-test: " + reason)
        return dict(out, result="fail", reason="PR file inventory incomplete: %d of %d changed files listed "
                    "(REST cap 3000); cannot judge, add a no-regression-test line or split the PR" % (len(files), total))
    if not code:
        return dict(out, result="pass", reason="fix PR changes no code (docs/config/deletions only)")
    if tests:
        return dict(out, result="pass", reason="fix PR changes %d test file(s)" % len(tests))
    reg = registered_tests(files, fetch)
    if reg:
        return dict(out, tests=reg, result="pass", reason="fix PR registers test(s) in CI: %s" % ", ".join(reg[:5]))
    inline = inline_selftests(files, fetch)
    if inline:
        return dict(out, tests=inline, result="pass", reason="fix PR adds in-file self-test case(s): %s" % ", ".join(inline[:5]))
    state, reason = exemption(pr.get("body"))
    out["exemption"] = reason
    if state == "ok":
        return dict(out, result="pass", reason="no-regression-test: " + reason)
    if state == "short":
        return dict(out, result="fail", reason="no-regression-test reason is %d chars (< %d): %r" % (len(reason), MIN_REASON, reason))
    return dict(out, result="fail", reason="fix PR changes code and no test file, and the body has no no-regression-test line")


TEMPLATE = """This PR is classified as a FIX (%s) and changes code, but adds or changes no test file.
Every bug fix ships a regression test (coding-guardrails, "Every bug fix ships a regression test").
Either add the test, or add ONE line to the PR body and re-run this job:

  no-regression-test: <why no test can pin this, >= 15 chars>

Test files this check recognises: tests/ test/ __tests__/ spec/ fixtures/ selftest/ e2e/ dirs at any depth,
test_*.py, *_test.*, *.test.*, *.spec.*, test-*.sh, conftest.py, *selftest*, suites.json. Also credited: an added
line naming a test file (CI step / suites.json registration), and added rows inside an in-file SELF_TESTS table."""


def summary_md(pr, v):
    lines = ["### regression-test-lint: **%s**" % v["result"].upper(), "",
             "- classification: %s (%s)" % ("FIX" if v["fix"] else "not a fix", v["why"]),
             "- code files: %d; test files: %d" % (len(v["code"]), len(v["tests"])),
             "- verdict: %s" % v["reason"]]
    if v.get("exemption"):
        lines.append("- `no-regression-test:` %s" % v["exemption"])
    return "\n".join(lines) + "\n"


def rest_fetcher():
    """fetch(path) over the contents API at the PR head (env REPO, HEAD_SHA, GH_TOKEN); None if unset."""
    repo, sha, tok = os.environ.get("REPO"), os.environ.get("HEAD_SHA"), os.environ.get("GH_TOKEN")
    if not (repo and sha and tok):
        return None
    import urllib.parse, urllib.request

    def fetch(path):
        url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, urllib.parse.quote(path), sha)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok,
                                                   "Accept": "application/vnd.github.raw"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(2_000_000).decode("utf-8", "replace")
        except Exception as e:  # fail toward the honest red: no fetch = no in-file credit
            print("::warning::regression-test-lint: could not read %s at head (%s)" % (path, e))
            return None
    return fetch


def main(path):
    pr = json.load(open(path, encoding="utf-8"))
    v = verdict(pr, rest_fetcher())
    print("regression-test-lint: %s (%s); code=%d tests=%d" % (v["result"].upper(), v["why"], len(v["code"]), len(v["tests"])))
    for p in v["code"][:30]:
        print("  code: %s" % p)
    for p in v["tests"][:30]:
        print("  test: %s" % p)
    print("verdict: %s" % v["reason"])
    if v.get("exemption") and v["result"] == "pass":
        print("::notice::no-regression-test: %s" % v["exemption"][:300])
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if s:
        with open(s, "a", encoding="utf-8") as fh:
            fh.write(summary_md(pr, v))
    if v["result"] == "fail":
        print("::error::regression-test-lint FAIL: %s" % v["reason"])
        print(TEMPLATE % v["why"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
