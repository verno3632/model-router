#!/usr/bin/env python3
"""Tests for launchers/orca_sweep.py. Run: python3 -m unittest -v"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "launchers"))
import orca_sweep


def git(path, *args):
    r = subprocess.run(["git", "-C", path, *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def init_repo(path, branch="main"):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-b", branch, path],
                   capture_output=True, check=True)
    git(path, "config", "user.name", "Sweep Test")
    git(path, "config", "user.email", "sweep@example.com")
    git(path, "commit", "--allow-empty", "-m", "init")


def commit_file(repo, name, content, msg="change"):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
        fh.write(content)
    git(repo, "add", name)
    git(repo, "commit", "-m", msg)


def wt(wtid, path, **kw):
    base = {"worktreeId": wtid, "repo": "r", "path": path,
            "isMainWorktree": False, "liveTerminalCount": 0,
            "agents": [], "lastOutputAt": None,
            "branch": "refs/heads/b"}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        init_repo(self.repo)
        self.cwd = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(self.cwd)

    def add_worktree(self, name, branch):
        path = os.path.join(self.tmp.name, name)
        git(self.repo, "worktree", "add", path, "-b", branch)
        return path


class TestClassify(Base):
    def test_folder_marker(self):
        path = os.path.join(self.tmp.name, "plain")
        os.makedirs(path)
        self.assertEqual(
            orca_sweep.classify(wt("r::workspace:x", path), self.cwd)[0],
            "folder")

    def test_folder_not_git(self):
        path = os.path.join(self.tmp.name, "plain")
        os.makedirs(path)
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "folder")
        self.assertIn("not the root of a git worktree", detail["reason"])

    def test_missing(self):
        klass, detail = orca_sweep.classify(
            wt("r::gone", os.path.join(self.tmp.name, "gone")), self.cwd)
        self.assertEqual(klass, "missing")

    def test_self(self):
        path = self.add_worktree("self-wt", "self-br")
        klass, _ = orca_sweep.classify(wt("r::" + path, path), path)
        self.assertEqual(klass, "self")

    def test_self_child_cwd(self):
        path = self.add_worktree("self-wt", "self-br")
        klass, _ = orca_sweep.classify(
            wt("r::" + path, path), os.path.join(path, "sub", "dir"))
        self.assertEqual(klass, "self")

    def test_live(self):
        path = self.add_worktree("live-wt", "live-br")
        klass, detail = orca_sweep.classify(wt(
            "r::" + path, path, liveTerminalCount=2,
            agents=[{"state": "working"}, {"state": "done"}],
            lastOutputAt=1), self.cwd, now_ms=1 + 5 * 60 * 1000)
        self.assertEqual(klass, "live")
        self.assertEqual(detail["agentStates"], ["working", "done"])
        self.assertEqual(detail["lastOutputAge"], "5m00s ago")

    def test_dirty_tracked(self):
        commit_file(self.repo, "f.txt", "a")
        path = self.add_worktree("dirty-wt", "dirty-br")
        # one modified tracked file and one untracked file
        with open(os.path.join(path, "f.txt"), "a") as fh:
            fh.write("x")
        with open(os.path.join(path, "new.txt"), "w") as fh:
            fh.write("u")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "dirty")
        self.assertEqual(detail["trackedChanges"], 1)
        self.assertEqual(detail["untrackedFiles"], 1)

    def test_unmerged(self):
        path = self.add_worktree("work-wt", "work-br")
        commit_file(path, "feat.txt", "feature")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "unmerged")
        self.assertEqual(detail["commits"], 1)

    def test_landed(self):
        # plain merged branch: commit exists in main
        path = self.add_worktree("done-wt", "done-br")
        commit_file(path, "feat.txt", "feature")
        git(self.repo, "merge", "done-br")
        klass, _ = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed")

    def test_landed_squash_merge(self):
        # same diff re-committed on main: git cherry reports it as `-`
        path = self.add_worktree("sq-wt", "sq-br")
        commit_file(path, "feat.txt", "feature", msg="wip")
        commit_file(self.repo, "feat.txt", "feature", msg="feat (#1)")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed")
        self.assertEqual(detail["base"], "main")

    def test_landed_with_ignored(self):
        commit_file(self.repo, ".gitignore", "*.dat\n.DS_Store\n")
        path = self.add_worktree("ign-wt", "ign-br")
        with open(os.path.join(path, "save.dat"), "w") as fh:
            fh.write("s")
        with open(os.path.join(path, ".DS_Store"), "w") as fh:
            fh.write("c")  # cache noise must not flag
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed-with-ignored")
        self.assertEqual(detail["ignoredFiles"], ["save.dat"])

    def test_no_base_is_unmerged(self):
        repo2 = os.path.join(self.tmp.name, "repo2")
        init_repo(repo2, branch="trunk")  # no main/master/origin HEAD
        path = os.path.join(self.tmp.name, "wt2")
        git(repo2, "worktree", "add", path, "-b", "b")
        klass, detail = orca_sweep.classify(wt("r::x", path), self.cwd)
        self.assertEqual(klass, "unmerged")
        self.assertIn("no base branch", detail["reason"])

    def test_base_master_fallback(self):
        repo2 = os.path.join(self.tmp.name, "repo2")
        init_repo(repo2, branch="master")
        path = os.path.join(self.tmp.name, "wt2")
        git(repo2, "worktree", "add", path, "-b", "done-br")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed")
        self.assertEqual(detail["base"], "master")

    def test_protected_head_is_base(self):
        # worktree checked out on `main` itself: cherry vs main is always empty
        path = os.path.join(self.tmp.name, "main-wt")
        git(self.repo, "worktree", "add", "-f", path, "main")
        commit_file(path, "wip.txt", "unpushed work")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "protected")
        self.assertEqual(detail["branch"], "main")

    def test_folder_repo_subdirectory(self):
        # a plain dir inside a repo resolves to the parent repo — still folder
        sub = os.path.join(self.repo, "data")
        os.makedirs(sub)
        klass, detail = orca_sweep.classify(wt("r::" + sub, sub), self.cwd)
        self.assertEqual(klass, "folder")

    def test_status_failure_is_unknown(self):
        path = self.add_worktree("broken-wt", "broken-br")
        gitdir = git(path, "rev-parse", "--absolute-git-dir").strip()
        with open(os.path.join(gitdir, "index"), "w") as fh:
            fh.write("garbage")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "unknown")
        self.assertIn("git status", detail["reason"])

    def test_untracked_despite_config(self):
        # status.showUntrackedFiles=no must not hide untracked work
        git(self.repo, "config", "status.showUntrackedFiles", "no")
        path = self.add_worktree("conf-wt", "conf-br")
        with open(os.path.join(path, "new-work.py"), "w") as fh:
            fh.write("x")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "dirty")
        self.assertEqual(detail["untrackedFiles"], 1)

    def test_submodule_change_despite_ignore(self):
        # .gitmodules ignore=all must not hide submodule-local commits
        sub = os.path.join(self.tmp.name, "sub")
        init_repo(sub)
        git(self.repo, "-c", "protocol.file.allow=always",
            "submodule", "add", sub, "sub")
        git(self.repo, "config", "-f", ".gitmodules", "submodule.sub.ignore", "all")
        git(self.repo, "add", ".gitmodules")
        git(self.repo, "commit", "-m", "sub")
        path = self.add_worktree("submod-wt", "submod-br")
        subprocess.run(["git", "-C", path, "-c", "protocol.file.allow=always",
                        "submodule", "update", "--init"],
                       capture_output=True, check=True)
        git(os.path.join(path, "sub"), "commit", "--allow-empty", "-m", "local")
        klass, _ = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "dirty")

    def test_missing_parent_absent_is_unknown(self):
        gone = os.path.join(self.tmp.name, "vol", "wt")  # vol/ doesn't exist
        klass, detail = orca_sweep.classify(wt("r::gone", gone), self.cwd)
        self.assertEqual(klass, "unknown")

    def test_live_count_missing_key_is_unknown(self):
        path = self.add_worktree("nolc-wt", "nolc-br")
        w = wt("r::" + path, path)
        del w["liveTerminalCount"]
        klass, detail = orca_sweep.classify(w, self.cwd)
        self.assertEqual(klass, "unknown")

    def test_live_no_last_output(self):
        path = self.add_worktree("live-wt", "live-br")
        klass, detail = orca_sweep.classify(wt(
            "r::" + path, path, liveTerminalCount=1, lastOutputAt=None), self.cwd)
        self.assertEqual(klass, "live")
        self.assertEqual(detail["lastOutputAge"], "no output recorded")

    def test_base_origin_head_fallback(self):
        # no local main/master: base is the remote-tracking ref itself
        src = os.path.join(self.tmp.name, "src")
        init_repo(src, branch="rel/2.0")
        repo2 = os.path.join(self.tmp.name, "repo2")
        init_repo(repo2, branch="zz")
        git(repo2, "remote", "add", "origin", src)
        git(repo2, "fetch", "origin")
        git(repo2, "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/rel/2.0")
        path = os.path.join(self.tmp.name, "wt2")
        git(repo2, "worktree", "add", path, "-b", "done-br", "origin/rel/2.0")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed")
        self.assertEqual(detail["base"], "origin/rel/2.0")

    def test_cherry_failure_returns_none(self):
        path = self.add_worktree("cf-wt", "cf-br")
        self.assertIsNone(orca_sweep.unmerged_commits(path, "nonexistent-ref"))

    def test_classify_cherry_failure_is_unmerged(self):
        path = self.add_worktree("cf-wt", "cf-br")
        with mock.patch.object(orca_sweep, "unmerged_commits", return_value=None):
            klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "unmerged")
        self.assertIn("cherry", detail["reason"])

    def test_ignored_cache_component_match(self):
        commit_file(self.repo, ".gitignore",
                    "*.sav\nmyvenv/\nnode_modules/\nsub_cache.egg-info/\n")
        path = self.add_worktree("ign-wt", "ign-br")
        for rel in ("keep.sav", "myvenv/x.py", "saves/node_modules_old.sav",
                    "libs/node_modules/mod.js", "sub_cache.egg-info/P"):
            p = os.path.join(path, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("x")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed-with-ignored")
        self.assertEqual(sorted(detail["ignoredFiles"]),
                         ["keep.sav", "myvenv/x.py",
                          "saves/node_modules_old.sav"])

    def test_ignored_japanese_name_unescaped(self):
        commit_file(self.repo, ".gitignore", "*.sav\n")
        path = self.add_worktree("ign-wt", "ign-br")
        with open(os.path.join(path, "セーブ データ.sav"), "w") as fh:
            fh.write("x")
        klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "landed-with-ignored")
        self.assertEqual(detail["ignoredFiles"], ["セーブ データ.sav"])

    def test_is_self_case_insensitive(self):
        path = self.add_worktree("self-wt", "self-br")
        self.assertTrue(orca_sweep.is_self(path.upper(), path))

    def test_empty_path_is_unknown(self):
        klass, _ = orca_sweep.classify(wt("r::x", ""), self.cwd)
        self.assertEqual(klass, "unknown")

    def test_none_path_is_unknown(self):
        w = wt("r::x", None)
        w["path"] = None
        klass, _ = orca_sweep.classify(w, self.cwd)
        self.assertEqual(klass, "unknown")

    def test_relative_path_is_unknown(self):
        klass, _ = orca_sweep.classify(wt("r::x", "some/rel/path"), self.cwd)
        self.assertEqual(klass, "unknown")

    def test_stat_permission_error_is_unknown(self):
        # unreadable parent: lstat raises PermissionError, not
        # FileNotFoundError — must not fall into `missing`
        with mock.patch.object(orca_sweep.os, "lstat",
                               side_effect=PermissionError("denied")):
            klass, detail = orca_sweep.classify(
                wt("r::x", os.path.join(self.tmp.name, "locked")), self.cwd)
        self.assertEqual(klass, "unknown")
        self.assertIn("cannot stat path", detail["reason"])

    def test_standalone_repo_root_is_folder(self):
        # a non-linked repo root (git-dir == git-common-dir) must never be
        # a removal candidate even when it looks fully merged
        klass, detail = orca_sweep.classify(wt("r::x", self.repo), self.cwd)
        self.assertEqual(klass, "folder")
        self.assertIn("standalone repo root", detail["reason"])

    def test_ignored_status_failure_is_unknown(self):
        path = self.add_worktree("ign-wt", "ign-br")
        real = orca_sweep.porcelain

        def fake(p, ignored=False):
            if ignored:
                return None
            return real(p, ignored)

        with mock.patch.object(orca_sweep, "porcelain", fake):
            klass, detail = orca_sweep.classify(wt("r::" + path, path), self.cwd)
        self.assertEqual(klass, "unknown")
        self.assertIn("--ignored", detail["reason"])


class TestRemoveCommand(unittest.TestCase):
    def test_argv(self):
        cmd = orca_sweep.remove_command({"worktreeId": "abc::/p"})
        self.assertEqual(cmd, ["orca", "worktree", "rm", "--worktree",
                               "id:abc::/p", "--json"])
        self.assertNotIn("--force", cmd)


class TestMain(Base):
    def cli(self, *argv):
        old = sys.argv
        sys.argv = ["orca_sweep.py", *argv]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = orca_sweep.main() or 0
        finally:
            sys.argv = old
        return code, out.getvalue()

    def write_input(self, worktrees):
        f = os.path.join(self.tmp.name, "ps.json")
        with open(f, "w") as fh:
            json.dump({"ok": True, "result": {"worktrees": worktrees}}, fh)
        return f

    def test_json_output_and_counts(self):
        gone = os.path.join(self.tmp.name, "gone")
        f = self.write_input([
            wt("r::main", self.repo, isMainWorktree=True),
            wt("r::gone", gone),
        ])
        code, out = self.cli("--input", f, "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["counts"], {"main": 1, "missing": 1})
        self.assertEqual(data["worktrees"][1]["class"], "missing")

    def test_table_runs(self):
        f = self.write_input([wt("r::gone", os.path.join(self.tmp.name, "gone"))])
        code, out = self.cli("--input", f)
        self.assertEqual(code, 0)
        self.assertIn("missing", out)
        self.assertIn("counts:", out)


class TestSelectRemovals(unittest.TestCase):
    def rows(self, *classes):
        return [{"class": c, "worktreeId": f"id{i}", "path": f"/p{i}",
                 "repo": "r", "detail": {}} for i, c in enumerate(classes)]

    def test_only_landed_and_missing_selected(self):
        classes = ["folder", "missing", "self", "live", "dirty", "unmerged",
                   "protected", "unknown", "landed-with-ignored", "landed",
                   "main"]
        rows = self.rows(*classes)
        sel = orca_sweep.select_removals(rows, remove_landed=True,
                                       remove_missing=True)
        self.assertEqual([r["class"] for r in sel], ["missing", "landed"])

    def test_flags_gate_selection(self):
        rows = self.rows("missing", "landed")
        self.assertEqual(orca_sweep.select_removals(rows), [])
        self.assertEqual(len(orca_sweep.select_removals(
            rows, remove_landed=True)), 1)
        self.assertEqual(len(orca_sweep.select_removals(
            rows, remove_missing=True)), 1)


class TestRemovalFlow(Base):
    def setUp(self):
        super().setUp()
        self.orca_calls = []
        self.ps_worktrees = []
        real_run = orca_sweep.run

        def fake(cmd, check=False):
            if cmd and cmd[0] == "orca":
                self.orca_calls.append(cmd)
                r = subprocess.CompletedProcess(cmd, 0)
                if "ps" in cmd:
                    r.stdout = json.dumps(
                        {"ok": True,
                         "result": {"worktrees": self.ps_worktrees}})
                else:
                    r.stdout = json.dumps({"ok": True})
                r.stderr = ""
                return r
            return real_run(cmd)
        patcher = mock.patch.object(orca_sweep, "run", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *argv):
        old = sys.argv
        sys.argv = ["orca_sweep.py", *argv]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = orca_sweep.main()
        finally:
            sys.argv = old
        return code, out.getvalue()

    def write_input(self, worktrees):
        self.ps_worktrees = worktrees
        f = os.path.join(self.tmp.name, "ps.json")
        with open(f, "w") as fh:
            json.dump({"ok": True, "result": {"worktrees": worktrees}}, fh)
        return f

    def landed_worktree(self):
        path = self.add_worktree("done-wt", "done-br")
        commit_file(path, "feat.txt", "f")
        git(self.repo, "merge", "done-br")
        return path

    def test_json_remove_landed(self):
        path = self.landed_worktree()
        f = self.write_input([wt("r::" + path, path)])
        code, out = self.cli("--input", f, "--json", "--remove-landed")
        self.assertEqual(code, 0)
        data = json.loads(out)  # must be pure JSON, no plaintext mixed in
        self.assertEqual(len(data["removals"]), 1)
        self.assertEqual(data["removals"][0]["status"], "removed")
        self.assertEqual(sum("rm" in c for c in self.orca_calls), 1)

    def test_json_remove_failure_exit_code(self):
        real_run = orca_sweep.run
        def fake(cmd, check=False):
            if cmd and cmd[0] == "orca":
                self.orca_calls.append(cmd)
                if "ps" in cmd:
                    r = subprocess.CompletedProcess(cmd, 0)
                    r.stdout = json.dumps(
                        {"ok": True,
                         "result": {"worktrees": self.ps_worktrees}})
                    r.stderr = ""
                    return r
                r = subprocess.CompletedProcess(cmd, 1)
                r.stdout, r.stderr = "", "boom"
                return r
            return real_run(cmd)
        path = self.landed_worktree()
        f = self.write_input([wt("r::" + path, path)])
        with mock.patch.object(orca_sweep, "run", fake):
            code, out = self.cli("--input", f, "--json", "--remove-landed")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["removals"][0]["status"], "failed")

    def test_reclassify_skips_newly_dirty(self):
        path = self.landed_worktree()
        f = self.write_input([wt("r::" + path, path)])
        real_classify = orca_sweep.classify
        calls = {"n": 0}

        def flipping(w, cwd, now_ms=None):
            calls["n"] += 1
            if calls["n"] == 2:  # the re-check before removal
                with open(os.path.join(path, "dirty.txt"), "w") as fh:
                    fh.write("x")
            return real_classify(w, cwd, now_ms)

        with mock.patch.object(orca_sweep, "classify", flipping):
            code, out = self.cli("--input", f, "--json", "--remove-landed")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["removals"][0]["status"], "skipped")
        self.assertFalse(any("rm" in c for c in self.orca_calls))

    def test_refetch_failure_skips_removal(self):
        path = self.landed_worktree()
        f = self.write_input([wt("r::" + path, path)])
        real_fresh = orca_sweep.fresh_worktree
        with mock.patch.object(orca_sweep, "fresh_worktree", return_value=None):
            code, out = self.cli("--input", f, "--json", "--remove-landed")
        self.assertEqual(code, 0)
        rem = json.loads(out)["removals"][0]
        self.assertEqual(rem["status"], "skipped")
        self.assertFalse(any("rm" in c for c in self.orca_calls))

    def test_refetch_catches_newly_live(self):
        path = self.landed_worktree()
        f = self.write_input([wt("r::" + path, path)])
        # terminal went live between the initial ps and the removal pass
        self.ps_worktrees = [wt("r::" + path, path, liveTerminalCount=1)]
        code, out = self.cli("--input", f, "--json", "--remove-landed")
        self.assertEqual(code, 0)
        rem = json.loads(out)["removals"][0]
        self.assertEqual(rem["status"], "skipped")
        self.assertIn("live", rem["reason"])
        self.assertFalse(any("rm" in c for c in self.orca_calls))

    def test_table_remove_landed(self):
        path = self.landed_worktree()
        f = self.write_input([wt("r::" + path, path)])
        code, out = self.cli("--input", f, "--remove-landed")
        self.assertEqual(code, 0)
        self.assertIn("removed", out)
        self.assertEqual(sum("rm" in c for c in self.orca_calls), 1)


if __name__ == "__main__":
    unittest.main()
