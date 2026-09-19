# Roster

A minimal starting point: two subscriptions, Claude Code and Codex, no other tools. Rewrite it for what you pay for. Keep the section headings — `SKILL.md` refers to them — and keep `config.json` in step with the keys used here. Model names below are placeholders for "your cheapest / middle / best"; write the real names your plan offers.

## Keys

| Product | Key | Model keys |
|---|---|---|
| Claude Code | `claude` | `claude:haiku` `claude:sonnet` `claude:opus` |
| Codex | `codex` | — (one model: start it without a model argument and read the model name from its output header. If your plan has several, list them here and in `config.json`) |

## Priority

**Claude → Codex.** State here why: which quota is larger, which is nearly spent, which model you are saving for what. Every fallback column below follows this order.

## Launchers

- Use whatever `limits.py where` lists, in that order.
- The `subagent` launcher may serve the "Repository exploration" and "Mechanical work" rows. It may not serve review rows while the other product is alive: review needs a different family.

## Roles

Pipeline order: plan → plan review → implement → review.

| Role | First choice | Effort | Demote (same tool) | Fallback (other products) |
|---|---|---|---|---|
| Direction and supervision | Whoever holds the conversation | High | — | — |
| Requirements, design, plan | Claude × Opus | High | Sonnet | Codex, high effort |
| Plan review | Codex, high effort | High | — | Claude × Opus (fresh session) |
| Fact-checking and research | Claude × Sonnet | Medium | Haiku | Codex |
| Repository exploration (quick checks mid-conversation) | Claude × Haiku subagent | Low | — | Codex |
| Ordinary implementation | Claude × Sonnet | Medium | Haiku for trivial edits | Codex |
| Hard implementation, stuck debugging | Claude × Opus | High | Sonnet | Codex, high effort |
| Mechanical work (lint, adding tests, bulk edits) | Claude × Haiku, in parallel when there is volume | Low | — | Codex, low effort |
| UI look (needs a screenshot or reference image) | Claude × Opus | High | Sonnet | Codex, high effort |
| Implementation review | Codex, high effort — when Claude wrote it. Claude × Opus (fresh session) when Codex wrote it | High | — | the same product in a fresh session; say so in the report |

## Cheap-tier boundary

The cheap tier here is Claude × Haiku.

**Send:** lint, typos, formatting, simple tests, renames, mechanical bulk edits, reading and summarising code.

**Never send** (not even as a fallback): design and planning, any review, the first draft of a UI look, work where what to build is still undecided, judgement calls on authentication, authorization, billing, DB migration, security boundaries or data integrity.

## Budget

| Claude is | Design / plan | Ordinary implementation | Implementation review |
|---|---|---|---|
| surplus | Opus, highest effort | as the role table | add a second review by Claude × Opus (fresh session) |
| normal | as the role table | as the role table | as the role table |
| tight | Sonnet; Opus only for decisions that are expensive to reverse | Codex first | as the role table |

- Codex tight: plan review goes to Claude × Opus (fresh session); keep Codex for implementation review only.

## Formations

- Claude entirely dead → every row goes to Codex; reviews run in a fresh Codex session, and the report says the review was same-family.
- Codex entirely dead → reviews run in a fresh Claude session with a different model from the author's, and the report says so.
- Both dead → stop and tell the user when the first one comes back (`limits.py status`).
