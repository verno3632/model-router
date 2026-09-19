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


class TestRealLimitWording(Base):
    def test_codex_limit_detected(self):
        lines = [
            "■ You’ve hit your usage limit. Visit https://chatgpt.com/codex/settings/usage",
            "to purchase more credits or try again at Sep 26th, 2026 5:13 PM.",
            "Goal hit usage limits (/goal resume)",
        ]
        self.assertTrue(limits.mark_if_limited("grok", lines))
        self.assertIn("grok", limits.load())

    def test_wrapped_limit_detected(self):
        lines = ["■ You’ve hit your", "usage limit. Try again later."]
        self.assertTrue(limits.mark_if_limited("grok", lines))

    def test_advisories_not_detected(self):
        for line in (
            "Approaching rate limits",
            "Switch to gpt-5.6-luna for lower credit usage?",
            "3. Keep current model (never show again) Hide future rate limit reminders about switching models.",
            "⚠ Heads up, you have less than 5% of your weekly limit left. Run /status for a breakdown.",
        ):
            self.assertFalse(limits.mark_if_limited("grok", [line]), line)
        self.assertEqual(limits.load(), {})

    def test_advisory_plus_limit_is_limit(self):
        lines = ["Approaching rate limits", "You've hit your usage limit. Try again in 3 hours"]
        self.assertTrue(limits.mark_if_limited("grok", lines))


class TestResetMs(Base):
    def test_hours_minutes(self):
        self.assertEqual(limits.reset_ms("try again in 3 hours 12 minutes"), 3 * 3600000 + 12 * 60000)

    def test_resets_at(self):
        ms = limits.reset_ms("resets at 3pm")
        self.assertIsNotNone(ms)
        self.assertTrue(0 < ms <= 1440 * 60000)

    def test_try_again_at_time(self):
        ms = limits.reset_ms("try again at 5:13 PM")
        self.assertIsNotNone(ms)
        self.assertTrue(0 < ms <= 1440 * 60000)

    def test_try_again_at_datetime(self):
        import time as _time
        target = _time.localtime(_time.time() + 2 * 86400)
        month = _time.strftime("%B", target)
        text = f"try again at {month} {target.tm_mday}th, {target.tm_year} {target.tm_hour % 12 or 12}:{target.tm_min:02d} {'AM' if target.tm_hour < 12 else 'PM'}"
        ms = limits.reset_ms(text)
        self.assertIsNotNone(ms)
        self.assertTrue(86400_000 < ms <= 3 * 86400_000)

    def test_datetime_wrapped_and_case(self):
        import time as _time
        target = _time.localtime(_time.time() + 2 * 86400)
        month = _time.strftime("%b", target)
        text = f"TRY AGAIN\nAT  {month} {target.tm_mday}, {target.tm_year} {target.tm_hour % 12 or 12}:{target.tm_min:02d} pm"
        self.assertIsNotNone(limits.reset_ms(text))

    def test_datetime_no_year_next_occurrence(self):
        import time as _time
        target = _time.localtime(_time.time() - 2 * 86400)  # this year's date already past
        month = _time.strftime("%b", target)
        text = f"try again at {month} {target.tm_mday} {target.tm_hour % 12 or 12}:{target.tm_min:02d} PM"
        ms = limits.reset_ms(text)
        self.assertEqual(ms, limits.MAX_MS)  # rolled into next year, capped

    def test_datetime_past_is_none(self):
        self.assertIsNone(limits.reset_ms("try again at Sep 26th, 2020 5:13 PM"))

    def test_datetime_capped_at_max(self):
        ms = limits.reset_ms("try again at Sep 26th, 2999 5:13 PM")
        self.assertEqual(ms, limits.MAX_MS)


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

    def test_init_minimal(self):
        for name in ("config.json", "roster.md"):
            p = os.path.join(self.home, name)
            if os.path.exists(p):
                os.remove(p)
        code, out, _ = cli("init", "--minimal")
        self.assertEqual(code, 0)
        examples = os.path.join(limits.skill_dir(), "examples")
        with open(os.path.join(examples, "config-minimal.json")) as fh:
            want_config = fh.read()
        with open(os.path.join(self.home, "config.json")) as fh:
            self.assertEqual(fh.read(), want_config)
        with open(os.path.join(examples, "roster-minimal.md")) as fh:
            want_roster = fh.read()
        with open(os.path.join(self.home, "roster.md")) as fh:
            self.assertEqual(fh.read(), limits.ROSTER_COPY_NOTE + want_roster)
        code, out, _ = cli("init", "--minimal")
        self.assertIn("skipped", out)
        cfg = limits.load_config()
        self.assertIn("claude", cfg["products"])
        self.assertNotIn("swe", cfg["products"])


class TestExamplesConsistency(unittest.TestCase):
    """Every bundled example config validates; every key in each roster's
    'Keys' table resolves against the matching config."""

    def _load_example_config(self, name):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        import shutil
        shutil.copyfile(os.path.join(limits.skill_dir(), "examples", name),
                        os.path.join(home.name, "config.json"))
        old = os.environ.get("MODEL_ROUTER_HOME")
        os.environ["MODEL_ROUTER_HOME"] = home.name
        self.addCleanup(lambda: os.environ.pop("MODEL_ROUTER_HOME", None) if old is None else os.environ.__setitem__("MODEL_ROUTER_HOME", old))
        return limits.load_config()

    def test_configs_validate(self):
        import glob
        paths = glob.glob(os.path.join(limits.skill_dir(), "examples", "config*.json"))
        self.assertGreaterEqual(len(paths), 2)
        for path in paths:
            with self.subTest(path=path):
                self._load_example_config(os.path.basename(path))

    def test_roster_keys_resolve(self):
        import glob
        import re
        for roster_path in glob.glob(os.path.join(limits.skill_dir(), "examples", "roster*.md")):
            suffix = os.path.basename(roster_path)[len("roster"):-len(".md")]  # "" or "-minimal"
            config_name = f"config{suffix}.json"
            config_path = os.path.join(limits.skill_dir(), "examples", config_name)
            if not os.path.exists(config_path):
                continue
            with self.subTest(roster=roster_path):
                with open(roster_path, encoding="utf-8") as fh:
                    text = fh.read()
                section = re.search(r"## Keys\n(.*?)(?=\n## |\Z)", text, re.S)
                self.assertIsNotNone(section, roster_path)
                keys = re.findall(r"`(\w+(?::\w+)?)`", section.group(1))
                self.assertTrue(keys, roster_path)
                cfg = self._load_example_config(config_name)
                for key in keys:
                    self.assertIsNotNone(limits.resolve_key(key, cfg), f"{roster_path}: {key}")


class TestFirstSkipsUnknown(Base):
    def test_unknown_key_skipped(self):
        code, out, err = cli("first", "nope", "swe")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "swe")
        self.assertIn("limits: skipping unknown key: nope", err)

    def test_unknown_product_and_bare_skipped(self):
        code, out, err = cli("first", "palm:big", "vega", "opus")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "claude:opus")
        self.assertEqual(err.count("skipping unknown key"), 2)

    def test_all_unknown_exits_1(self):
        code, _, err = cli("first", "nope", "palm:big")
        self.assertEqual(code, 1)

    def test_strict_commands_still_exit_2(self):
        for argv in (("mark", "nope"), ("clear", "nope"), ("scan", "nope"), ("run", "nope", "--", "true")):
            code, _, _ = cli(*argv)
            self.assertEqual(code, 2, argv)


class TestBundledConfig(Base):
    def test_no_launchers_key_uses_detection(self):
        with open(os.path.join(limits.skill_dir(), "examples", "config.json")) as fh:
            raw = json.load(fh)
        self.assertNotIn("launchers", raw)
        os.remove(os.path.join(self.home, "config.json"))
        cfg = limits.load_config()
        self.assertEqual(cfg["launchers"], limits.default_launchers())


class TestRunSpawnFailure(Base):
    def test_missing_command_exits_75(self):
        code, _, err = cli("run", "swe", "--", "definitely-not-a-real-command-xyz")
        self.assertEqual(code, 75)
        self.assertIn("limits: cannot start definitely-not-a-real-command-xyz: not installed or not executable", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("swe", limits.load())

    def test_option_after_dashes_exits_2(self):
        code, _, err = cli("run", "swe", "--", "--log", "x")
        self.assertEqual(code, 2)
        self.assertIn("limits: options such as --log go before the key: limits.py run [--log F] <key> -- <command...>", err)


class TestIsoToEpoch(Base):
    def test_trailing_z(self):
        import calendar
        self.assertEqual(limits.iso_to_epoch("2026-01-01T00:00:00Z"), calendar.timegm((2026, 1, 1, 0, 0, 0)))

    def test_naive_is_utc(self):
        self.assertEqual(limits.iso_to_epoch("2026-01-01T00:00:00"), limits.iso_to_epoch("2026-01-01T00:00:00Z"))

    def test_empty(self):
        self.assertIsNone(limits.iso_to_epoch(None))
        self.assertIsNone(limits.iso_to_epoch(""))


class TestProductDeadline(Base):
    def test_dead_when_all_models_cooling(self):
        cli("mark", "claude:haiku", "--for", "1h")
        cli("mark", "claude:sonnet", "--for", "2h")
        cli("mark", "claude:opus", "--for", "3h")
        cli("mark", "claude:fable", "--for", "5h")
        cfg = limits.load_config()
        cool = limits.cooling()
        self.assertEqual(limits.dead_until("claude", cool, cfg), cool["claude:haiku"])

    def test_alive_while_one_model_up(self):
        cli("mark", "claude:haiku", "--for", "1h")
        cfg = limits.load_config()
        self.assertIsNone(limits.dead_until("claude", limits.cooling(), cfg))

    def test_status_and_first_agree(self):
        for m in ("haiku", "sonnet", "opus", "fable"):
            cli("mark", f"claude:{m}", "--for", "1h")
        code, out, _ = cli("status")
        self.assertIn("DEAD claude", out)
        code, out, _ = cli("first", "claude", "swe")
        self.assertEqual(out.strip(), "swe")


class TestKeyCaseAndShape(Base):
    def test_uppercase_key(self):
        code, out, _ = cli("mark", "OPUS", "--for", "1h")
        self.assertEqual(code, 0)
        self.assertIn("claude:opus", limits.load())

    def test_uppercase_product_model(self):
        self.assertEqual(limits.norm_key("Codex:Astra", limits.load_config()), "codex:astra")

    def test_bad_model_name_exits_2(self):
        code, _, _ = cli("mark", "claude:bad!!", "--for", "1h")
        self.assertEqual(code, 2)

    def test_unlisted_model_records_with_notice(self):
        code, _, err = cli("mark", "codex:vega", "--for", "1h")
        self.assertEqual(code, 0)
        self.assertIn("limits: codex:vega is not in config; recording anyway", err)
        self.assertIn("codex:vega", limits.load())


class TestWhereLauncherDocs(Base):
    def test_missing_doc_skipped(self):
        write_config(self.home, dict(PRODUCTS), launchers=["shell", "nodoc"])
        code, out, err = cli("where")
        self.assertEqual(code, 0)
        self.assertIn('limits: no doc for launcher "nodoc"; skipping', err)
        launcher_rows = [l for l in out.splitlines() if l.startswith("launcher")]
        self.assertEqual(len(launcher_rows), 1)
        self.assertIn("shell.md", launcher_rows[0])


class TestRosterCopyNote(Base):
    def test_init_prepends_note_and_where_warns(self):
        code, _, _ = cli("init")
        self.assertEqual(code, 0)
        roster = os.path.join(self.home, "roster.md")
        with open(roster) as fh:
            first = fh.readline()
        self.assertEqual(
            first,
            "<!-- model-router: unedited copy of the author's roster. Rewrite it for your subscriptions, then delete this line. -->\n",
        )
        code, _, err = cli("where")
        self.assertEqual(code, 0)
        self.assertIn("limits: roster is still an unedited copy of the example; ask the user to rewrite it", err)

    def test_where_quiet_after_rewrite(self):
        cli("init")
        with open(os.path.join(self.home, "roster.md"), "w") as fh:
            fh.write("# Roster\nmine\n")
        code, _, err = cli("where")
        self.assertEqual(code, 0)
        self.assertNotIn("unedited copy", err)


class TestScanFileErrors(Base):
    def test_missing_file(self):
        code, _, err = cli("scan", "grok", "--file", os.path.join(self.home, "nope.txt"))
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertNotIn("Traceback", err)

    def test_unreadable_file(self):
        code, _, err = cli("scan", "grok", "--file", self.home)  # a directory
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)


class TestLimitDeadline(Base):
    def test_spent_week_uses_reset(self):
        self.assertEqual(limits.limit_deadline({"used": 100, "resets": 1234}, False, now=1), 1234)
        self.assertEqual(limits.limit_deadline({"used": 100, "resets": 1234}, True, now=1), 1234)

    def test_bare_reached_is_5h(self):
        self.assertEqual(limits.limit_deadline({"used": 50}, True, now=1000), 1000 + 5 * 3600)

    def test_not_limited(self):
        self.assertIsNone(limits.limit_deadline({"used": 50}, False, now=1))
        self.assertIsNone(limits.limit_deadline({}, False, now=1))

    def test_budget_marks_5h_on_reached(self):
        import time as _time
        cache = {"at": _time.time(), "codex": {"week": {"used": 10, "resets": _time.time() + 86400, "span": 7 * 86400}, "reached": True}}
        with open(limits.budget_cache(), "w") as fh:
            json.dump(cache, fh)
        code, _, _ = cli("budget")
        self.assertEqual(code, 0)
        until = limits.load()["codex"]
        self.assertLess(until, (_time.time() + 6 * 3600) * 1000)
        self.assertGreater(until, (_time.time() + 4 * 3600) * 1000)


class TestBudgetNoFetcher(Base):
    def test_products_without_fetcher_print_line(self):
        import time as _time
        with open(limits.budget_cache(), "w") as fh:
            json.dump({"at": _time.time(), "claude": {"week": {"used": 1, "resets": _time.time() + 86400, "span": 7 * 86400}}}, fh)
        code, out, _ = cli("budget")
        self.assertEqual(code, 0)
        self.assertIn("swe     normal   not graded (no usage source)", out)
        self.assertIn("grok    normal   not graded (no usage source)", out)
        self.assertIn("codex   normal   not graded (no usage source)", out)


if __name__ == "__main__":
    unittest.main()
