# Launcher: Orca

Children run as terminals inside Orca worktrees. Read the `orca-cli` skill first (`orca skills get orca-cli`); read `orchestration` too when you will supervise several workers. `$L` is `limits.py`, as in `SKILL.md`.

| Verb | Command |
|---|---|
| start, new worktree | see below |
| start, existing worktree | `orca terminal create --worktree <selector> --command '<command>' --json` |
| read | `orca terminal read --terminal <handle>` |
| wait | `orca terminal wait --terminal <handle> --for exit --timeout-ms <ms> --json` (`--for tui-idle` for a TUI child) |
| send | `orca terminal send --terminal <handle> --text '<text>' --enter` |

## Starting in a new worktree: one tab, not two

A bare `orca worktree create` opens one empty shell (`Terminal 1`). Adding `terminal create --command` on top leaves that shell behind as a useless second tab. Start the child **in** the empty shell instead:

```sh
orca worktree create --repo id:<repoId> --name <task> --no-parent --json      # note result.worktree.path
orca terminal list --worktree 'id:<repoId>::<path>' --json                    # the one terminal is the empty shell
orca terminal send --terminal <handle> --text 'exec <command>' --enter
orca terminal rename --terminal <handle> --title '<task>'
```

- `exec` replaces the shell with the child, so the child's exit — including exit code 75 from `limits.py run` — ends the terminal and `wait` sees it.
- When Orca's built-in launcher is enough (no custom model or effort arguments), `orca worktree create --agent <id> --prompt '<task>'` puts the agent in the first terminal and needs none of this.
- A repo with `defaultTabs` in its `orca.yaml` gets those tabs instead of the empty shell. They may run real commands; never close or reuse one without checking it is an idle shell.

## Children

- Headless: `<command>` is `python3 $L run --log <file> <key> -- <product CLI> ...`. Orca drops a terminal's output once the child exits, so without `--log` the child's final report is gone by the time `wait` returns. Read the report from `<file>`.
- TUI: pick the tier with `python3 $L first ...`, start the CLI with explicit model and effort arguments, **wait** for `tui-idle`, then **send** the task. When it looks stuck: `orca terminal read --terminal <handle> | python3 $L scan <key>`.

If the Orca runtime is unreachable (`orca status`), move to the next launcher `where` lists — or to whatever the roster names for that case — and report that you fell back.
