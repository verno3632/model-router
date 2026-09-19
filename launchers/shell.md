# Launcher: shell

No extra tools: the child is a background process with a log file. Headless children only — a TUI needs a terminal, so use `tmux` or `orca` for those. `$L` is `limits.py`, as in `SKILL.md`.

If your host has its own background-command facility (Claude Code: `Bash` with `run_in_background`; others: their equivalent), use it for **start** / **read** / **wait** and keep only the wrapped command from this page. Otherwise:

```sh
# start — the handle is the log path
LOG=$(mktemp -t mr-child.XXXXXX)
( cd <worktree> && python3 $L run <key> -- <command>; echo $? > "$LOG.exit" ) > "$LOG" 2>&1 &

tail -n 40 "$LOG"                                       # read
until [ -f "$LOG.exit" ]; do sleep 5; done; cat "$LOG.exit"   # wait → exit code (75 = limited)
```

**send** does not exist here. Put the whole task in the prompt. For a follow-up, start a new headless run with the product's resume flag (check its `--help`; commonly `--continue` / `resume`).

## Isolation

A child that edits files gets its own checkout, so parallel children and your own session cannot collide:

```sh
git -C <repo> worktree add ../<repo>-<task> -b <task>
```

`<task>` is any short slug. Briefs, logs and `.exit` files go outside the repo (a temp directory), so they never show up in a diff. You, the director, read the diff. Merge and `git worktree remove` only when the user asked for the change to land; otherwise leave the branch and report its name.

## Headless commands

Examples only — the roster decides which products exist. Model and effort always go on the command line.

```sh
claude -p --model <model> --effort <effort> --permission-mode acceptEdits < brief.md
codex exec -m <model> -c model_reasoning_effort=<effort> - < brief.md
devin --model <model> --permission-mode dangerous --prompt-file brief.md -p
```

`run` passes its stdin to the child, so the wrapped form is simply `python3 $L run <key> -- claude -p --model <model> ... < brief.md`. The roster's effort words (Low / Medium / High) are not the CLI's: check `--help` for the accepted values. A child that only reviews gets no edit rights (`claude --permission-mode plan`, `codex exec -s read-only`).

A headless child cannot stop to ask for permission, so whatever it may do is granted at launch. Grant edit rights only inside a git worktree with a clean tree, and tell the user before the first such launch in a session — telling is enough; wait for an answer only if their own rules ask for one. Prompts go in a file, not inline: pass it by `--prompt-file` where the CLI has one, otherwise on stdin (`claude -p --model <model> ... < brief.md`). An inline prompt breaks on quotes, and a flag that takes several values (`--allowedTools a b c`) swallows a prompt placed after it — the child then fails at once with "no input". After **start**, **read** once to confirm the child is actually running.
