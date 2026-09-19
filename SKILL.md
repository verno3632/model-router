---
name: model-router
description: Decide which AI product and model gets a piece of work (planning, fact-checking, research, implementation, UI, review) and how to launch it. Read before delegating to another agent or model, before choosing a model, and when a product hits its usage limit.
---

# Model routing

Precedence: the user's instruction → the project's AGENTS.md / CLAUDE.md → this skill. A project's choice applies inside that project only; never carry it to another project.

This file holds what is true for everyone. Who does what is in the **roster**; how a child session is started is in a **launcher** doc. Both are chosen per user.

## Start here

`limits.py` sits next to this file. Set `L` to its path, then ask where everything is:

```sh
L=<directory of this SKILL.md>/limits.py
python3 $L where
# home      ~/.agents/model-router
# config    .../config.json
# roster    .../roster.md
# launcher  .../launchers/orca.md      (one line per available launcher, in order of preference)
```

1. Read the `roster` file. It is the only source of truth for models, effort levels, fallbacks and boundaries.
2. Read a launcher doc when you are about to start a child — not before.
3. If `roster` points into this skill's `examples/` directory, the user has not set up their own. Use it, and tell them once that `python3 $L init` copies editable versions into `home`.

## Principles

- **Expensive models think; cheap models type.** Design, work that needs eyes on a screen, and cross-family review go to the top tier. Writing, fixing and mass production go to the roster's cheapest capable tier.
- **For new implementation, ask first whether the cheapest tier is enough.** If it is, open nothing else. Lint, typos, spacing and simple tests never spend top-tier quota.
- **Promote after two failures of the same kind,** not before, and do not keep re-running the same ticket at maximum effort.
- **Whoever wrote it does not review it.** The model that planned does not implement or review. Review goes to a different model family in a fresh session.
- **UI: a model that can see decides the look first,** from a reference image, an existing screen or a screenshot — add one if the brief is text only. Once the look is fixed, the cheap tier turns it into CSS and components.
- **Conversations do not transfer.** Only work that is complete in its task description goes out.
- **Set model and effort in the launch arguments and confirm the actual model from the child's output.** Saving a setting is not switching.
- **Never substitute silently.** If the first choice is unavailable, do not stop — walk the roster's fallbacks and report where the work went. Do not use a model the roster does not list.

## Choosing

1. Find the row in the roster's role table that matches the work. Rows are in pipeline order.
2. Check the roster's boundary lists: some work never goes to the cheap tier, even as a fallback.
3. Before planning, reviewing or promoting, check the weekly budget (below) and apply the roster's budget table.
4. Turn the row into keys — `<product>` or `<product>:<model>`, as the roster's key legend spells them — and let `limits.py first` pick the first live one (see "When a tier is dead").

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

Use the first launcher `where` lists that can carry the child, unless the roster says otherwise. Children that edit files get their own git worktree so parallel children cannot collide.

- A **headless** child (runs a prompt and exits) is always wrapped: `python3 $L run [--log <file>] <key> -- <command>`. Its final output is its report; `--log` keeps a copy where the launcher would lose it.
- A **TUI** child holds the terminal, so it cannot be wrapped. Pick its tier with `first` before starting it; when it looks stuck, pipe **read** into `python3 $L scan <key>`.

## Weekly budget

`python3 $L budget` grades each product's weekly quota by pace (share of the week elapsed − share used): **surplus**, **normal** or **tight**. When numbers cannot be fetched the grade is normal. Results are cached for 5 minutes and the decision is remade on every delegation, so an upgrade that burns quota undoes itself.

The roster's budget table says what each grade changes. Surplus is spent on thinking roles; implementation and mass production stay on the cheap tier regardless. A model-specific line in the output is graded separately: treat that model by the worse of its own grade and its product's.

## When a tier is dead

Start every child through `limits.py` so limits are seen before launch and recorded when hit.

```sh
python3 $L status                                  # every product and model
python3 $L first swe claude:sonnet codex:luna      # first live key in a fallback chain
python3 $L run swe -- <headless command>           # refuses to start a dead tier; records a limit hit
<read> | python3 $L scan codex:astra               # TUI child: check its output for a limit message
python3 $L mark codex:astra --for 5h               # record by hand (30m / 5h / 3d, max 7d, default 5h)
python3 $L clear codex:astra                       # it came back early
```

- `run` and `scan` exit **75** when the tier is limited — whether it was already recorded or the child just hit it. **On 75, move to the next key.** Any other exit code is the child's own.
- Order of retreat: the same tool one step down (the roster's "demote" column), then the "fallback" column left to right, skipping dead keys.
- A product key (`codex`) means the whole product is limited and kills all its models; a model key (`codex:astra`) kills only that model. `--for` takes the time until the reset shown in the error.
- Records live in `home`, are shared by every session, and expire on their own. They are written only by `run`, `scan`, `mark` and `budget`, so do not start children any other way.
- `run` and `scan` look at the last 30 lines only. If a report legitimately ends with the words "rate limit", the record is wrong: `clear` it.
