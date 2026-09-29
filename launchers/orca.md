# Launcher: Orca

Children run as terminals inside Orca worktrees. Read the `orca-cli` skill first (`orca skills get orca-cli`); read `orchestration` too when you will supervise several workers. `$L` is `limits.py`, as in `SKILL.md`.

| Verb | Command |
|---|---|
| start, new worktree | see below |
| start, existing worktree | `orca terminal create --worktree <selector> --command '<command>' --json` |
| read | `orca terminal read --terminal <handle>` |
| wait | `orca terminal wait --terminal <handle> --for exit --timeout-ms <ms> --json` (`--for tui-idle` for a TUI child) |
| send | `orca terminal send --terminal <handle> --text '<text>' --enter` |
| cleanup | `orca terminal close --terminal <handle> --json`, then `orca worktree rm --worktree <selector> --json` — see below |

## Starting in a new worktree: one tab, not two

A bare `orca worktree create` opens one empty shell (`Terminal 1`). Adding `terminal create --command` on top leaves that shell behind as a useless second tab. Start the child **in** the empty shell instead:

```sh
orca worktree create --repo id:<repoId> --name <task> --parent-worktree path:<your worktree> --json   # note result.worktree.path
orca terminal list --worktree 'id:<repoId>::<path>' --json                    # the one terminal is the empty shell
orca terminal send --terminal <handle> --text 'exec <command>' --enter
orca terminal rename --terminal <handle> --title '<task>'
```

- `--parent-worktree` makes the new worktree a child of the worktree you run in (`git rev-parse --show-toplevel`), so Orca nests your children under you. Orca guesses the parent from the terminal or cwd only when it can, so pass it every time.
- `exec` replaces the shell with the child, so the child's exit — including exit code 75 from `limits.py run` — ends the terminal and `wait` sees it.
- When Orca's built-in launcher is enough (no custom model or effort arguments), `orca worktree create --agent <id> --prompt '<task>'` (with the same `--parent-worktree`) puts the agent in the first terminal and needs none of this.
- A repo with `defaultTabs` in its `orca.yaml` gets those tabs instead of the empty shell. They may run real commands; never close or reuse one without checking it is an idle shell.

## Children

- Headless: `<command>` is `python3 $L run --log <file> <key> -- <product CLI> ...`. Orca drops a terminal's output once the child exits, so without `--log` the child's final report is gone by the time `wait` returns. Read the report from `<file>`.
- TUI: pick the tier with `python3 $L first ...`, start the CLI with explicit model and effort arguments, **wait** for `tui-idle`, then **send** the task. When it looks stuck: `orca terminal read --terminal <handle> | python3 $L scan <key>`.

## Cleanup: close the tab when the child reports, remove the worktree when its branch lands

Every child you start leaves a tab, and every new worktree leaves a sidebar entry. Both stay until you remove them, so they pile up run after run.

**Tab.** Once you have the child's final report on record (the `--log` file, or the last message copied into your notes or the issue), close its tab in the same turn: `orca terminal close --terminal <handle> --json`. Closing drops the terminal's output, so the report must be saved first. This covers every tab you opened: reviewers and retries in a child's worktree, and children you started in the main checkout, which never get a worktree removal. A child you will send more work to keeps its tab.

**Worktree.** After you merge a child's branch into the base, remove the child's worktree in the same turn. Use `orca worktree rm`, never `git worktree remove`: the git command leaves the worktree registered in Orca as a stale sidebar entry.

```sh
git -C <repo> merge-base --is-ancestor <branch> <base>         # exit 0 = landed
git -C <worktree> status --porcelain --ignored                  # anything worth keeping?
orca worktree rm --worktree 'id:<repoId>::<path>' --json
```

- `rm` also deletes the local branch when Orca can prove it merged. After a squash or rebase merge it cannot, so the branch stays; delete it with `git branch -D <branch>` once you have checked the change is on the base.
- Check the `--ignored` output before removing. Generated output, local data and save files live there and are not in git. If any of it matters, copy it out or leave the worktree and report it.
- Do not pass `--force` to get past uncommitted changes. Look at them first; `--force` is for changes you have decided to discard.
- A child whose branch was not merged keeps its worktree. Report the branch name instead, as in `shell.md`.

**Sweep.** Before you report a milestone or a batch of delegations as finished, run `orca worktree ps --json` and account for every worktree and tab you created: each one is removed, or kept with its reason written in the issue. `python3 <this skill>/launchers/orca_sweep.py` lists that classification in one table, and `--remove-landed` removes only the ones safe to delete.

If the Orca runtime is unreachable (`orca status`), move to the next launcher `where` lists — or to whatever the roster names for that case — and report that you fell back.
