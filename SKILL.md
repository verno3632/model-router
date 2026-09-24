---
name: model-router
description: Decide which AI product and model gets a piece of work (planning, fact-checking, research, implementation, UI, review) and how to launch it. Read before delegating to another agent or model, before choosing a model, and when a product hits its usage limit.
---

# Model routing

Precedence: the user's instruction → the project's AGENTS.md / CLAUDE.md → this skill. A project's choice applies inside that project only; never carry it to another project. An instruction that names a product or model wins even when the roster does not list it — "never substitute silently" below limits your own choices, not the user's. If `limits.py` has no key for it, start that child unwrapped, say so, and suggest adding it to `config.json`.

This file holds what is true for everyone. Who does what is in the **roster**; how a child session is started is in a **launcher** doc. Both are chosen per user.

## Start here

`limits.py` sits next to this file. Set `L` to its path, then ask where everything is:

```sh
L=~/.agents/skills/model-router/limits.py    # wherever this SKILL.md is; limits.py is in the same directory
python3 $L where
# home      ~/.agents/model-router
# config    .../config.json
# roster    .../roster.md
# launcher  <this skill>/launchers/shell.md   (one line per launcher, in order of preference)
```

1. Read the `roster` file. It is the only source of truth for models, effort levels, fallbacks and boundaries. This file refers to its sections by heading: **Keys**, **Priority**, **Launchers**, **Roles**, **Cheap-tier boundary**, **Budget**, **Formations**.
2. Read a launcher doc when you are about to start a child — not before.
3. If `roster` points into this skill's `examples/` directory, the user has not set up their own: use it, and tell them once that `python3 $L init --minimal` (or `init` for the author's full example) copies editable versions into `home`. If `where` warns that the roster is an unedited copy, it still describes the skill author's subscriptions, not the user's: tell them once, and expect rows naming products they do not have — those fall through as unavailable.

## Principles

- **Expensive models think; cheap models type.** Design, work that needs eyes on a screen, and cross-family review go to the top tier. Writing, fixing and mass production go to the roster's cheapest capable tier.
- **For new implementation, ask first whether the cheapest tier is enough.** If it is, open nothing else. Lint, typos, spacing and simple tests never spend top-tier quota.
- **Promote after two failures of the same kind,** not before, and do not keep re-running the same ticket at maximum effort.
- **Whoever wrote it does not review it.** The model that planned does not implement or review. Review goes to a different model family in a fresh session.
- **UI: a model that can see decides the look first,** from a reference image, an existing screen or a screenshot — add one if the brief is text only. Once the look is fixed, the cheap tier turns it into CSS and components.
- **Conversations do not transfer.** Only work that is complete in its task description goes out.
- **Set model and effort in the launch arguments and confirm the actual model from the child's output.** Saving a setting is not switching. When a CLI prints no model name, say in your report that the model is unconfirmed. A product with a single model takes no model argument: confirm what it picked from its output.
- **Never substitute silently.** If the first choice is unavailable, do not stop — walk the roster's fallbacks and report where the work went. Do not use a model the roster does not list.

## Choosing

1. Find the row in the roster's **Roles** table that matches the work. Rows are in pipeline order, not a checklist: small work whose brief you can write yourself skips the plan rows, and your brief is the plan. Whoever wrote the brief still does not implement or review it.
2. Check the roster's **Cheap-tier boundary**: some work never goes to the cheap tier, even as a fallback.
3. Before planning, reviewing or promoting, check the weekly budget (below) and apply the roster's **Budget** table.
4. Turn the row into keys — `<product>` or `<product>:<model>`, as the roster's **Keys** table spells them — and let `limits.py first` pick the first live one (see "When a tier is dead").

## Launching a child

Every launcher doc implements the same four verbs:

| Verb | Meaning |
|---|---|
| **start** | run a command in a directory; get a handle |
| **read** | recent output of a handle |
| **wait** | block until the handle exits; get its exit code |
| **send** | type text into a live handle |

| Launcher | Headless child | TUI child | Other products | Limit recording |
|---|---|---|---|---|
| `orca` | yes | yes | yes | `run` / `scan` |
| `tmux` | yes | yes | yes | `run` / `scan` |
| `shell` | yes | no | yes | `run` |
| `subagent` | — | — | no, host product only | none; `mark` by hand |

Use the first launcher `where` lists that can carry the child. The roster's **Launchers** section overrides this: it may restrict a launcher to certain roles or forbid it, and the launcher docs never widen what the roster allows. Children that edit files get their own git worktree so parallel children cannot collide.

- A **headless** child (runs a prompt and exits) is always wrapped: `python3 $L run [--log <file>] <key> -- <command>`. Its final output is its report; `--log` keeps a copy where the launcher would lose it.
- A **TUI** child holds the terminal, so it cannot be wrapped. Pick its tier with `first` before starting it; when it looks stuck, pipe **read** into `python3 $L scan <key>`.

## Weekly budget

`python3 $L budget` grades each product's weekly quota by pace (share of the week elapsed − share used): **surplus**, **normal** or **tight**. When numbers cannot be fetched, and for products with no usage source (shown as "not graded"), the grade is normal. Results are cached for 5 minutes and the decision is remade on every delegation, so an upgrade that burns quota undoes itself.

The roster's budget table says what each grade changes. Surplus is spent on thinking roles; implementation and mass production stay on the cheap tier regardless. A model-specific line in the output is graded separately: treat that model by the worse of its own grade and its product's.

## When a tier is dead

Start every child through `limits.py` so limits are seen before launch and recorded when hit. The keys below are examples; yours are in the roster's **Keys** table.

```sh
python3 $L status                                  # every product and model
python3 $L first swe claude:sonnet codex:luna      # first live key in a fallback chain
python3 $L run swe -- <headless command>           # refuses to start a dead tier; records a limit hit
<read> | python3 $L scan codex:astra               # TUI child: check its output for a limit message
python3 $L mark codex:astra --for 5h               # record by hand (30m / 5h / 3d, max 7d, default 5h)
python3 $L clear codex:astra                       # it came back early
```

- `run` and `scan` exit **75** when the tier is unavailable: already recorded as limited, limited just now, or its CLI is not installed. **On 75, move to the next key.** Any other exit code from `run` is the child's own.
- `run` records a limit only when the child exits non-zero. A child that exits 0 is working, even if its report talks about rate limits; `run` then only warns on stderr. If that child's output really was a limit message, `mark` it.
- `first` exits **1** with no output when every key in the chain is dead. Do not start the work; report to the user which tiers are dead and until when (`status`). It skips keys the config does not know, with a warning.
- Exit **2** is a usage or configuration error. `run`, `scan`, `mark` and `clear` give it for a key `config.json` does not know; `first` only warns and skips such keys, so read its warnings when it exits 1. Tell the user what to fix; do not guess another key.
- Order of retreat: the same tool one step down (the roster's "demote" column), then the "fallback" column left to right, skipping dead keys.
- A product key (`codex`) means the whole product is limited and kills all its models; a model key (`codex:astra`) kills only that model. `--for` takes the time until the reset shown in the error.
- Records live in `home`, are shared by every session, and expire on their own. They are written only by `run`, `scan`, `mark` and `budget`, so do not start children any other way. Each write is logged with its origin; `status` shows who wrote each live record (command, matched line, exit code, cwd, and the session that ran it).
- `run` and `scan` look at the last 30 lines only. A hit is recorded until the reset time if the output states one, otherwise for 5 hours. A record you doubt: read its origin in `status`, try the tier with a one-line prompt, and `clear` it if it answers.
