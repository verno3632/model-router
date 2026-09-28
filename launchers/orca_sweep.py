#!/usr/bin/env python3
"""Classify Orca worktrees so a manager can sweep finished children.

Reads `orca worktree ps --json` (or a file via --input) and classifies each
non-main worktree: folder, missing, self, live, dirty, unmerged, landed,
landed-with-ignored. Dry-run by default; --remove-landed / --remove-missing
delete via `orca worktree rm`. Python 3.9+, stdlib only.
"""
import argparse
import json
import os
import subprocess
import sys
import time

FOLDER_MARKER = "::workspace:"

# Ignored-path fragments that are ordinary caches, not worth flagging.
CACHE_IGNORED = (
    "node_modules", ".venv", "venv/", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".DS_Store", ".godot/", ".egg-info",
)
MAX_FLAGGED_IGNORED = 5


def run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def is_git_repo(path):
    return run(["git", "-C", path, "rev-parse", "--git-dir"]).returncode == 0


def base_branch(path):
    """Local `main`, else `master`, else origin/HEAD's target; None if none."""
    for name in ("main", "master"):
        if run(["git", "-C", path, "rev-parse", "--verify", "--quiet", name]).returncode == 0:
            return name
    r = run(["git", "-C", path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if r.returncode == 0:
        ref = r.stdout.strip()  # refs/remotes/origin/<name>
        return ref.rsplit("/", 1)[-1]
    return None


def porcelain(path, ignored=False):
    cmd = ["git", "-C", path, "status", "--porcelain"]
    if ignored:
        cmd.append("--ignored")
    r = run(cmd)
    return r.stdout.splitlines() if r.returncode == 0 else []


def dirty_counts(path):
    tracked = untracked = 0
    for line in porcelain(path):
        if line.startswith("??"):
            untracked += 1
        else:
            tracked += 1
    return tracked, untracked


def unmerged_commits(path, base):
    """`git cherry <base> HEAD` lines starting with `+` — patches not in base."""
    r = run(["git", "-C", path, "cherry", base, "HEAD"])
    if r.returncode != 0:
        return None
    return sum(1 for line in r.stdout.splitlines() if line.startswith("+"))


def flagged_ignored(path):
    """Ignored files that are not well-known caches (saves, builds, DBs)."""
    out = []
    for line in porcelain(path, ignored=True):
        if not line.startswith("!!"):
            continue
        name = line[3:].strip()
        if any(part in name for part in CACHE_IGNORED):
            continue
        out.append(name)
        if len(out) >= MAX_FLAGGED_IGNORED:
            break
    return out


def age_ms(last_output_at, now_ms):
    if not last_output_at:
        return None
    return max(0, now_ms - last_output_at)


def human_ms(ms):
    if ms is None:
        return "no output recorded"
    s = ms // 1000
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s ago"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m ago"
    return f"{s // 86400}d{s % 86400 // 3600}h ago"


def is_self(path, cwd):
    """True if path is cwd or an ancestor of it."""
    path = os.path.realpath(path)
    cwd = os.path.realpath(cwd)
    return cwd == path or cwd.startswith(path + os.sep)


def classify(wt, cwd, now_ms=None):
    """Return (class, detail dict) for one worktree entry."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    path = wt.get("path") or ""
    detail = {}

    if FOLDER_MARKER in (wt.get("worktreeId") or ""):
        return "folder", {"reason": "folder workspace (::workspace: in worktreeId)"}

    if not os.path.exists(path):
        return "missing", {"reason": "path does not exist; Orca registration only"}

    if not is_git_repo(path):
        return "folder", {"reason": "path is not a git repository"}

    if is_self(path, cwd):
        return "self", {"reason": "this sweep is running inside it"}

    live = wt.get("liveTerminalCount") or 0
    if live > 0:
        states = [a.get("state", "?") for a in wt.get("agents") or []]
        return "live", {
            "liveTerminalCount": live,
            "agentStates": states,
            "lastOutputAge": human_ms(age_ms(wt.get("lastOutputAt"), now_ms)),
        }

    tracked, untracked = dirty_counts(path)
    if tracked + untracked > 0:
        return "dirty", {"trackedChanges": tracked, "untrackedFiles": untracked}

    base = base_branch(path)
    if base is None:
        return "unmerged", {"reason": "no base branch (main/master/origin HEAD) found"}
    n = unmerged_commits(path, base)
    if n is None:
        return "unmerged", {"base": base, "reason": "git cherry failed"}
    if n > 0:
        return "unmerged", {"base": base, "commits": n}

    flagged = flagged_ignored(path)
    klass = "landed-with-ignored" if flagged else "landed"
    detail = {"base": base}
    if flagged:
        detail["ignoredFiles"] = flagged
    return klass, detail


def remove_command(wt):
    """The `orca worktree rm` invocation for a worktree (never --force)."""
    return ["orca", "worktree", "rm", "--worktree", f"id:{wt['worktreeId']}", "--json"]


def load_worktrees(args):
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        r = run(["orca", "worktree", "ps", "--json"])
        if r.returncode != 0:
            sys.exit(f"orca worktree ps failed: {r.stderr.strip() or r.stdout.strip()}")
        data = json.loads(r.stdout)
    if not data.get("ok"):
        sys.exit(f"orca worktree ps returned not-ok: {json.dumps(data)[:300]}")
    return data.get("result", {}).get("worktrees", [])


def report_table(rows, counts):
    for klass in ("folder", "missing", "self", "live", "dirty", "unmerged",
                  "landed-with-ignored", "landed", "main"):
        group = [r for r in rows if r["class"] == klass]
        if not group:
            continue
        print(f"\n== {klass} ({len(group)}) ==")
        for r in group:
            print(f"  {r['repo']:<24} {r['path']}")
            detail = r["detail"]
            if detail:
                bits = "; ".join(f"{k}={v}" for k, v in detail.items())
                print(f"      {bits}")
    print("\ncounts:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def main():
    p = argparse.ArgumentParser(description="Classify Orca worktrees for sweeping.")
    p.add_argument("--input", help="read `orca worktree ps --json` output from a file")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument("--remove-landed", action="store_true",
                   help="remove `landed` worktrees via `orca worktree rm`")
    p.add_argument("--remove-missing", action="store_true",
                   help="remove `missing` registrations via `orca worktree rm`")
    args = p.parse_args()

    cwd = os.getcwd()
    rows = []
    for wt in load_worktrees(args):
        if wt.get("isMainWorktree"):
            rows.append({"class": "main", "worktreeId": wt.get("worktreeId"),
                         "repo": wt.get("repo"), "path": wt.get("path"), "detail": {}})
            continue
        klass, detail = classify(wt, cwd)
        rows.append({"class": klass, "worktreeId": wt.get("worktreeId"),
                     "repo": wt.get("repo"), "path": wt.get("path"), "detail": detail})

    counts = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1

    removals = []
    if args.remove_landed:
        removals += [r for r in rows if r["class"] == "landed"]
    if args.remove_missing:
        removals += [r for r in rows if r["class"] == "missing"]

    if args.json:
        out = {"counts": counts, "worktrees": rows}
        if removals:
            out["removals"] = []
        print(json.dumps(out, indent=2))
    else:
        report_table(rows, counts)

    for r in removals:
        cmd = remove_command({"worktreeId": r["worktreeId"]})
        res = run(cmd)
        try:
            ok = res.returncode == 0 and json.loads(res.stdout).get("ok")
        except json.JSONDecodeError:
            ok = False
        msg = res.stdout.strip() or res.stderr.strip()
        print(f"{'removed' if ok else 'FAILED '} {r['path']}  {msg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
