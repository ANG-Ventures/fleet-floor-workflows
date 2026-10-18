import json, os, re, sys

# A FIX PR that changes code must change a test, or carry `no-regression-test: <reason>` (>= 15 chars)
# in its body. Input: one JSON object {title, branch, body, files: [{path, status, mode?, patch_head?}]}.
# status: added|modified|renamed|removed|copied|changed (REST names; git letters A/M/R/D/C/T also accepted).
# Rule source: coding-guardrails SKILL.md "Every bug fix ships a regression test" (Ace 2026-10-18).

# --- FIX classification (title or head branch) ---
CONVENTIONAL = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*(fix|hotfix|bugfix|bug)(\([^)]*\))?!?\s*:", re.I)
FIX_WORDS = re.compile(r"\b(fix(?:es|ed|ing)?|hotfix(?:es)?|bug(?:fix)?s?|regress(?:ion|ions|ed|es)?|broke|broken"
                       r"|repair(?:s|ed|ing)?|restore[sd]?|restoring)\b", re.I)
# Words that look like a fix but are not: "fixture(s)", "prefix", "suffix" are excluded by \b; these are
# phrases where the word is about something else.
NOT_FIX = re.compile(r"\b(?:fix(?:ed)?[- ](?:point|width|size|rate|price)|bug[- ]?(?:report|bounty|tracker)s?"
                     r"|restore[sd]?[- ]?(?:drill|test|point)s?|backup and restore)\b", re.I)
PARITY = re.compile(r"\bparity\b|\bupstream[- ]sync\b|\bsync(?:ing|ed)?\s+(?:with|from|to)\s+upstream\b"
                    r"|\bmerge\s+upstream\b", re.I)
BRANCH_FIX = re.compile(r"(?:^|[/_.-])(fix|hotfix|bugfix|bug|regress(?:ion)?|repair|restore|parity)(?:$|[/_.-])", re.I)

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
                           r"|test-.+\.(?:sh|bash|py|js|ts)|.+-test\.(?:sh|bash|py|js|ts)|suites\.json)$", re.I)

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


def verdict(pr):
    """-> dict(result in {pass, fail}, reason, details...). Pure; the replay and the unit tests call it."""
    is_fix, why = classify(pr.get("title"), pr.get("branch"))
    files = [dict(f, status=norm_status(f.get("status"))) for f in pr.get("files") or []]
    live = [f for f in files if f["status"] != "removed"]
    code = [f["path"] for f in live if is_code(f)]
    tests = [f["path"] for f in files if is_test(f["path"])]
    out = {"fix": is_fix, "why": why, "code": code, "tests": tests}
    if not is_fix:
        return dict(out, result="pass", reason=why)
    if not code:
        return dict(out, result="pass", reason="fix PR changes no code (docs/config/deletions only)")
    if tests:
        return dict(out, result="pass", reason="fix PR changes %d test file(s)" % len(tests))
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
test_*.py, *_test.*, *.test.*, *.spec.*, test-*.sh, conftest.py, *selftest*, suites.json."""


def summary_md(pr, v):
    lines = ["### regression-test-lint: **%s**" % v["result"].upper(), "",
             "- classification: %s (%s)" % ("FIX" if v["fix"] else "not a fix", v["why"]),
             "- code files: %d; test files: %d" % (len(v["code"]), len(v["tests"])),
             "- verdict: %s" % v["reason"]]
    if v.get("exemption"):
        lines.append("- `no-regression-test:` %s" % v["exemption"])
    return "\n".join(lines) + "\n"


def main(path):
    pr = json.load(open(path, encoding="utf-8"))
    v = verdict(pr)
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
