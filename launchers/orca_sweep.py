#!/usr/bin/env python3
"""Classify Orca worktrees so a manager can sweep finished children.

Reads `orca worktree ps --json` (or a file via --input) and classifies each
non-main worktree: folder, missing, self, live, dirty, unmerged, protected,
unknown, landed, landed-with-ignored. Dry-run by default; --remove-landed /
--remove-missing delete via `orca worktree rm`. Python 3.9+, stdlib only.
"""
import argparse
import json
import os
import subprocess
import sys
import time

FOLDER_MARKER = "::workspace:"

# Ignored-path components that are ordinary caches, not worth flagging.
CACHE_COMPONENTS = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".DS_Store", ".godot",
})
MAX_FLAGGED_IGNORED = 5


def run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def is_git_worktree_root(path):
    """True only if `path` itself is the root of a git working tree."""
    r = run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    if r.returncode != 0:
        return False
    top = r.stdout.strip()
    return bool(top) and os.path.realpath(top) == os.path.realpath(path)


def is_linked_worktree(path):
    """True if `path` is a linked worktree (git-dir differs from common-dir).

    A standalone repo's root shares the main .git dir, so it must never be
    classified as a removable worktree."""
    r1 = run(["git", "-C", path, "rev-parse", "--git-dir"])
    r2 = run(["git", "-C", path, "rev-parse", "--git-common-dir"])
    if r1.returncode != 0 or r2.returncode != 0:
        return False
    d, cd = r1.stdout.strip(), r2.stdout.strip()
    if not d or not cd:
        return False
    if not os.path.isabs(d):
        d = os.path.join(path, d)
    if not os.path.isabs(cd):
        cd = os.path.join(path, cd)
    return os.path.realpath(d) != os.path.realpath(cd)


def base_branch(path):
    """Local `main`, else `master`, else origin/HEAD's target as `origin/<name>`."""
    for name in ("main", "master"):
        if run(["git", "-C", path, "rev-parse", "--verify", "--quiet", name]).returncode == 0:
            return name
    r = run(["git", "-C", path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if r.returncode == 0:
        ref = r.stdout.strip()  # refs/remotes/origin/<name>
        if ref.startswith("refs/remotes/"):
            return ref[len("refs/remotes/"):]
    return None


def base_branch_name(base):
    """The branch-name part of a base ref (`origin/rel/2.0` -> `rel/2.0`)."""
    if base.startswith("origin/"):
        return base[len("origin/"):]
    return base


def head_branch(path):
    """Checked-out branch name, or None for detached/unborn HEAD."""
    r = run(["git", "-C", path, "symbolic-ref", "--quiet", "--short", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else None


def porcelain(path, ignored=False, untracked_all=False, pathspec=None):
    """status --porcelain lines, or None if git status fails."""
    cmd = ["git", "-c", "core.quotePath=false", "-C", path, "status",
           "--porcelain", "--ignore-submodules=none"]
    if ignored:
        if untracked_all:
            cmd.append("--untracked-files=all")
        else:
            # -uall would enumerate every file inside ignored dirs; skip that.
            cmd.append("--untracked-files=normal")
        cmd.append("--ignored")
    else:
        cmd.append("--untracked-files=all")
    if pathspec:
        cmd += ["--", pathspec]
    r = run(cmd)
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def dirty_counts(path):
    """(tracked, untracked) change counts, or None if status fails."""
    lines = porcelain(path)
    if lines is None:
        return None
    tracked = untracked = 0
    for line in lines:
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


def is_cache_path(name):
    """True if any path component is a well-known cache name."""
    for comp in name.strip("/").split("/"):
        if comp in CACHE_COMPONENTS or comp.endswith(".egg-info"):
            return True
    return False


def flagged_ignored(path):
    """Ignored files that are not well-known caches, or None on failure.

    The status uses -unormal, so ignored dirs come back as `dir/` without
    their contents. A flagged dir is expanded with a scoped -uall status so
    a dir holding only caches (e.g. libs/node_modules/) is not flagged."""
    lines = porcelain(path, ignored=True)
    if lines is None:
        return None
    out = []
    for line in lines:
        if not line.startswith("!!"):
            continue
        name = line[3:].strip().strip('"')
        if is_cache_path(name):
            continue
        if name.endswith("/"):
            inner = porcelain(path, ignored=True, untracked_all=True,
                              pathspec=name)
            if inner is None:
                return None
            flagged = [l[3:].strip().strip('"') for l in inner
                       if l.startswith("!!")
                       and not is_cache_path(l[3:].strip().strip('"'))]
            if not flagged:
                continue
            name = flagged[0]
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
    """True if path is cwd or an ancestor of it (case-insensitive)."""
    path = os.path.realpath(path).lower()
    cwd = os.path.realpath(cwd).lower()
    return cwd == path or cwd.startswith(path + os.sep)


def classify(wt, cwd, now_ms=None):
    """Return (class, detail dict) for one worktree entry."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    path = wt.get("path") or ""
    detail = {}

    if FOLDER_MARKER in (wt.get("worktreeId") or ""):
        return "folder", {"reason": "folder workspace (::workspace: in worktreeId)"}

    if not path or not os.path.isabs(path):
        return "unknown", {"reason": "path is empty or not absolute"}

    try:
        os.lstat(path)
    except FileNotFoundError:
        parent = os.path.dirname(path.rstrip(os.sep)) or os.sep
        if os.path.isdir(parent):
            return "missing", {"reason": "path does not exist; Orca registration only"}
        return "unknown", {"reason": "path and its parent are missing "
                                     "(unmounted volume?)"}
    except OSError as e:
        return "unknown", {"reason": f"cannot stat path ({e}); "
                                     "permission error?"}

    if not is_git_worktree_root(path):
        return "folder", {"reason": "path is not the root of a git worktree"}

    if not is_linked_worktree(path):
        return "folder", {"reason": "standalone repo root, not a linked worktree"}

    if is_self(path, cwd):
        return "self", {"reason": "this sweep is running inside it"}

    live = wt.get("liveTerminalCount")
    if live is None:
        return "unknown", {"reason": "liveTerminalCount missing from `orca worktree ps` entry"}
    if live > 0:
        states = [a.get("state", "?") for a in wt.get("agents") or []]
        return "live", {
            "liveTerminalCount": live,
            "agentStates": states,
            "lastOutputAge": human_ms(age_ms(wt.get("lastOutputAt"), now_ms)),
        }

    counts = dirty_counts(path)
    if counts is None:
        return "unknown", {"reason": "git status failed; cannot verify clean state"}
    tracked, untracked = counts
    if tracked + untracked > 0:
        return "dirty", {"trackedChanges": tracked, "untrackedFiles": untracked}

    base = base_branch(path)
    if base is None:
        return "unmerged", {"reason": "no base branch (main/master/origin HEAD) found"}

    head = head_branch(path)
    if head is not None and head == base_branch_name(base):
        return "protected", {"base": base, "branch": head,
                             "reason": "HEAD is the base branch itself"}

    n = unmerged_commits(path, base)
    if n is None:
        return "unmerged", {"base": base, "reason": "git cherry failed"}
    if n > 0:
        return "unmerged", {"base": base, "commits": n}

    flagged = flagged_ignored(path)
    if flagged is None:
        return "unknown", {"reason": "git status --ignored failed"}
    detail = {"base": base}
    if flagged:
        detail["ignoredFiles"] = flagged
        return "landed-with-ignored", detail
    return "landed", detail


def remove_command(wt):
    """The `orca worktree rm` invocation for a worktree (never --force)."""
    return ["orca", "worktree", "rm", "--worktree", f"id:{wt['worktreeId']}", "--json"]


def select_removals(rows, remove_landed=False, remove_missing=False):
    """Rows eligible for deletion. Only `landed` / `missing` ever qualify."""
    out = []
    for r in rows:
        if r["class"] == "landed" and remove_landed:
            out.append(r)
        elif r["class"] == "missing" and remove_missing:
            out.append(r)
    return out


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
                  "protected", "unknown", "landed-with-ignored", "landed", "main"):
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


def fresh_worktree(worktree_id):
    """Re-fetch `orca worktree ps --json` and return the entry, or None."""
    r = run(["orca", "worktree", "ps", "--json"])
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if not data.get("ok"):
        return None
    for w in data.get("result", {}).get("worktrees", []):
        if w.get("worktreeId") == worktree_id:
            return w
    return None


def perform_removals(removals, wt_by_id, cwd):
    """Run `orca worktree rm` for each row, re-classifying right before.

    The fresh `orca worktree ps` entry is re-classified so a terminal that
    went live since the first scan is caught. Entries that fail to re-fetch
    or no longer classify the same way are skipped, not failed.
    Returns (results list, failed count)."""
    results = []
    failed = 0
    for r in removals:
        entry = {"worktreeId": r["worktreeId"], "path": r["path"]}
        wt = fresh_worktree(r["worktreeId"])
        if wt is None:
            entry.update(ok=None, status="skipped",
                         reason="could not re-fetch `orca worktree ps` entry")
            results.append(entry)
            continue
        klass, detail = classify(wt, cwd)
        if klass != r["class"]:
            entry.update(ok=None, status="skipped",
                         reason=f"re-classified as {klass}: "
                                f"{detail.get('reason', detail)}")
            results.append(entry)
            continue
        res = run(remove_command(wt))
        try:
            ok = res.returncode == 0 and json.loads(res.stdout).get("ok")
        except json.JSONDecodeError:
            ok = False
        entry.update(ok=ok, status="removed" if ok else "failed",
                     output=(res.stdout.strip() or res.stderr.strip()))
        if not ok:
            failed += 1
        results.append(entry)
    return results, failed


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
    wt_by_id = {}
    for wt in load_worktrees(args):
        row = {"worktreeId": wt.get("worktreeId"), "repo": wt.get("repo"),
               "path": wt.get("path"), "detail": {}}
        if wt.get("isMainWorktree"):
            row["class"] = "main"
        else:
            row["class"], row["detail"] = classify(wt, cwd)
            wt_by_id[row["worktreeId"]] = wt
        rows.append(row)

    counts = {}
    for r in rows:
        counts[r["class"]] = counts.get(r["class"], 0) + 1

    removals = select_removals(rows, args.remove_landed, args.remove_missing)
    results, failed = perform_removals(removals, wt_by_id, cwd) if removals else ([], 0)

    if args.json:
        out = {"counts": counts, "worktrees": rows}
        if args.remove_landed or args.remove_missing:
            out["removals"] = results
        print(json.dumps(out, indent=2))
    else:
        report_table(rows, counts)
        for e in results:
            print(f"{e['status']:<8} {e['path']}  {e.get('output') or e.get('reason', '')}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
