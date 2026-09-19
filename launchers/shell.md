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

Review the diff there, merge, then `git worktree remove`.

## Headless commands

Examples only — the roster decides which products exist. Model and effort always go on the command line.

```sh
claude -p '<task>' --model <model> --permission-mode acceptEdits
codex exec -m <model> -c model_reasoning_effort=<effort> '<task>'
devin --model <model> --permission-mode dangerous -p '<task>'
```

A headless child cannot stop to ask for permission, so whatever it may do is granted at launch. Grant edit rights only inside a git worktree with a clean tree, and tell the user before the first such launch in a session. Long prompts go in a file (`--prompt-file`, or `"$(cat brief.md)"`), not inline.
