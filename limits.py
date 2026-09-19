#!/usr/bin/env python3
"""枠切れの記録。model-router skill が委譲の前に読み、上限に当たったら書く。

記録先は ~/.agents/model-router-limits.json（{キー: 期限の epoch ミリ秒}）。この skill 専用。
親は子セッションを run 経由で起動する。run は起動前に記録を見て、子が上限で止まったら書く。
標準ライブラリのみ。

  limits.py status
  limits.py budget [--refresh]       Claude / Codex の週枠が余り・普通・節約のどれか
  limits.py first <key>...         フォールバックの並びから生きている最初の段
  limits.py run <key> -- <command>   非対話の子を起動。上限なら記録して終了コード 75
  limits.py scan <key> [--file F]    TUI の子の出力を読み、上限なら記録して 75
  limits.py mark <key> [--for 5h]
  limits.py clear <key>
"""
import argparse
import json
import os
import re
import sys
import time

FILE = os.environ.get(
    "MODEL_ROUTER_LIMITS_FILE", os.path.expanduser("~/.agents/model-router-limits.json")
)

# 製品 → その製品の中で個別に枯れうるモデル。製品名そのものもキーになる（全モデル共通の上限）。
PRODUCTS = {
    "swe": (),
    "claude": ("haiku", "sonnet", "opus", "fable"),
    "codex": ("codex:astra", "codex:sol", "codex:luna", "codex:terra"),
    "grok": (),
}
SWE_FREE_UNTIL = "2026-10-10"
MAX_MS = 7 * 86_400_000  # 週次の上限まで書けるように 7 日
UNITS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}


def load():
    try:
        with open(FILE, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in state.items() if isinstance(v, (int, float))}


def save(state):
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, FILE)


def cooling():
    now = time.time() * 1000
    return {k: v for k, v in load().items() if v > now}


def until_text(ms):
    fmt = "%H:%M" if ms - time.time() * 1000 < 86_400_000 else "%m-%d %H:%M"
    return time.strftime(fmt, time.localtime(ms / 1000))


def markable(key):
    known = set(PRODUCTS) | {m for models in PRODUCTS.values() for m in models}
    return key in known or re.fullmatch(r"codex:[a-z0-9_-]+", key) is not None


def parse_duration(text):
    m = re.fullmatch(r"(\d+)([mhd])", text or "")
    return min(int(m.group(1)) * UNITS[m.group(2)], MAX_MS) if m else None


def fail(msg):
    print(f"limits: {msg}", file=sys.stderr)
    raise SystemExit(2)


def check_key(key):
    if not markable(key):
        fail(f"知らないキー: {key}\n  使えるキー: swe grok claude haiku sonnet opus fable codex codex:<モデル名>")


def cmd_status(_):
    cool = cooling()
    for product, models in PRODUCTS.items():
        if product in cool:
            ok, why = False, f"全体が上限（{until_text(cool[product])}まで）"
        elif product == "swe" and time.strftime("%Y-%m-%d") > SWE_FREE_UNTIL:
            ok, why = False, f"無料期限（{SWE_FREE_UNTIL}）切れ。全滅として扱う"
        else:
            dead = [m for m in models if m in cool]
            alive = [m.split(":")[-1] for m in models if m not in cool]
            ok = not models or bool(alive)
            why = "使える" if not dead else (
                "上限: " + "、".join(f"{m.split(':')[-1]}（{until_text(cool[m])}まで）" for m in dead)
                + (f"。使える: {' '.join(alive)}" if alive else "")
            )
        print(f"{'○' if ok else '×'} {product:<7} {why}")
    # `codex:<表に無いモデル名>` で記録したぶんなど、上の表に出ないキー
    known = set(PRODUCTS) | {m for models in PRODUCTS.values() for m in models}
    for key, until in sorted(cool.items(), key=lambda kv: kv[1]):
        if key not in known:
            print(f"  {key}  {until_text(until)} まで")
    return 0


def cmd_mark(args):
    check_key(args.key)
    ms = parse_duration(args.duration)
    if ms is None:
        fail(f"期間の形が違う: {args.duration}（30m / 5h / 3d）")
    state = load()
    state[args.key] = int(time.time() * 1000) + ms
    save(state)
    print(f"{args.key}  {until_text(state[args.key])} まで")
    return 0


def cmd_clear(args):
    check_key(args.key)
    state = load()
    if state.pop(args.key, None) is not None:
        save(state)
    return 0


def product_of(key):
    if key in PRODUCTS:
        return key
    return "codex" if key.startswith("codex:") else "claude"


def dead_until(key, cool):
    """キー自身か、その製品全体が冷却中なら期限を返す。生きていれば None。"""
    if key == "swe" and time.strftime("%Y-%m-%d") > SWE_FREE_UNTIL:
        return float("inf")
    hits = [cool[k] for k in (key, product_of(key)) if k in cool]
    return max(hits) if hits else None


# 上限で止まったときに各 CLI が出す文言。調査結果の本文に紛れた語で誤検出しないよう、
# 見るのは出力の末尾 TAIL_LINES 行だけ。
LIMIT_RE = re.compile(
    r"usage limit|rate.?limit|limit reached|hit your .{0,20}limit|quota exceeded|too many requests"
    r"|\b429\b|out of credits|insufficient credits|利用上限|上限に達",
    re.I,
)
TAIL_LINES = 30
EXIT_LIMITED = 75  # 親はこの終了コードを見たら次の段へ進む


def reset_ms(text):
    """「try again in 3 hours 12 minutes」「resets at 3pm」から復活までの長さを読む。読めなければ None。"""
    m = re.search(r"(?:again|resets?) in\s+(?:(\d+)\s*d\w*)?\s*(?:(\d+)\s*h\w*)?\s*(?:(\d+)\s*m\w*)?", text, re.I)
    if m and any(m.groups()):
        d, h, mi = (int(g or 0) for g in m.groups())
        return min(d * UNITS["d"] + h * UNITS["h"] + mi * UNITS["m"], MAX_MS)
    m = re.search(r"resets?(?: at)?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text, re.I)
    if m:
        hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        now = time.localtime()
        ahead = (hour * 60 + int(m.group(2) or 0)) - (now.tm_hour * 60 + now.tm_min)
        return (ahead % 1440 or 1440) * UNITS["m"]
    return None


def mark_if_limited(key, lines):
    tail = "\n".join(lines[-TAIL_LINES:])
    hit = LIMIT_RE.search(tail)
    if not hit:
        return False
    state = load()
    state[key] = int(time.time() * 1000) + (reset_ms(tail) or UNITS["h"] * 5)
    save(state)
    print(f"limits: {key} が上限（「{hit.group(0)}」を検出）。{until_text(state[key])} まで記録した", file=sys.stderr)
    return True


def cmd_first(args):
    cool = cooling()
    for key in args.keys:
        check_key(key)
        if dead_until(key, cool) is None:
            print(key)
            return 0
    print("limits: 挙げた段はすべて上限", file=sys.stderr)
    return 1


def cmd_scan(args):
    check_key(args.key)
    text = open(args.file, encoding="utf-8", errors="replace").read() if args.file else sys.stdin.read()
    return EXIT_LIMITED if mark_if_limited(args.key, text.splitlines()) else 0


def cmd_run(args):
    """子を起動する前に記録を見て、死んでいれば起動しない。起動したら出力を素通ししつつ末尾を見張る。"""
    import collections
    import subprocess
    import threading

    check_key(args.key)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        fail("起動するコマンドが無い（limits.py run <key> -- <command...>）")
    until = dead_until(args.key, cooling())
    if until is not None:
        when = "無料期限切れ" if until == float("inf") else f"{until_text(until)} まで"
        print(f"limits: {args.key} は上限（{when}）。起動しない", file=sys.stderr)
        return EXIT_LIMITED
    tail = collections.deque(maxlen=TAIL_LINES)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")

    def pump(src, dst):
        for line in src:
            dst.write(line)
            dst.flush()
            tail.append(line.rstrip("\n"))

    threads = [threading.Thread(target=pump, args=p) for p in ((proc.stdout, sys.stdout), (proc.stderr, sys.stderr))]
    for t in threads:
        t.start()
    code = proc.wait()
    for t in threads:
        t.join()
    return EXIT_LIMITED if mark_if_limited(args.key, list(tail)) else code


# ---------- 週枠の余り具合 ----------
# 使用率そのものではなくペースで見る。余裕 = 週の経過割合 − 使用率（ポイント）。
# リセット前日に 50% なら余り、リセット翌日に 50% なら使いすぎ、を同じ式で扱える。
BUDGET_CACHE = os.path.expanduser("~/.agents/model-router-budget.json")  # 割合と時刻だけ。トークンは書かない
BUDGET_TTL = 300
SURPLUS_AT, TIGHT_AT, TIGHT_USED, FIVE_HOUR_GATE = 25, -15, 85, 80


def fetch_json(url, headers):
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "model-router-limits", **headers})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def iso_to_epoch(text):
    from datetime import datetime

    return datetime.fromisoformat(text).timestamp() if text else None


def claude_usage():
    import subprocess

    raw = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True, timeout=10,
    ).stdout
    token = json.loads(raw)["claudeAiOauth"]["accessToken"]
    data = fetch_json(
        "https://api.anthropic.com/api/oauth/usage",
        {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
    )
    week = data.get("seven_day") or {}
    out = {"week": {"used": week.get("utilization"), "resets": iso_to_epoch(week.get("resets_at")), "span": 7 * 86400}}
    five = data.get("five_hour") or {}
    out["five_hour"] = five.get("utilization")
    # モデル別の週枠（seven_day_opus など）は、プランによって null で返る
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
    # モデル単位で止まっているもの。{"astra": 復活の epoch 秒 or None}
    out["blocked"] = {
        name.rsplit("-", 1)[-1]: info.get("available_at")
        for name, info in (data.get("model_usage") or {}).items()
        if isinstance(info, dict) and info.get("available") is False
    }
    return out


def load_budget(refresh):
    try:
        with open(BUDGET_CACHE, encoding="utf-8") as fh:
            cached = json.load(fh)
        if not refresh and time.time() - cached.get("at", 0) < BUDGET_TTL:
            return cached
    except (OSError, ValueError):
        pass
    fresh = {"at": time.time()}
    for name, fetch in (("claude", claude_usage), ("codex", codex_usage)):
        try:
            fresh[name] = fetch()
        except Exception as err:  # 取れなければ「普通」に倒す。理由だけ残す
            fresh[name] = {"error": f"{type(err).__name__}: {err}"[:120]}
    tmp = BUDGET_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(fresh, fh)
    os.replace(tmp, BUDGET_CACHE)
    return fresh


def grade(window):
    """(状態, 説明)。数字が欠けていれば普通。"""
    used, resets, span = (window or {}).get("used"), (window or {}).get("resets"), (window or {}).get("span")
    if used is None or not resets or not span:
        return "普通", "数字が取れない"
    elapsed = min(max(1 - (resets - time.time()) / span, 0), 1) * 100
    room = elapsed - used
    text = f"週 {used:.0f}% 使用 / {elapsed:.0f}% 経過（{room:+.0f}）。リセット {until_text(resets * 1000)}"
    if used >= TIGHT_USED or room <= TIGHT_AT:
        return "節約", text
    return ("余り" if room >= SURPLUS_AT else "普通"), text


def auto_mark(key, until_epoch):
    """API が「止まっている」と言っている枠を記録へ写す。期限が読めなければ 5h。"""
    state = load()
    until_ms = int(until_epoch * 1000) if until_epoch else int(time.time() * 1000) + 5 * UNITS["h"]
    if state.get(key, 0) < until_ms:
        state[key] = min(until_ms, int(time.time() * 1000) + MAX_MS)
        save(state)


def cmd_budget(args):
    data = load_budget(args.refresh)
    for product in ("claude", "codex"):
        info = data.get(product) or {}
        if "error" in info:
            print(f"{product:<7} 普通   取得できず（{info['error']}）")
            continue
        state, text = grade(info.get("week"))
        week = info.get("week") or {}
        if (week.get("used") or 0) >= 100 or info.get("reached"):
            auto_mark(product, week.get("resets"))
        if product == "claude":
            five = info.get("five_hour")
            if five is not None:
                text += f"。5h 枠 {five:.0f}%"
                if state == "余り" and five > FIVE_HOUR_GATE:
                    state, text = "普通", text + f"（{FIVE_HOUR_GATE}% 超なので格上げしない）"
        print(f"{product:<7} {state}   {text}")
        for model, window in (info.get("models") or {}).items():
            m_state, m_text = grade(window)
            print(f"  {model:<9} {m_state}   {m_text}")
            if (window.get("used") or 0) >= 100:
                auto_mark(model, window.get("resets"))
        for model, available_at in (info.get("blocked") or {}).items():
            print(f"  {model:<9} 上限   API が利用不可と返した")
            auto_mark(f"codex:{model}", available_at)
    age = time.time() - data.get("at", 0)
    print(f"（{age:.0f} 秒前の取得。--refresh で取り直す）")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="limits.py", description="枠切れの記録")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="どの製品・モデルが上限に当たっているか").set_defaults(func=cmd_status)
    sp = sub.add_parser("mark", help="上限に当たったと記録する")
    sp.add_argument("key")
    sp.add_argument("--for", dest="duration", default="5h", help="復活までの長さ（30m / 5h / 3d、既定 5h、上限 7d）")
    sp.set_defaults(func=cmd_mark)
    sp = sub.add_parser("clear", help="記録を消す")
    sp.add_argument("key")
    sp.set_defaults(func=cmd_clear)
    sp = sub.add_parser("budget", help="Claude / Codex の週枠が余り・普通・節約のどれか")
    sp.add_argument("--refresh", action="store_true", help="キャッシュ（5 分）を使わず取り直す")
    sp.set_defaults(func=cmd_budget)
    sp = sub.add_parser("first", help="フォールバックの並びから、生きている最初の段を返す")
    sp.add_argument("keys", nargs="+")
    sp.set_defaults(func=cmd_first)
    sp = sub.add_parser("run", help="記録を見てから子を起動し、上限で止まったら記録する（非対話のコマンド用）")
    sp.add_argument("key")
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_run)
    sp = sub.add_parser("scan", help="端末の出力を読み、上限で止まっていたら記録する（TUI セッション用）")
    sp.add_argument("key")
    sp.add_argument("--file")
    sp.set_defaults(func=cmd_scan)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
