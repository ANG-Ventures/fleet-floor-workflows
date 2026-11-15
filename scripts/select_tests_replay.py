#!/usr/bin/env python3
"""select-tests replay: what select-tests.yml would have done to a repo's last N days of merged PRs (card t_4c317916).

For every pull_request run of the repo's test workflow on a PR merged in the window, it
  * recomputes the selection at that run's head commit (diff merge-base(head, origin/main)..head) with
    scripts/select_tests.py, the same code the workflow embeds;
  * reads the run's real jobs (queue, setup, pytest step, post) and models the run with selection in front:
      select job  = the run's own trivial fan-in latency (queue + run of a no-op job on the same pool, measured)
                    + its shard checkout step + the selector's measured local runtime;
      each leg    = a real shard's queue + setup + post, with pytest time = the run's total measured pytest seconds
                    x (selected duration weight / full weight) / legs;
      wall        = select job + slowest leg + fan-in latency  (the actual wall is run created -> fan-in done);
  * for a red run, parses the failing test files out of the failed shard logs and reports any the selection skips.
Read-only: GitHub REST via `gh api`, plus a local clone.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_tests  # noqa: E402

ISO = "%Y-%m-%dT%H:%M:%SZ"


def ts(s):
    return dt.datetime.strptime(s, ISO).replace(tzinfo=dt.timezone.utc).timestamp() if s else None


def gh(path, paginate=False, raw=False):
    cmd = ["gh", "api", path] + (["--paginate"] if paginate else [])
    for i in range(4):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout if raw else (json.loads(r.stdout) if not paginate else
                                         _split_json(r.stdout))
        time.sleep(2 * (i + 1))
    raise RuntimeError("gh api %s: %s" % (path, r.stderr[-300:]))


def _split_json(text):
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        obj, i = dec.raw_decode(text, i)
        out.append(obj)
    return out


def cached(path, fn):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    v = fn()
    with open(path, "w") as fh:
        json.dump(v, fh)
    return v


def git(clone, *args, check=True):
    return subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True, check=check).stdout


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


FAIL_RE = re.compile(r"(?:FAILED|ERROR)\s+([\w./-]+\.py)(?:::|\s|$)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--clone", required=True, help="local clone with origin = the repo")
    ap.add_argument("--workflow", required=True, help="workflow file name, e.g. pytest.yml")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--shard-job-re", required=True, help="regex for the test leg job names")
    ap.add_argument("--pytest-step-re", default=r"pytest")
    ap.add_argument("--checkout-step-re", default=r"^Checkout")
    ap.add_argument("--other-job-re", help="regex for test jobs selection does not change; their end bounds the wall")
    ap.add_argument("--fanin-job", required=True, help="the job that reports the suite result")
    ap.add_argument("--durations", help="JSON {test file: seconds} at the repo root")
    ap.add_argument("--tests-path", action="append")
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--source-root", action="append", default=[])
    ap.add_argument("--lazy-imports", default="skip", choices=("skip", "follow"))
    ap.add_argument("--max-legs", type=int, default=1)
    ap.add_argument("--files-per-leg", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(os.path.join(a.out, "jobs"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "logs"), exist_ok=True)
    tests_paths = a.tests_path or ["tests"]
    aliases = dict(x.split("=", 1) for x in a.alias)
    now = time.time()
    since = now - a.days * 86400
    since_iso = time.strftime(ISO, time.gmtime(since))

    pulls = cached(os.path.join(a.out, "pulls.json"), lambda: [
        {"number": p["number"], "merged_at": p["merged_at"], "head_ref": p["head"]["ref"]}
        for page in gh("repos/%s/pulls?state=closed&sort=updated&direction=desc&per_page=100" % a.repo,
                       paginate=True) for p in page if p.get("merged_at") and ts(p["merged_at"]) >= since])
    merged = {p["number"] for p in pulls}
    by_branch = {p["head_ref"]: p["number"] for p in pulls}
    runs = cached(os.path.join(a.out, "runs.json"), lambda: [
        {"id": r["id"], "head_sha": r["head_sha"], "conclusion": r["conclusion"], "created_at": r["created_at"],
         "prs": [x["number"] for x in r.get("pull_requests") or []], "head_branch": r["head_branch"]}
        for page in gh("repos/%s/actions/workflows/%s/runs?event=pull_request&per_page=100&created=%%3E%%3D%s"
                       % (a.repo, a.workflow, since_iso), paginate=True) for r in page["workflow_runs"]])
    # PR numbers by branch, for runs whose pull_requests field is empty
    rows = []
    shard_re, step_re, co_re = re.compile(a.shard_job_re), re.compile(a.pytest_step_re), re.compile(a.checkout_step_re)
    weights = {}
    if a.durations:
        weights = json.loads(git(a.clone, "show", "origin/main:" + a.durations))
    med = statistics.median(weights.values()) if weights else 1.0
    git(a.clone, "fetch", "-q", "origin", "main")
    wt = os.path.abspath(os.path.join(a.out, "wt"))
    if not os.path.exists(wt):
        git(a.clone, "worktree", "add", "-q", "--detach", wt, "origin/main")
    for r in runs:
        if not r["prs"] and r["head_branch"] in by_branch:
            r["prs"] = [by_branch[r["head_branch"]]]
    todo = [r for r in runs if r["conclusion"] in ("success", "failure") and set(r["prs"]) & merged]
    print("window %s: %d merged PRs, %d PR runs, %d graded runs on merged PRs" % (
        since_iso, len(merged), len(runs), len(todo)), file=sys.stderr)
    shas = sorted({r["head_sha"] for r in todo})
    have = set()
    for i in range(0, len(shas), 50):
        chunk = shas[i:i + 50]
        subprocess.run(["git", "-C", a.clone, "fetch", "-q", "origin", *chunk], capture_output=True)
    for s in shas:
        if subprocess.run(["git", "-C", a.clone, "cat-file", "-e", s + "^{commit}"]).returncode == 0:
            have.add(s)
    sel_cache = {}
    for n, r in enumerate(todo):
        jobs = cached(os.path.join(a.out, "jobs", "%d.json" % r["id"]),
                      lambda: [j for page in gh("repos/%s/actions/runs/%d/jobs?per_page=100" % (a.repo, r["id"]),
                                                paginate=True) for j in page["jobs"]])
        shards = [j for j in jobs if shard_re.search(j["name"]) and j["conclusion"] in ("success", "failure")]
        fanin = [j for j in jobs if j["name"] == a.fanin_job and j.get("completed_at")]
        if not shards or not fanin or r["head_sha"] not in have:
            continue
        fanin = fanin[0]
        created = ts(r["created_at"])
        others_end = [ts(j["completed_at"]) for j in jobs if a.other_job_re and re.search(a.other_job_re, j["name"])
                      and j.get("completed_at") and j["conclusion"] in ("success", "failure")]
        last_need = max([ts(j["completed_at"]) for j in shards] + others_end)
        fanin_lat = ts(fanin["completed_at"]) - last_need  # queue + run of a no-op job on this pool, this run
        actual_wall = ts(fanin["completed_at"]) - created
        legs = []
        for j in shards:
            st = [s for s in j["steps"] if step_re.search(s["name"]) and s.get("started_at") and s.get("completed_at")]
            co = [s for s in j["steps"] if co_re.search(s["name"]) and s.get("started_at") and s.get("completed_at")]
            js, jc = ts(j["started_at"]), ts(j["completed_at"])
            if not st:
                continue
            ps, pc = ts(st[0]["started_at"]), ts(st[-1]["completed_at"])
            legs.append({"queue": js - created, "setup": ps - js, "pytest": pc - ps, "post": jc - pc,
                         "checkout": (ts(co[0]["completed_at"]) - ts(co[0]["started_at"])) if co else 0.0,
                         "dur": jc - js, "failed": j["conclusion"] == "failure", "id": j["id"]})
        if not legs:
            continue
        sha = r["head_sha"]
        sc = os.path.join(a.out, "sel-" + a.lazy_imports, sha + ".json")
        if sha not in sel_cache and os.path.exists(sc):
            with open(sc) as fh:
                sel_cache[sha] = tuple(json.load(fh))
        if sha not in sel_cache:
            mb = git(a.clone, "merge-base", sha, "origin/main", check=False).strip()
            if not mb:
                continue
            changed = git(a.clone, "diff", "--no-renames", "--name-only", "--diff-filter=d", mb, sha).split()
            deleted = git(a.clone, "diff", "--no-renames", "--name-only", "--diff-filter=D", mb, sha).split()
            if subprocess.run(["git", "-C", wt, "checkout", "-q", "--detach", "-f", sha],
                              capture_output=True).returncode != 0:
                git(wt, "clean", "-fdqx")
                git(wt, "checkout", "-q", "--detach", "-f", sha)
            t0 = time.time()
            graphs = {}
            sel, summ = select_tests.select(wt, changed, deleted, mb, tests_paths, a.source_root, aliases, graphs,
                                            a.lazy_imports)
            sel_secs = time.time() - t0
            full = graphs["head"].tests
            sel_cache[sha] = (sel, summ, full, sel_secs, changed, deleted)
            os.makedirs(os.path.dirname(sc), exist_ok=True)
            with open(sc, "w") as fh:
                json.dump(sel_cache[sha], fh)
        sel, summ, full, sel_secs, changed, deleted = sel_cache[sha]
        w = lambda fs: sum(weights.get(f, med) for f in fs)
        frac = (w(sel) / w(full)) if full else 1.0
        nsel = len(sel)
        L = a.max_legs if a.files_per_leg <= 0 else max(1, min(a.max_legs, math.ceil(nsel / a.files_per_leg)))
        total_pytest = sum(l["pytest"] for l in legs)
        leg_pytest = total_pytest * frac / L
        by_end = sorted(legs, key=lambda l: l["queue"] + l["setup"] + l["post"], reverse=True)[:L]
        checkout = statistics.median(l["checkout"] for l in legs)
        select_job = fanin_lat + checkout + sel_secs
        model_wall = select_job + max(l["queue"] + l["setup"] + leg_pytest + l["post"] for l in by_end) + fanin_lat
        if others_end:  # jobs selection does not shorten still bound the wall
            model_wall = max(model_wall, max(others_end) - created + fanin_lat)
        actual_min = sum(l["dur"] for l in legs) / 60
        model_min = (select_job + sum(l["setup"] + l["post"] + leg_pytest for l in by_end)) / 60
        failing, missed = [], []
        if any(l["failed"] for l in legs):
            for l in legs:
                if not l["failed"]:
                    continue
                log = os.path.join(a.out, "logs", "%d.txt" % l["id"])
                if not os.path.exists(log):
                    try:
                        text = gh("repos/%s/actions/jobs/%d/logs" % (a.repo, l["id"]), raw=True)
                    except RuntimeError:
                        text = ""
                    with open(log, "w") as fh:
                        fh.write(text)
                with open(log) as fh:
                    failing += FAIL_RE.findall(fh.read())
            failing = sorted(set(failing))
            missed = [f for f in failing if f not in set(sel)]
        rows.append({"run": r["id"], "pr": (set(r["prs"]) & merged).pop(), "sha": sha[:10],
                     "conclusion": r["conclusion"], "changed": len(changed), "deleted": len(deleted),
                     "selected": nsel, "total": len(full), "weight_frac": round(frac, 4), "legs": L,
                     "fallback": summ["fallback_own_package"], "actual_wall": round(actual_wall),
                     "model_wall": round(model_wall), "select_job": round(select_job, 1),
                     "actual_min": round(actual_min, 2), "model_min": round(model_min, 2),
                     "failing": failing, "missed": missed})
        if n % 25 == 0:
            print("%d/%d" % (n, len(todo)), file=sys.stderr)
    with open(os.path.join(a.out, "rows.json"), "w") as fh:
        json.dump(rows, fh, indent=1)
    if not rows:
        print("no graded runs")
        return 1
    aw, mw = [x["actual_wall"] for x in rows], [x["model_wall"] for x in rows]
    am, mm = sum(x["actual_min"] for x in rows), sum(x["model_min"] for x in rows)
    prs = {x["pr"] for x in rows}
    full_rows = sum(1 for x in rows if x["selected"] >= x["total"])
    red = [x for x in rows if x["conclusion"] == "failure"]
    missed = [x for x in red if x["missed"]]
    print("| Repo | PRs | PR runs | Test files selected, median (p90) of full | Full-suite runs | Duration share selected, median | "
          "Runner-min actual -> with selection | Wall p50 actual -> with | Wall p90 actual -> with | Red runs | Reds selection skips |")
    print("|---|---:|---:|---|---:|---:|---|---|---|---:|---:|")
    print("| %s | %d | %d | %d (%d) of %d | %d | %.0f%% | %.0f -> %.0f (%+.0f%%) | %.1f -> %.1f min | %.1f -> %.1f min | %d | %d |" % (
        a.repo.split("/")[1], len(prs), len(rows), statistics.median(x["selected"] for x in rows),
        pct([x["selected"] for x in rows], 0.9), statistics.median(x["total"] for x in rows), full_rows,
        100 * statistics.median(x["weight_frac"] for x in rows), am, mm, 100 * (mm - am) / am,
        pct(aw, .5) / 60, pct(mw, .5) / 60, pct(aw, .9) / 60, pct(mw, .9) / 60, len(red), len(missed)))
    for x in missed:
        print("  missed: run %d PR #%d failing %s, not selected %s" % (x["run"], x["pr"], x["failing"], x["missed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
