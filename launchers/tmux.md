# Launcher: tmux

Each child is a detached tmux session; the session name is the handle. Carries both headless and TUI children. `$L` is `limits.py`, as in `SKILL.md`. Children that edit files get their own git worktree — see "Isolation" in `shell.md`.

| Verb | Command |
|---|---|
| start | `tmux new-session -d -s mr-<task> -c <worktree> '<command>'` then `tmux set-option -t mr-<task> remain-on-exit on` |
| read | `tmux capture-pane -p -t mr-<task> -S -200` |
| wait | poll `tmux display-message -p -t mr-<task> '#{pane_dead} #{pane_dead_status}'` until it starts with `1`; the second field is the exit code |
| send | `tmux send-keys -t mr-<task> -l '<text>'` then `tmux send-keys -t mr-<task> Enter` |

- `remain-on-exit` keeps the pane after the child exits, so the last output and the exit code stay readable. Clean up with `tmux kill-session -t mr-<task>` once you have them.
- `send-keys -l` sends the text literally; without it tmux interprets words like `Enter` or `C-c` inside your text as keys. Send `Enter` as a separate call.
- The user can watch or take over with `tmux attach -t mr-<task>`. Say so when you start a long-running child.

## Children

- Headless: `<command>` is `python3 $L run <key> -- <product CLI> ...` (command examples in `shell.md`; add `--log <file>` if you will kill the session before reading). Exit code 75 in `pane_dead_status` means limited: move to the next key.
- TUI: pick the tier with `python3 $L first ...`, start the CLI with explicit model and effort arguments, **read** until its input box is on screen, then **send** the task. When it looks stuck: `tmux capture-pane -p -t mr-<task> -S -200 | python3 $L scan <key>`.
