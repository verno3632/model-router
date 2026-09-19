# Launcher: tmux

Each child is a detached tmux session; the session name is the handle. Carries both headless and TUI children. `$L` is `limits.py`, as in `SKILL.md`. Children that edit files get their own git worktree — see "Isolation" in `shell.md`.

| Verb | Command |
|---|---|
| start | `tmux new-session -d -s mr-<task> -c <worktree> "sh -c '<command>; echo \$? > <worktree>/.mr-<task>.exit'"` |
| read | `tmux capture-pane -p -t mr-<task> -S -200` |
| wait | `until [ -f <worktree>/.mr-<task>.exit ]; do sleep 5; done; cat <worktree>/.mr-<task>.exit` → exit code |
| send | `tmux send-keys -t mr-<task> -l '<text>'` then `tmux send-keys -t mr-<task> Enter` |

- The session disappears when the child exits, taking its screen with it. The exit code therefore goes to a file, and a headless child's report to `run --log`. A child that dies at once — `run` refusing a dead tier with 75 — is caught the same way. Delete the `.exit` file afterwards; a TUI child's session is closed with `tmux kill-session -t mr-<task>`.
- `<command>` sits inside two layers of quotes. Keep prompts out of it: put the task in a file and pass it by `--prompt-file` or stdin (see `shell.md`).
- `send-keys -l` sends the text literally; without it tmux interprets words like `Enter` or `C-c` inside your text as keys. Send `Enter` as a separate call.
- The user can watch or take over with `tmux attach -t mr-<task>`. Say so when you start a long-running child.

## Children

- Headless: `<command>` is `python3 $L run --log <file> <key> -- <product CLI> ...` (command examples in `shell.md`). `--log` goes before the key. Read the report from `<file>`. Exit code 75 means unavailable: move to the next key.
- TUI: pick the tier with `python3 $L first ...`, start the CLI with explicit model and effort arguments, **read** until its input box is on screen, then **send** the task. When it looks stuck: `tmux capture-pane -p -t mr-<task> -S -200 | python3 $L scan <key>`.
