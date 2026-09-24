#!/usr/bin/env python3
"""Record of exhausted rate limits. The model-router skill reads this before
delegating and writes to it when a limit is hit.

State lives in $MODEL_ROUTER_LIMITS_FILE, else $MODEL_ROUTER_HOME/limits.json
({key: deadline in epoch ms}). Every write is appended to limits.log.jsonl next
to it with its origin (pid, cwd, command, matched line, exit code); `status`
shows the origin of each live record. HOME_DIR is $MODEL_ROUTER_HOME or
~/.agents/model-router/. Products and models come from a config file, not this
file. Parents launch child sessions via `run`: it checks the records first and
writes one if the child stops on a limit. Standard library only.

  limits.py status
  limits.py where                    print the paths in use (home, config, roster, launchers)
  limits.py init [--minimal]         seed HOME_DIR with the bundled config and roster
  limits.py budget [--refresh]       surplus / normal / tight for the week's quota
  limits.py first <key>...           first live entry from a fallback chain
  limits.py run [--log F] <key> -- <command>   launch a non-interactive child; record a limit, exit 75
  limits.py scan <key> [--file F]    read a TUI child's output; record a limit, exit 75
  limits.py mark <key> [--for 5h]
  limits.py clear <key>
"""
import argparse
import json
import os
import re
import sys
import time

MAX_MS = 7 * 86_400_000  # allow recording up to weekly limits
UNITS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
NAME_RE = re.compile(r"[a-z0-9_-]+")
MODEL_RE = re.compile(r"[a-z0-9_.-]+")
ROSTER_COPY_NOTE = "<!-- model-router: unedited copy of the author's roster. Rewrite it for your subscriptions, then delete this line. -->\n"


def skill_dir():
    return os.path.dirname(os.path.abspath(__file__))


def home_dir():
    return os.path.expanduser(os.environ.get("MODEL_ROUTER_HOME", "~/.agents/model-router"))


def state_file():
    return os.environ.get("MODEL_ROUTER_LIMITS_FILE") or os.path.join(home_dir(), "limits.json")


def budget_cache():
    return os.path.join(home_dir(), "budget.json")


def fail(msg):
    print(f"limits: {msg}", file=sys.stderr)
    raise SystemExit(2)


# ---------- config ----------

def config_path():
    """User config wins; else the bundled example. None if neither exists."""
    user = os.path.join(home_dir(), "config.json")
    if os.path.exists(user):
        return user
    bundled = os.path.join(skill_dir(), "examples", "config.json")
    return bundled if os.path.exists(bundled) else None


def default_launchers():
    import shutil

    out = ["orca"] if shutil.which("orca") else []
    out.append("shell")
    if shutil.which("tmux"):
        out.append("tmux")
    out.append("subagent")
    return out


def load_config():
    """Validated {products: {name: {models: [...], free_until: str|None}}, launchers: [...]}."""
    path = config_path()
    if path is None:
        fail(f"no config: {os.path.join(home_dir(), 'config.json')} not found and no bundled example")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as err:
        fail(f"bad config {path}: {err}")
    products = raw.get("products") if isinstance(raw, dict) else None
    if not isinstance(products, dict) or not products:
        fail(f"bad config {path}: 'products' must be a non-empty object")
    out = {}
    for name, spec in products.items():
        if not NAME_RE.fullmatch(name) or not isinstance(spec, dict):
            fail(f"bad config {path}: invalid product {name!r}")
        models = spec.get("models", [])
        free_until = spec.get("free_until")
        if not isinstance(models, list) or any(not MODEL_RE.fullmatch(str(m)) for m in models):
            fail(f"bad config {path}: invalid models for {name!r}")
        if free_until is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(free_until)):
            fail(f"bad config {path}: invalid free_until for {name!r}")
        out[name] = {"models": [str(m) for m in models], "free_until": free_until}
    launchers = raw.get("launchers")
    if launchers is None:
        launchers = default_launchers()
    elif not isinstance(launchers, list) or any(not isinstance(x, str) for x in launchers):
        fail(f"bad config {path}: 'launchers' must be a list of names")
    return {"products": out, "launchers": launchers, "path": path}


def resolve_key(key, cfg):
    """Canonical '<product>' or '<product>:<model>', or None when the key is unknown,
    ambiguous, or malformed. Keys are case-insensitive. Bare models resolve only
    when exactly one product lists them."""
    key = key.lower()
    products = cfg["products"]
    if ":" in key:
        product, model = key.split(":", 1)
        if product not in products or not MODEL_RE.fullmatch(model):
            return None
        return key
    if key in products:
        return key
    owners = [p for p, spec in products.items() if key in spec["models"]]
    return f"{owners[0]}:{key}" if len(owners) == 1 else None


def norm_key(key, cfg):
    """resolve_key or exit 2."""
    key = key.lower()
    out = resolve_key(key, cfg)
    if out is None:
        fail(unknown_msg(key, cfg))
    return out


def unknown_msg(key, cfg):
    listing = " ".join(
        f"{p}({','.join(s['models']) or '-'})" for p, s in cfg["products"].items()
    )
    return f"unknown or ambiguous key: {key}\n  configured: {listing}"


# ---------- state ----------

def load():
    try:
        with open(state_file(), encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in state.items() if isinstance(v, (int, float))}


def save(state):
    os.makedirs(home_dir(), exist_ok=True)
    tmp = state_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, state_file())


def log_file():
    base = state_file()
    return (base[:-5] if base.endswith(".json") else base) + ".log.jsonl"


LOG_MAX_BYTES, LOG_KEEP = 256_000, 500


def ps(*args):
    import subprocess

    try:
        return subprocess.run(["ps", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def origin():
    """Who is writing: this process, its parent's command line, the chain of
    ancestors (which agent session ran us), and the cwd."""
    ppid = os.getppid()
    procs = {}
    for row in ps("-A", "-o", "pid=,ppid=,comm=").splitlines():
        parts = row.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]))
    chain, pid = [], ppid
    while pid in procs and pid > 1 and len(chain) < 8:
        chain.append(f"{procs[pid][1]}[{pid}]")
        pid = procs[pid][0]
    try:
        cwd = os.getcwd()
    except OSError:  # the worktree was removed under us
        cwd = None
    parent = ps("-o", "command=", "-p", str(ppid)).strip()
    return {"pid": os.getpid(), "ppid": ppid, "ancestors": chain, "parent": parent[:2000], "cwd": cwd}


def append_log(entry):
    """Append one JSON line; keep the newest LOG_KEEP once the file grows. Never fails the caller."""
    path = log_file()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if os.path.getsize(path) > LOG_MAX_BYTES:
            with open(path, encoding="utf-8") as fh:
                keep = fh.readlines()[-LOG_KEEP:]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(keep)
            os.replace(tmp, path)
    except OSError as err:
        print(f"limits: could not write {path}: {err}", file=sys.stderr)


def record(key, until, by, **detail):
    """Store key's deadline and log where it came from."""
    state = load()
    state[key] = until
    save(state)
    append_log({"at": int(time.time() * 1000), "key": key, "until": until, "by": by, **origin(), **detail})


def origins():
    """{key: the log entry that wrote its current deadline}."""
    out = {}
    try:
        with open(log_file(), encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and "key" in e:
                    out.setdefault(e["key"], []).append(e)
    except OSError:
        pass
    state = load()
    # a record rewritten by an older limits.py has no matching entry
    return {k: next((e for e in reversed(v) if e.get("until") == state.get(k)), None) for k, v in out.items()}


def cooling():
    now = time.time() * 1000
    return {k: v for k, v in load().items() if v > now}


def until_text(ms):
    fmt = "%H:%M" if ms - time.time() * 1000 < 86_400_000 else "%m-%d %H:%M"
    return time.strftime(fmt, time.localtime(ms / 1000))


def parse_duration(text):
    m = re.fullmatch(r"(\d+)([mhd])", text or "")
    return min(int(m.group(1)) * UNITS[m.group(2)], MAX_MS) if m else None


def free_until(product, cfg):
    """free_until of the product owning this key, else None."""
    return cfg["products"].get(product.split(":")[0], {}).get("free_until")


def expired(product, cfg):
    until = free_until(product, cfg)
    return until is not None and time.strftime("%Y-%m-%d") > until


def product_of(key, cfg):
    return key.split(":")[0]


def dead_until(key, cool, cfg):
    """Deadline if the key is cooling. A bare product is dead when it has its own
    record or every listed model is cooling — the expiry is the earliest model's.
    None if alive."""
    if expired(key, cfg):
        return float("inf")
    product = product_of(key, cfg)
    if ":" in key:
        hits = [cool[k] for k in (key, product) if k in cool]
        return max(hits) if hits else None
    if product in cool:
        return cool[product]
    models = [f"{product}:{m}" for m in cfg["products"].get(product, {}).get("models", [])]
    if models and all(m in cool for m in models):
        return min(cool[m] for m in models)
    return None


# ---------- limit detection ----------
# Phrases each CLI prints when it stops on a limit. Only the last TAIL_LINES are
# scanned, and `run` ignores them when the child exits 0, so words inside a
# report's body don't false-positive. Bare "rate limit" and "429" are too common
# in reports about code; they count only as an error ("rate limited", "status: 429").
LIMIT_RE = re.compile(
    r"usage limit|limit reached|limit exceeded|hit your .{0,20}limit|rate.?limited|rate_limit_error"
    r"|quota exceeded|too many requests|(?:error|status|http|code)\W{0,3}429\b"
    r"|out of credits|insufficient credits|利用上限|上限に達",
    re.I,
)
# Advisories and context-window messages mention limits without being a quota;
# their lines are dropped before matching.
ADVISORY_RE = re.compile(
    r"approaching rate.?limits?|rate.?limit reminders|less than \d+% of your .{0,20}limit left"
    r"|context (?:window |length )?limit",
    re.I,
)
TAIL_LINES = 30
EXIT_LIMITED = 75  # parents see this exit code and move to the next fallback


MONTHS = {
    m: i
    for i, name in enumerate(
        "january february march april may june july august september october november december".split(), 1
    )
    for m in (name, name[:3])
}


def _ampm_hour(h, ampm):
    return int(h) % 12 + (12 if ampm.lower() == "pm" else 0)


def reset_ms(text):
    """Cooldown length from 'try again in 3 hours 12 minutes', 'resets at 3pm',
    'try again at 5:13 PM', or 'try again at Sep 26th, 2026 5:13 PM' (local time).
    None if unreadable or the named time is already past."""
    flat = re.sub(r"\s+", " ", text)  # terminals wrap messages mid-phrase
    m = re.search(r"(?:again|resets?) in\s+(?:(\d+)\s*d\w*)?\s*(?:(\d+)\s*h\w*)?\s*(?:(\d+)\s*m\w*)?", flat, re.I)
    if m and any(m.groups()):
        d, h, mi = (int(g or 0) for g in m.groups())
        return min(d * UNITS["d"] + h * UNITS["h"] + mi * UNITS["m"], MAX_MS)
    m = re.search(
        r"(?:again|resets?) at\s+(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(?:(\d{4})\s+)?(\d{1,2}):(\d{2})\s*([ap]m)\b",
        flat, re.I,
    )
    if m and MONTHS.get(m.group(1).lower()):
        month = MONTHS[m.group(1).lower()]
        day, year = int(m.group(2)), m.group(3)
        hour, minute = _ampm_hour(m.group(4), m.group(6)), int(m.group(5))
        years = (int(year),) if year else (time.localtime().tm_year, time.localtime().tm_year + 1)
        for y in years:
            try:
                ahead = time.mktime((y, month, day, hour, minute, 0, 0, 0, -1)) - time.time()
            except (ValueError, OverflowError):
                continue
            if ahead > 0:
                return min(int(ahead * 1000), MAX_MS)
        return None
    m = re.search(r"(?:again|resets?)(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b", flat, re.I)
    if m:
        hour = _ampm_hour(m.group(1), m.group(3))
        now = time.localtime()
        ahead = (hour * 60 + int(m.group(2) or 0)) - (now.tm_hour * 60 + now.tm_min)
        return (ahead % 1440 or 1440) * UNITS["m"]
    return None


def find_limit(lines):
    """(matched phrase, the line holding it, cooldown ms or None), or None."""
    tail = "\n".join(l for l in lines[-TAIL_LINES:] if not ADVISORY_RE.search(l))
    hit = LIMIT_RE.search(tail)
    if not hit:
        return None
    start = tail.rfind("\n", 0, hit.start()) + 1
    end = tail.find("\n", hit.start())
    line = tail[start:end if end >= 0 else None].strip()
    return hit.group(0), line[:300], reset_ms(tail)


def mark_if_limited(key, lines, by="scan", **detail):
    found = find_limit(lines)
    if not found:
        return False
    match, line, ms = found
    until = int(time.time() * 1000) + (ms or UNITS["h"] * 5)
    record(key, until, by, match=match, line=line, **detail)
    print(f"limits: {key} hit a limit (matched {match!r} in {line!r}); recorded until {until_text(until)}", file=sys.stderr)
    return True


# ---------- commands ----------

def cmd_status(_):
    cfg = load_config()
    cool = cooling()
    for product, spec in cfg["products"].items():
        models = [f"{product}:{m}" for m in spec["models"]]
        if product in cool:
            ok, why = False, f"all limited (until {until_text(cool[product])})"
        elif expired(product, cfg):
            ok, why = False, f"free tier ended {spec['free_until']}; treating as dead"
        else:
            dead = [m for m in models if m in cool]
            alive = [m.split(":")[-1] for m in models if m not in cool]
            ok = not models or bool(alive)
            why = "usable" if not dead else (
                "limited: " + ", ".join(f"{m.split(':')[-1]} (until {until_text(cool[m])})" for m in dead)
                + (f". usable: {' '.join(alive)}" if alive else "")
            )
        print(f"{'ok' if ok else 'DEAD'} {product:<7} {why}")
    if cool:
        print("records:")
        by_key = origins()
        for key, until in sorted(cool.items(), key=lambda kv: kv[1]):
            print(f"  {key}  until {until_text(until)}")
            for line in describe_origin(by_key.get(key)):
                print(f"      {line}")
        print(f"  (history: {log_file()})")
    return 0


def describe_origin(e):
    """Lines saying who wrote a record, for status."""
    if e is None:
        return ["no origin logged (written by an older limits.py or by hand)"]
    head = f"by {e.get('by')} at {time.strftime('%m-%d %H:%M:%S', time.localtime(e.get('at', 0) / 1000))}"
    if "exit" in e:
        head += f", exit {e['exit']}"
    head += f", pid {e.get('pid')}, cwd {e.get('cwd')}"
    out = [head]
    if e.get("command"):
        out.append("command: " + " ".join(e["command"])[:200])
    if e.get("source"):
        out.append(f"read from: {e['source']}")
    if e.get("line"):
        out.append(f"matched {e.get('match')!r} in: {e['line']}")
    if e.get("reason"):
        out.append(f"reason: {e['reason']}")
    if e.get("ancestors"):
        out.append("launched from: " + " < ".join(e["ancestors"]))
    return out


def cmd_mark(args):
    cfg = load_config()
    key = norm_key(args.key, cfg)
    if ":" in key and key.split(":", 1)[1] not in cfg["products"][product_of(key, cfg)]["models"]:
        print(f"limits: {key} is not in config; recording anyway", file=sys.stderr)
    ms = parse_duration(args.duration)
    if ms is None:
        fail(f"bad duration: {args.duration} (30m / 5h / 3d)")
    until = int(time.time() * 1000) + ms
    record(key, until, "mark", duration=args.duration)
    print(f"{key}  until {until_text(until)}")
    return 0


def cmd_clear(args):
    key = norm_key(args.key, load_config())
    state = load()
    if state.pop(key, None) is not None:
        save(state)
        append_log({"at": int(time.time() * 1000), "key": key, "until": None, "by": "clear", **origin()})
    return 0


def cmd_first(args):
    cfg = load_config()
    cool = cooling()
    for arg in args.keys:
        key = resolve_key(arg, cfg)
        if key is None:
            print(f"limits: skipping unknown key: {arg}", file=sys.stderr)
            continue
        if dead_until(key, cool, cfg) is None:
            print(key)
            return 0
    print("limits: no live key in the chain", file=sys.stderr)
    return 1


def cmd_scan(args):
    key = norm_key(args.key, load_config())
    if args.file:
        try:
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as err:
            fail(f"cannot read {args.file}: {err.strerror or err}")
    else:
        text = sys.stdin.read()
    source = args.file or "stdin"
    return EXIT_LIMITED if mark_if_limited(key, text.splitlines(), "scan", source=source) else 0


def cmd_run(args):
    """Check records before launching a child; don't start a dead one. While it
    runs, pass output through and watch the tail."""
    import collections
    import subprocess
    import threading

    cfg = load_config()
    key = norm_key(args.key, cfg)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        fail("no command to run (limits.py run <key> -- <command...>)")
    if command[0].startswith("-"):
        fail("options such as --log go before the key: limits.py run [--log F] <key> -- <command...>")
    until = dead_until(key, cooling(), cfg)
    if until is not None:
        when = "free tier ended" if until == float("inf") else f"until {until_text(until)}"
        print(f"limits: {key} is limited ({when}); not starting", file=sys.stderr)
        return EXIT_LIMITED
    tail = collections.deque(maxlen=TAIL_LINES)
    # Some launchers drop a terminal's output once the child exits; --log keeps the report.
    log = open(args.log, "w", encoding="utf-8") if args.log else None
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    except (FileNotFoundError, PermissionError):
        if log:
            log.close()
        print(f"limits: cannot start {command[0]}: not installed or not executable; treat this tier as unavailable", file=sys.stderr)
        return EXIT_LIMITED

    def pump(src, dst):
        for line in src:
            dst.write(line)
            dst.flush()
            if log:
                log.write(line)
                log.flush()
            tail.append(line.rstrip("\n"))

    threads = [threading.Thread(target=pump, args=p) for p in ((proc.stdout, sys.stdout), (proc.stderr, sys.stderr))]
    for t in threads:
        t.start()
    code = proc.wait()
    for t in threads:
        t.join()
    if log:
        log.close()
    if code == 0:
        # A CLI that stops on a limit exits non-zero; limit words in a clean
        # run are the child's report talking about limits.
        found = find_limit(list(tail))
        if found:
            print(
                f"limits: {key} exited 0, so nothing was recorded, though its output mentions {found[0]!r}. "
                f"If it really stopped on a limit: limits.py mark {key} --for <time until reset>",
                file=sys.stderr,
            )
        return 0
    limited = mark_if_limited(key, list(tail), "run", command=[c[:200] for c in command], exit=code, log=args.log)
    return EXIT_LIMITED if limited else code


def cmd_where(_):
    cfg = load_config()
    roster = os.path.join(home_dir(), "roster.md")
    if not os.path.exists(roster):
        roster = os.path.join(skill_dir(), "examples", "roster.md")
    rows = [("home", home_dir()), ("config", cfg["path"]), ("roster", roster)]
    for name in cfg["launchers"]:
        path = os.path.join(skill_dir(), "launchers", f"{name}.md")
        if os.path.exists(path):
            rows.append(("launcher", path))
        else:
            print(f'limits: no doc for launcher "{name}"; skipping', file=sys.stderr)
    try:
        with open(roster, encoding="utf-8") as fh:
            head = fh.read(200)
    except OSError:
        head = ""
    if head.startswith("<!-- model-router: unedited copy"):
        print("limits: roster is still an unedited copy of the example; ask the user to rewrite it", file=sys.stderr)
    for name, path in rows:
        print(f"{name}\t{path}")
    return 0


def cmd_init(args):
    import shutil

    suffix = "-minimal" if args.minimal else ""
    os.makedirs(home_dir(), exist_ok=True)
    for name in ("config.json", "roster.md"):
        src = os.path.join(skill_dir(), "examples", name.replace(".", f"{suffix}."))
        dst = os.path.join(home_dir(), name)
        if not os.path.exists(src):
            print(f"skipped {name}: no bundled example")
        elif os.path.exists(dst):
            print(f"skipped {dst}: already exists")
        else:
            if name == "roster.md":
                with open(src, encoding="utf-8") as fh:
                    body = fh.read()
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write(ROSTER_COPY_NOTE + body)
            else:
                shutil.copyfile(src, dst)
            print(f"copied {src} -> {dst}")
    print(f"edit {os.path.join(home_dir(), 'config.json')} and roster.md for your setup")
    return 0


# ---------- weekly budget ----------
# Pace, not raw usage. Headroom = elapsed share of the week - usage (points).
# 50% the day before reset is surplus; 50% the day after is overspending — same formula.
BUDGET_TTL = 300
SURPLUS_AT, TIGHT_AT, TIGHT_USED, FIVE_HOUR_GATE = 25, -15, 85, 80


def fetch_json(url, headers):
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "model-router-limits", **headers})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def iso_to_epoch(text):
    from datetime import datetime, timezone

    if not text:
        return None
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))  # 3.9/3.10 can't parse 'Z'
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def claude_token():
    """OAuth token from macOS Keychain, else ~/.claude/.credentials.json."""
    import subprocess

    raw = None
    if sys.platform == "darwin":
        try:
            res = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10,
            )
            raw = res.stdout if res.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            raw = None
    if raw is None:
        with open(os.path.expanduser("~/.claude/.credentials.json"), encoding="utf-8") as fh:
            raw = fh.read()
    return json.loads(raw)["claudeAiOauth"]["accessToken"]


def claude_usage():
    data = fetch_json(
        "https://api.anthropic.com/api/oauth/usage",
        {"Authorization": f"Bearer {claude_token()}", "anthropic-beta": "oauth-2025-04-20"},
    )
    week = data.get("seven_day") or {}
    out = {"week": {"used": week.get("utilization"), "resets": iso_to_epoch(week.get("resets_at")), "span": 7 * 86400}}
    five = data.get("five_hour") or {}
    out["five_hour"] = five.get("utilization")
    # per-model weekly windows (seven_day_opus etc.) come back null on some plans
    out["models"] = {
        k[len("seven_day_"):]: {"used": v.get("utilization"), "resets": iso_to_epoch(v.get("resets_at")), "span": 7 * 86400}
        for k, v in data.items()
        if k.startswith("seven_day_") and isinstance(v, dict) and v.get("resets_at")
    }
    return out


def codex_usage():
    with open(os.path.expanduser("~/.codex/auth.json"), encoding="utf-8") as fh:
        tokens = json.load(fh).get("tokens") or {}
    data = fetch_json(
        "https://chatgpt.com/backend-api/wham/usage",
        {"Authorization": f"Bearer {tokens['access_token']}", "ChatGPT-Account-Id": tokens.get("account_id", "")},
    )
    limit = data.get("rate_limit") or {}
    win = limit.get("primary_window") or {}
    out = {"week": {"used": win.get("used_percent"), "resets": win.get("reset_at"), "span": win.get("limit_window_seconds")}}
    out["reached"] = bool(limit.get("limit_reached"))
    # models currently stopped. {"astra": resume epoch or None}
    out["blocked"] = {
        name.rsplit("-", 1)[-1]: info.get("available_at")
        for name, info in (data.get("model_usage") or {}).items()
        if isinstance(info, dict) and info.get("available") is False
    }
    return out


def load_budget(refresh, cfg):
    path = budget_cache()
    try:
        with open(path, encoding="utf-8") as fh:
            cached = json.load(fh)
        if not refresh and time.time() - cached.get("at", 0) < BUDGET_TTL:
            return cached
    except (OSError, ValueError):
        pass
    fresh = {"at": time.time()}
    fetchers = {"claude": claude_usage, "codex": codex_usage}
    for name in cfg["products"]:
        fetch = fetchers.get(name)
        if fetch is None:
            continue  # no fetcher for this product
        try:
            fresh[name] = fetch()
        except Exception as err:  # fall back to 'normal'; keep only the reason
            fresh[name] = {"error": f"{type(err).__name__}: {err}"[:120]}
    os.makedirs(home_dir(), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(fresh, fh)
    os.replace(tmp, path)
    return fresh


def grade(window):
    """(state, description). Missing numbers mean 'normal'."""
    used, resets, span = (window or {}).get("used"), (window or {}).get("resets"), (window or {}).get("span")
    if used is None or not resets or not span:
        return "normal", "no numbers"
    elapsed = min(max(1 - (resets - time.time()) / span, 0), 1) * 100
    room = elapsed - used
    text = f"week {used:.0f}% used / {elapsed:.0f}% elapsed ({room:+.0f}); resets {until_text(resets * 1000)}"
    if used >= TIGHT_USED or room <= TIGHT_AT:
        return "tight", text
    return ("surplus" if room >= SURPLUS_AT else "normal"), text


def limit_deadline(week, reached, now=None):
    """Epoch seconds a limited product cools until. A spent week (used >= 100) cools
    until the weekly reset; a bare limit_reached cools for 5 hours. None = not limited."""
    if (week.get("used") or 0) >= 100:
        return week.get("resets")
    if reached:
        return (time.time() if now is None else now) + 5 * 3600
    return None


def auto_mark(key, until_epoch, reason):
    """Copy a limit the API reports into the records. 5h if no deadline is readable."""
    until_ms = int(until_epoch * 1000) if until_epoch else int(time.time() * 1000) + 5 * UNITS["h"]
    if load().get(key, 0) < until_ms:
        record(key, min(until_ms, int(time.time() * 1000) + MAX_MS), "budget", reason=reason)


def cmd_budget(args):
    cfg = load_config()
    data = load_budget(args.refresh, cfg)
    for product in cfg["products"]:
        if product not in data:
            print(f"{product:<7} normal   not graded (no usage source)")
            continue
        info = data[product] or {}
        if "error" in info:
            print(f"{product:<7} normal   fetch failed ({info['error']})")
            continue
        state, text = grade(info.get("week"))
        week = info.get("week") or {}
        deadline = limit_deadline(week, info.get("reached"))
        if deadline is not None:
            auto_mark(product, deadline, f"usage API: week {week.get('used')}% used, limit_reached={bool(info.get('reached'))}")
        if product == "claude":
            five = info.get("five_hour")
            if five is not None:
                text += f"; 5h window {five:.0f}%"
                if state == "surplus" and five > FIVE_HOUR_GATE:
                    state, text = "normal", text + f" (over {FIVE_HOUR_GATE}%, not upgrading)"
        print(f"{product:<7} {state}   {text}")
        for model, window in (info.get("models") or {}).items():
            m_state, m_text = grade(window)
            print(f"  {model:<9} {m_state}   {m_text}")
            if (window.get("used") or 0) >= 100:
                auto_mark(f"{product}:{model}", window.get("resets"), f"usage API: {model} week {window.get('used')}% used")
        for model, available_at in (info.get("blocked") or {}).items():
            print(f"  {model:<9} limited   API reports unavailable")
            auto_mark(f"{product}:{model}", available_at, f"usage API: {model} unavailable")
    age = time.time() - data.get("at", 0)
    print(f"(fetched {age:.0f}s ago; --refresh to fetch again)")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="limits.py", description="record of exhausted rate limits")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="which products and models are limited").set_defaults(func=cmd_status)
    sub.add_parser("where", help="print the paths in use").set_defaults(func=cmd_where)
    sp = sub.add_parser("init", help="seed the home dir with bundled config and roster")
    sp.add_argument("--minimal", action="store_true", help="seed with the minimal examples instead")
    sp.set_defaults(func=cmd_init)
    sp = sub.add_parser("mark", help="record a limit")
    sp.add_argument("key")
    sp.add_argument("--for", dest="duration", default="5h", help="cooldown length (30m / 5h / 3d, default 5h, max 7d)")
    sp.set_defaults(func=cmd_mark)
    sp = sub.add_parser("clear", help="drop a record")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_clear)
    sp = sub.add_parser("budget", help="surplus / normal / tight for the week's quota")
    sp.add_argument("--refresh", action="store_true", help="skip the 5-minute cache and fetch again")
    sp.set_defaults(func=cmd_budget)
    sp = sub.add_parser("first", help="first live entry from a fallback chain")
    sp.add_argument("keys", nargs="+")
    sp.set_defaults(func=cmd_first)
    sp = sub.add_parser("run", help="launch a child after checking records; record if it stops on a limit (non-interactive commands)")
    sp.add_argument("--log", help="also write the child's output to this file")
    sp.add_argument("key")
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_run)
    sp = sub.add_parser("scan", help="read terminal output and record if it stopped on a limit (TUI sessions)")
    sp.add_argument("key")
    sp.add_argument("--file")
    sp.set_defaults(func=cmd_scan)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
