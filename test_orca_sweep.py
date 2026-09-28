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
        self.assertIn("not a git repository", detail["reason"])

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
        klass, detail = orca_sweep.classify(wt("r::x", repo2), self.cwd)
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


if __name__ == "__main__":
    unittest.main()
