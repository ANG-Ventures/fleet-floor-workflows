import os, re, sys

VOCAB = re.compile(r"refus|never|not allowed|orchestrator-only|workers? never|must not|denied|forbidden", re.I)
ANCHOR = re.compile(r"raise\s+[\w.]*(?:Error|Exception|Refused)\b\s*\(|\bemit_block\s*\("
                    r"|\bsys\.exit\s*\(\s*(?:[1-9]|[\"'fF])|\bHTTPException\s*\(\s*(?:status_code\s*=\s*)?409\b")
CODE_EXT = {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rb", ".pl"}
HASH_COMMENT_EXT = {".py", ".sh", ".bash", ".rb", ".pl", ""}
# Seed: register class A rows ("none by design"). `AI/Operator Capabilities — Removed, Gated,
# and Their Overrides` (t_674b3ce7). kind, regex, why.
SEED = [
    ("path", r"(^|/)hooks/guard_self_disable_policy\.py$", "gate 3 guard self-disable (class A, none by design)"),
    ("path", r"(^|/)hooks/live_tree_branch_policy\.py$", "H3 gate 7 live-tree branch (class A, none by design)"),
    ("path", r"(^|/)hooks/live_tree_bare_init_policy\.py$", "H3 gate 8 live-tree bare init (class A, none by design)"),
    ("path", r"(^|/)profiles/momus/hooks/momus_policy\.py$", "H14 momus read-only gate (class A, none by design)"),
    ("line", r"kanban live-system guard|LiveBoardWriteRefused|_refuse_live_kanban_write",
     "K13 live-board guard (class A, none by design)"),
]
OVERRIDE_LINE = re.compile(r"^[ \t>*_-]*\**[ \t]*override[ \t]*\**[ \t]*:[ \t]*\**(.*)$", re.I | re.M)
NONE_BY_DESIGN = re.compile(r"^none\s+by\s+design\s*[—–:-]+\s*(\S.{2,})", re.I)
TOKEN = re.compile(r"(--[a-z][a-z0-9-]+)|\b([A-Z][A-Z0-9]*_[A-Z0-9_]+)\b|\b([a-z]+_[a-z0-9_]+(?:=\S+)?)\b")
PROSE_NONE = re.compile(r"\bno\s+(?:[\w-]+\s+){0,3}overrides?\b|\bnone\s+by\s+design\b", re.I)
# Prose fallback only credits OVERRIDE-SHAPED names (not any flag: `--model` is not an escape hatch).
PROSE_NAMED = re.compile(r"(--(?:[a-z0-9]+-)*(?:allow|force|override|bypass|skip|waive|break-glass|ace-approved"
                         r"|foreign-ok|operator|drop-key)[a-z0-9-]*)"
                         r"|\b([A-Z][A-Z0-9_]*(?:ALLOW|OVERRIDE|FORCE|BYPASS|SKIP|WAIVE|DISABLE|BREAK_GLASS)[A-Z0-9_]*)\b"
                         r"|\b([A-Z][A-Z0-9]*_[A-Z0-9_]+)=[01]\b")

def ext(p):
    b = p.rsplit("/", 1)[-1]
    return "" if "." not in b else "." + b.rsplit(".", 1)[-1].lower()

def is_test(p):
    parts = p.split("/")
    b = parts[-1]
    return (any(s in ("tests", "test", "__tests__", "testdata", "fixtures") for s in parts[:-1])
            or b.startswith("test_") or b == "conftest.py" or re.search(r"(_test\.\w+|\.(test|spec)\.\w+)$", b) is not None)

def in_scope(p, scope):
    for s in scope:
        if s.endswith("/"):
            if p.startswith(s) or ("/" + s) in p:
                return True
        elif p == s or p.endswith("/" + s):
            return True
    return False

def strings_of(line, e):
    """Text inside quotes on this line (unterminated -> to EOL); code after a # comment dropped."""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c == "#" and e in HASH_COMMENT_EXT and (i == 0 or line[i - 1] in " \t;"):
            break
        if c in "\"'`":
            j, buf = i + 1, []
            while j < n and line[j] != c:
                if line[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(line[j]); j += 1
            out.append("".join(buf)); i = j + 1; continue
        i += 1
    if e in (".sh", ".bash", "") and re.match(r"\s*(echo|printf|die|fail|err|warn)\b", line):
        out.append(line)
    return " ".join(out)

def is_comment(s):
    t = s.lstrip()
    return t.startswith(("#", "//", "/*", "*", "<!--")) and not t.startswith("#!")

def parse(diff):
    files, cur, ln = {}, None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            cur = None if p == "/dev/null" else re.sub(r"^b/", "", p)
            if cur is not None:
                files.setdefault(cur, [])
            continue
        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue
        m = re.match(r"@@ -\S+ \+(\d+)(?:,\d+)? @@", raw)
        if m:
            ln = int(m.group(1)); continue
        if cur is None:
            continue
        if raw.startswith("+"):
            files[cur].append((ln, raw[1:])); ln += 1
        elif raw.startswith(" "):
            ln += 1
    return files

def hits_of(files, scope):
    hits = []
    for p, lines in files.items():
        e = ext(p)
        if is_test(p) or not (e in CODE_EXT or e == ""):
            continue
        scoped = in_scope(p, scope)
        for k, (ln, text) in enumerate(lines):
            if is_comment(text):
                continue
            if scoped and VOCAB.search(strings_of(text, e)):
                hits.append((p, ln, text.strip())); continue
            if ANCHOR.search(text):
                window = [text] + [t for (l2, t) in lines[k + 1:k + 4] if l2 - ln <= 3]
                if VOCAB.search(" ".join(strings_of(t, e) for t in window)):
                    hits.append((p, ln, text.strip()))
    return hits

def allowlisted(hit, allow):
    p, _, text = hit
    for kind, rx, why in allow:
        if (kind == "path" and re.search(rx, p)) or (kind == "line" and re.search(rx, text)):
            return why
    return None

def parse_allow(extra):
    out = []
    for l in extra.splitlines():
        l = l.strip()
        m = re.match(r"(path|line):(\S+)\s*(.*)$", l)
        if m:
            out.append((m.group(1), m.group(2), m.group(3) or "caller allowlist"))
        elif l:
            print("::warning::override-lint: ignoring malformed allowlist entry %r" % l)
    return out

def tokens_of(value):
    toks = [(m.group(1) or m.group(2) or m.group(3)).split("=", 1)[0] for m in TOKEN.finditer(value)]
    for m in re.finditer(r"`([^`\n]+)`", value):  # backticked names with no flag/env shape inside
        inner = m.group(1)
        if not any(t in inner for t in toks):
            toks.append(inner.strip())
    return toks

def verdict(body, files):
    """-> (ok, how, tokens, message)"""
    lines = [m.group(1).strip().strip("*").strip() for m in OVERRIDE_LINE.finditer(body)]
    lines = [v for v in lines if v]
    for v in lines:
        if NONE_BY_DESIGN.match(v):
            return True, "Override line (none by design)", [], v
    for v in lines:
        if re.match(r"none\b", v, re.I):
            continue
        toks = tokens_of(v)
        if toks:
            return True, "Override line", toks, v
    if lines:
        return False, "Override line present but names nothing", [], lines[0]
    m = PROSE_NONE.search(body)
    if m:
        return True, "prose: states no override", [], m.group(0)
    added = "\n".join(t for p, ls in files.items() if not is_test(p) for (_, t) in ls)
    for m in PROSE_NAMED.finditer(body):
        t = m.group(1) or m.group(2) or m.group(3)
        if t in added or (t.startswith("--") and t[2:].replace("-", "_") in added):
            return True, "prose: names an override the diff adds", [t], t
    return False, "no Override line", [], ""

def documented(tok, roots, files):
    for p, ls in files.items():
        if p.endswith(".md") and any(tok in t for (_, t) in ls):
            return p + " (this PR)"
    for r in roots:
        r = os.path.expanduser(r)
        paths = [r] if os.path.isfile(r) else [os.path.join(d, f) for d, _, fs in os.walk(r, followlinks=True)
                                              for f in fs if f.endswith(".md")] if os.path.isdir(r) else []
        for fp in paths:
            try:
                if tok in open(fp, encoding="utf-8", errors="replace").read():
                    return fp
            except OSError:
                pass
    return None

TEMPLATE = """Add ONE of these lines to the PR body, then re-run this job:

  Override: `--flag-name "<reason>"` (or ENV_VAR=1 / a reason field) — <who uses it, how it is audited>
  Override: none by design — <why no operator may bypass this>

If an override exists, also name it in a skill (skills-shared/**.md) or the standing register note
`AI/Operator Capabilities — Removed, Gated, and Their Overrides`."""

def main(diff_path, body_path):
    files = parse(open(diff_path, encoding="utf-8", errors="replace").read())
    body = open(body_path, encoding="utf-8", errors="replace").read()
    scope = os.environ.get("OVERRIDE_LINT_SCOPE", "").split()
    allow = [(k, r, w) for k, r, w in SEED] + parse_allow(os.environ.get("OVERRIDE_LINT_ALLOWLIST", ""))
    hits = hits_of(files, scope)
    open_hits = []
    for h in hits:
        why = allowlisted(h, allow)
        if why:
            print("allowlisted: %s:%d  [%s]  %s" % (h[0], h[1], why, h[2][:140]))
        else:
            open_hits.append(h)
    print("override-lint: %d refusal hit(s), %d allowlisted, %d need an Override line"
          % (len(hits), len(hits) - len(open_hits), len(open_hits)))
    if not open_hits:
        return 0
    ok, how, toks, msg = verdict(body, files)
    if not ok:
        for i, (p, ln, text) in enumerate(open_hits):
            if i < 20:
                print("::error file=%s,line=%d::refusal added without an Override: line in the PR body" % (p, ln))
            print("  %s:%d  %s" % (p, ln, text[:160]))
        print("::error::override-lint FAIL (%s): this PR adds %d refusal line(s) and the body does not "
              "name the override or say there is none by design.%s" % (how, len(open_hits),
              (" Found: %r" % msg) if msg else ""))
        print(TEMPLATE)
        return 1
    print("override-lint PASS via %s: %s" % (how, msg[:200]))
    if how.startswith("prose"):
        print("::notice::override-lint accepted prose (%s). Prefer an explicit `Override:` line." % how)
    roots = os.environ.get("OVERRIDE_LINT_DOCS", "").split()
    reachable = [r for r in roots if os.path.exists(os.path.expanduser(r))]
    for t in toks:
        if not reachable:
            print("::warning::override-lint: cannot check that %r is documented: none of %s exists on this runner"
                  % (t, roots)); break
        where = documented(t, reachable, files)
        if where:
            print("documented: %s -> %s" % (t, where))
        else:
            print("::warning::override-lint: override %r appears in no skill under %s. Document it (skill or the "
                  "operator-capabilities register) so operators can find it." % (t, reachable))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
