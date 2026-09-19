# Launcher: subagent

The host agent's built-in subagents (Claude Code's `Agent` tool with a `model` override, Codex's built-in workers, and the like). Nothing to install, and the cheapest way to get a second context — with three limits that decide when it is appropriate.

- **Host product only.** A subagent is another model of the product you are already running in. Any roster row whose point is a *different* product or family (cross-family review, a free tier elsewhere) cannot be served from here.
- **Same quota.** It spends the host product's allowance. It is a way to use a cheaper model for a lookup, not a way around a limit.
- **No limit recording.** `limits.py run` and `scan` cannot wrap it. If a subagent fails with limit wording, record it yourself: `python3 $L mark <product>:<model> --for <time until reset>`.

The four verbs are the host tool's own: starting the subagent is **start**, its final report is **read** and **wait** in one, and **send** exists only if the host lets you message a running subagent. The subagent does not see your conversation, so the task description must stand alone.

Good fits: repository exploration, quick lookups mid-conversation, mechanical sweeps small enough not to need another product. For review it is a last resort — a fresh context but the same family — so when you use it that way, say so in your report.
