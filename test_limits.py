#!/usr/bin/env python3
"""Tests for limits.py. Run: python3 -m unittest -v"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limits


def cli(*argv):
    """Call main() with argv; return (exit_code, stdout, stderr)."""
    old = sys.argv
    sys.argv = ["limits.py", *argv]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = limits.main() or 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.argv = old
    return code, out.getvalue(), err.getvalue()


def write_config(home, products, launchers=None):
    cfg = {"products": products}
    if launchers is not None:
        cfg["launchers"] = launchers
    with open(os.path.join(home, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)


PRODUCTS = {
    "swe": {"models": [], "free_until": "2999-01-01"},
    "claude": {"models": ["haiku", "sonnet", "opus", "fable"]},
    "codex": {"models": ["astra", "sol", "luna", "terra"]},
    "grok": {"models": []},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self._env = {k: os.environ.get(k) for k in ("MODEL_ROUTER_HOME", "MODEL_ROUTER_LIMITS_FILE")}
        os.environ["MODEL_ROUTER_HOME"] = self.home
        os.environ.pop("MODEL_ROUTER_LIMITS_FILE", None)
        self.addCleanup(self._restore_env)
        write_config(self.home, dict(PRODUCTS))

    def _restore_env(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestKeys(Base):
    def cfg(self):
        return limits.load_config()

    def test_bare_model(self):
        self.assertEqual(limits.norm_key("opus", self.cfg()), "claude:opus")

    def test_canonical(self):
        self.assertEqual(limits.norm_key("codex:astra", self.cfg()), "codex:astra")

    def test_unlisted_model_known_product(self):
        self.assertEqual(limits.norm_key("codex:vega", self.cfg()), "codex:vega")

    def test_unknown_product(self):
        with self.assertRaises(SystemExit) as cm:
            limits.norm_key("palm:big", self.cfg())
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_bare(self):
        with self.assertRaises(SystemExit) as cm:
            limits.norm_key("vega", self.cfg())
        self.assertEqual(cm.exception.code, 2)

    def test_ambiguous_bare(self):
        write_config(self.home, {
            "a": {"models": ["shared"]},
            "b": {"models": ["shared"]},
        })
        with self.assertRaises(SystemExit) as cm:
            limits.norm_key("shared", limits.load_config())
        self.assertEqual(cm.exception.code, 2)


class TestRoundTrip(Base):
    def test_mark_clear_first(self):
        code, out, _ = cli("mark", "opus", "--for", "5h")
        self.assertEqual(code, 0)
        self.assertIn("claude:opus", out)
        state = limits.load()
        self.assertIn("claude:opus", state)

        # product-level record kills its models
        cli("mark", "claude", "--for", "5h")
        code, out, _ = cli("first", "opus", "swe")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "swe")

        code, _, _ = cli("clear", "claude")
        self.assertEqual(code, 0)
        code, out, _ = cli("first", "claude:opus", "swe")
        self.assertEqual(out.strip(), "swe")  # model record still there

        cli("clear", "claude:opus")
        code, out, _ = cli("first", "opus")
        self.assertEqual(out.strip(), "claude:opus")

    def test_first_all_dead(self):
        cli("mark", "swe", "--for", "1h")
        code, _, err = cli("first", "swe")
        self.assertEqual(code, 1)


class TestFreeUntil(Base):
    def test_past_kills(self):
        write_config(self.home, {"old": {"models": ["m1"], "free_until": "2000-01-01"}})
        cfg = limits.load_config()
        self.assertEqual(limits.dead_until("old", {}, cfg), float("inf"))
        self.assertEqual(limits.dead_until("old:m1", {}, cfg), float("inf"))

    def test_future_ok(self):
        write_config(self.home, {"new": {"models": ["m1"], "free_until": "2999-01-01"}})
        cfg = limits.load_config()
        self.assertIsNone(limits.dead_until("new", {}, cfg))
        self.assertIsNone(limits.dead_until("new:m1", {}, cfg))


class TestRun(Base):
    def test_dead_key_no_child(self):
        cli("mark", "swe", "--for", "1h")
        marker = os.path.join(self.home, "child-ran")
        code, _, _ = cli("run", "swe", "--", "touch", marker)
        self.assertEqual(code, 75)
        self.assertFalse(os.path.exists(marker))

    def test_child_limit_recorded(self):
        code, _, _ = cli("run", "swe", "--", "sh", "-c", "echo 'rate limit reached'")
        self.assertEqual(code, 75)
        self.assertIn("swe", limits.load())

    def test_exit_passthrough(self):
        code, _, _ = cli("run", "swe", "--", "sh", "-c", "exit 3")
        self.assertEqual(code, 3)

    def test_log_keeps_output(self):
        f = os.path.join(self.home, "child.log")
        code, _, _ = cli("run", "--log", f, "swe", "--", "sh", "-c", "echo report; echo err >&2; exit 3")
        self.assertEqual(code, 3)
        with open(f) as fh:
            self.assertEqual(sorted(fh.read().split()), ["err", "report"])


class TestScan(Base):
    def test_scan_file(self):
        f = os.path.join(self.home, "out.txt")
        with open(f, "w") as fh:
            fh.write("doing work\nquota exceeded\n")
        code, _, _ = cli("scan", "grok", "--file", f)
        self.assertEqual(code, 75)
        self.assertIn("grok", limits.load())

    def test_scan_clean(self):
        f = os.path.join(self.home, "out.txt")
        with open(f, "w") as fh:
            fh.write("all good\n")
        code, _, _ = cli("scan", "grok", "--file", f)
        self.assertEqual(code, 0)


class TestResetMs(Base):
    def test_hours_minutes(self):
        self.assertEqual(limits.reset_ms("try again in 3 hours 12 minutes"), 3 * 3600000 + 12 * 60000)

    def test_resets_at(self):
        ms = limits.reset_ms("resets at 3pm")
        self.assertIsNotNone(ms)
        self.assertTrue(0 < ms <= 1440 * 60000)


class TestConfig(Base):
    def test_bundled_fallback(self):
        os.remove(os.path.join(self.home, "config.json"))
        cfg = limits.load_config()
        self.assertEqual(cfg["path"], os.path.join(limits.skill_dir(), "examples", "config.json"))
        self.assertIn("claude", cfg["products"])

    def test_broken_config(self):
        with open(os.path.join(self.home, "config.json"), "w") as fh:
            fh.write("{nope")
        with self.assertRaises(SystemExit) as cm:
            limits.load_config()
        self.assertEqual(cm.exception.code, 2)

    def test_grade(self):
        now = __import__("time").time()
        state, _ = limits.grade({"used": 10, "resets": now + 86400 * 6, "span": 7 * 86400})
        self.assertEqual(state, "normal")
        state, _ = limits.grade({"used": 95, "resets": now + 86400, "span": 7 * 86400})
        self.assertEqual(state, "tight")
        state, _ = limits.grade({"used": 5, "resets": now + 86400, "span": 7 * 86400})
        self.assertEqual(state, "surplus")
        state, _ = limits.grade(None)
        self.assertEqual(state, "normal")


class TestWhereInit(Base):
    def test_where_shape(self):
        code, out, _ = cli("where")
        self.assertEqual(code, 0)
        rows = [line.split("\t") for line in out.strip().splitlines()]
        self.assertEqual(rows[0], ["home", self.home])
        self.assertEqual(rows[1][0], "config")
        self.assertEqual(rows[2][0], "roster")
        self.assertTrue(all(r[0] == "launcher" for r in rows[3:]))
        self.assertGreaterEqual(len(rows), 4)

    def test_init_copies_and_preserves(self):
        dst = os.path.join(self.home, "config.json")
        os.remove(dst)
        code, out, _ = cli("init")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(dst))
        self.assertIn("copied", out)
        with open(dst, "w") as fh:
            fh.write('{"products": {"mine": {}}}')
        cli("init")
        with open(dst) as fh:
            self.assertEqual(fh.read(), '{"products": {"mine": {}}}')


if __name__ == "__main__":
    unittest.main()
