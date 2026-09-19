# model-router

A skill for coding agents. For each kind of work — planning, fact-checking, research, implementation, UI, review — it decides which AI product and model should do it, and how to start that child session. It also records which products have hit their usage limit and switches to the fallback.

Written to be read by both Claude Code and Codex. The agent makes the decision itself by reading a table; there is no classifier or proxy.

## Three layers

| Layer | Same for everyone? | File |
|---|---|---|
| Policy — expensive models think, author ≠ reviewer, promote after two failures, exit code 75 means "next tier" | yes | `SKILL.md` |
| Roster — your products, models, effort levels, fallback order, what never goes to the cheap tier | **no, yours** | `~/.agents/model-router/roster.md` |
| Launcher — how a child session is started, read, waited on and typed into | per environment | `launchers/orca.md` `tmux.md` `shell.md` `subagent.md` |

Your roster and config live outside the repo, so `git pull` never conflicts with them.

## Install

```sh
git clone https://github.com/verno3632/model-router ~/.agents/skills/model-router
mkdir -p ~/.claude/skills && ln -s ~/.agents/skills/model-router ~/.claude/skills/model-router   # if Claude Code should see it
python3 ~/.agents/skills/model-router/limits.py init                # copies roster.md and config.json to ~/.agents/model-router/
```

Then edit the two copied files:

- `roster.md` — replace the author's subscriptions with yours. Keep the section headings, and delete the first line (the "unedited copy" marker) when you are done; until then `where` warns the agent that the roster is not yours.
- `config.json` — list the same products and models, so `limits.py` knows the keys:

```json
{
  "products": {
    "claude": {"models": ["haiku", "sonnet", "opus"]},
    "codex":  {"models": ["sol", "luna"]},
    "swe":    {"models": [], "free_until": "2026-10-10"}
  }
}
```

Launchers are detected: `orca` if installed, `shell`, `tmux` if installed, `subagent`. To force your own order of preference, add `"launchers": ["tmux", "shell"]` — list only what you have. `free_until` marks a product dead after that date. `MODEL_ROUTER_HOME` moves the whole directory.

To make sure the agent reads the skill, add one line to your `CLAUDE.md` / `AGENTS.md`:

```md
Read the `model-router` skill (`~/.agents/skills/model-router/SKILL.md`) before delegating work or choosing a model.
```

Claude Code finds the skill through the symlink. Agents without a skill mechanism (Codex and others) need the path, as above.

Until you run `init`, the skill works from the bundled `examples/` — the author's setup — and tells you so.

## Launchers

| | Needs | Headless | TUI | Other products | Limit recording |
|---|---|---|---|---|---|
| `orca` | [Orca](https://github.com/stablyai/orca) | yes | yes | yes | automatic |
| `tmux` | tmux | yes | yes | yes | automatic |
| `shell` | nothing | yes | no | yes | automatic |
| `subagent` | nothing | — | — | no | by hand |

## limits.py

Standard library only, Python 3.9+. The parent starts children through `run`. `run` checks the records first and refuses to start a dead tier; if the child ends with limit wording, `run` records it and exits 75. A CLI that is not installed also gives 75, without a record. The parent sees 75 and moves to the next tier.

```sh
L=~/.agents/skills/model-router/limits.py
python3 $L where                                    # which home, config, roster and launcher docs are in use
python3 $L status                                   # live or dead, per product and model
python3 $L first swe claude:sonnet codex:luna       # first live key in a chain
python3 $L run --log out.log swe -- devin --model swe-2-medium -p "..."   # start a headless child; --log (before the key) keeps its output
<terminal output> | python3 $L scan codex:astra     # check a TUI child's output
python3 $L mark codex:astra --for 5h                # record by hand (30m / 5h / 3d, max 7d)
python3 $L clear codex:astra
python3 $L budget                                   # weekly quota: surplus / normal / tight
```

A key is `<product>` or `<product>:<model>`. A bare model name works when only one product has it (`opus` → `claude:opus`). A product-level record kills all of that product's models. Records are `{key: expiry in epoch ms}` in `~/.agents/model-router/limits.json`, shared by every session; `MODEL_ROUTER_LIMITS_FILE` overrides the path.

### budget

Graded by pace, not by usage: share of the week elapsed minus share used. +25 points or more is surplus; −15 or less, or 85% used, is tight. Claude is never surplus while its 5-hour window is above 80%. What each grade changes is in your roster's "Budget" section.

- Supported for products named `claude` and `codex`; others are shown as "not graded" and count as normal. Claude's token comes from the macOS Keychain (`Claude Code-credentials`) or `~/.claude/.credentials.json`; Codex's from `~/.codex/auth.json`. Both usage APIs are private and may change — when they cannot be read, the grade is normal.
- Tokens are never written anywhere. The cache holds percentages and timestamps only.
- A quota the API reports as exhausted is copied into the limit records automatically.

### Limits of the approach

- Limit detection is a match on generic wording (`usage limit`, `rate limit`, `429`, `too many requests`, ...), not on each CLI's verified message. When one is missed, `mark` it and add the wording to `LIMIT_RE`.
- Only the last 30 lines are examined. A report that legitimately ends with "rate limit" is still recorded by mistake: `clear` it.
- A TUI child cannot go through `run`. If the parent forgets to `scan`, nothing is recorded.
- Reset times are read only from `try again in 3 hours 12 minutes` and `resets at 3pm`. Otherwise the record lasts 5 hours.

Tests: `python3 -m unittest -v`.
