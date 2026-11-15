#!/usr/bin/env python3
"""Re-embed scripts/select_tests.py into .github/workflows/select-tests.yml (tests/test_select_tests.py pins equality)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "select-tests.yml"
s = WF.read_text()
start = s.index("<<'PY'\n") + len("<<'PY'\n")
end = s.index("          PY\n", start)
script = (ROOT / "scripts" / "select_tests.py").read_text()
WF.write_text(s[:start] + "".join(("          " + l) if l.strip() else "\n" for l in script.splitlines(True)) + s[end:])
