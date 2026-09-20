# Roster

A worked example: the skill author's subscriptions as of 2026-09. Copy it with `limits.py init` and rewrite it for the products you pay for. Keep the section headings — `SKILL.md` refers to them — and keep `config.json` in step with the keys used here.

## Keys

| Product | Key | Model keys |
|---|---|---|
| Devin CLI (SWE-2) | `swe` | — (effort is chosen by model name: `swe-2-medium` / `-high` / `-max`) |
| Claude Code | `claude` | `claude:haiku` `claude:sonnet` `claude:opus` `claude:fable` |
| Codex | `codex` | `codex:astra` `codex:sol` `codex:luna` `codex:terra` |
| Grok | `grok` | — |

## Priority

**Devin → Claude → Codex → Grok.** Every fallback column below is in this order.

- SWE-2 is free until 2026-10-10: use it as much as possible. After that date `limits.py` reports `swe` dead; treat SWE-2 as gone and ask the user to revise this roster.
- Codex: keep Astra for UI. For anything else use Sol or Luna first.
- Grok is on a low plan and close to its cap, so it comes last. Exception: research on X, which only Grok can see.

## Launchers

- The entry point for delegation is an independent CLI session under Orca. Devin / SWE-2 is started there too. Codex's built-in subagents and direct headless launches are not entry points.
- The `subagent` launcher is allowed for the "Repository exploration" row only.
- If Orca itself is unavailable, use the `agent-relay` / `swe-relay` skills and report that you fell back.
- Important reviews state model and effort explicitly, e.g. `codex -c 'model="gpt-5.6-sol"' -c 'model_reasoning_effort="high"' review`.

## Roles

Pipeline order: plan → plan review → implement → UI look → UI production → implementation review. If doubt remains after review, run one more pass with Codex × Sol High.

| Role | First choice | Effort | Demote (same tool) | Fallback (other products) |
|---|---|---|---|---|
| Direction and supervision (splitting, monitoring) | Whoever holds the conversation. On Codex: Terra High | High | — | — |
| Requirements, design, plan | Claude Code × Fable 5.1 | High / xHigh | Opus 5 High | Codex × Sol High → Astra High |
| Plan review | Codex × Astra High | High | Sol High | Claude × Opus 5 (fresh session) |
| Fact-checking (real code, existing tests, official docs) | Devin × SWE-2 Medium | Medium | — | Claude × Sonnet 5 → Codex × Luna max |
| External / web research (library comparison, material for a technical choice, official docs, case studies). The conclusion stays with the conversation holder | Devin × SWE-2 Medium. Run Grok 4.6 alongside for anything on X | Medium | SWE-2 High | Claude × Sonnet 5 → Codex × Luna. If Grok is dead, skip the X part and say so |
| Repository exploration (quick checks mid-conversation) | Claude × Haiku 4.5 subagent | Low | Sonnet 5 | Devin × SWE-2 Medium → Codex × Luna |
| Ordinary implementation | Devin × SWE-2 Medium | Medium | promote to SWE-2 High | Claude × Sonnet 5 → Codex × Luna Extra High → Grok 4.6 |
| Hard implementation | Devin × SWE-2 High / Max | High. Cross-cutting work and migrations: Max | SWE-2 Medium | Claude × Opus 5 → Codex × Sol High → Grok 4.6 |
| Stuck debugging | Devin × SWE-2 High | High. Max after two failures | — | Claude × Fable 5.1 → Codex × Sol High → Astra High |
| Mechanical mass production, boilerplate, parallel sweeps (lint, adding unit / integration tests, scanning) | Devin × SWE-2 Medium, in parallel when there is volume | Medium | — | Claude × Haiku 4.5 (Sonnet 5 if not enough) → Codex × Luna → Grok 4.5 / 4.6 |
| Overnight / asynchronous tickets | Devin × SWE-2 High alone | High | Fusion (Fable Medium + SWE-2) only when the plan is vague | Claude × Sonnet 5 → Codex × Luna Extra High → Grok 4.6 |
| UI look (screenshots, browser QA, reproducing a reference / mock / existing screen, landing pages, spatial / 3D / motion) | Codex × Astra High | High | Sol High | Claude × Fable 5.1 / Opus 5 (screenshot required) → Grok 4.6 |
| UI / E2E tests (Playwright etc.): the first spec for a screen or flow — choosing selectors and assertions against the running app — plus triage of flaky or failing runs and approval of snapshot baselines | Codex × Astra High | High | Sol High | Claude × Fable 5.1 / Opus 5 (trace or screenshot required) → Grok 4.6 |
| UI / E2E tests: further specs that follow an existing one | Devin × SWE-2 High | High | — | Claude × Sonnet 5 → Codex × Luna Extra High |
| UI: CSS production once the look is fixed | Devin × SWE-2 Medium | Medium | SWE-2 High | Claude × Sonnet 5 → Codex × Luna |
| Implementation review | Claude Code × Opus 5 (fresh session) | Medium–High | Sonnet 5 | Codex × Sol High → Grok 4.6 |

Pairings for review: UI written by Astra is reviewed by Opus 5; work written by Opus is reviewed by Sol High. SWE-2 never reviews its own work. E2E specs written by SWE-2 are reviewed for false greens: weakened assertions, fixed sleeps, skipped steps. A ticket that failed twice on SWE-2 goes up to Fable or Sol High.

## Cheap-tier boundary (SWE-2)

**Send:** implementation with a settled spec, refactoring, type fixes, adding unit / integration tests, E2E specs that follow an existing spec, lint, dependency bumps, simple bugs, turning Astra's UI decisions into CSS / components, overnight tickets, parallel mechanical work, bulk API or type replacement, fixtures and mocks, accessibility, responsive layout, unifying logs and config values, migration scripts, documentation upkeep, gathering material for external / web research.

**Never send** (not even as a fallback): design and planning, plan review, implementation review, screenshot diffs, browser QA, the first E2E spec for a screen or flow, triage of flaky E2E runs, approval of snapshot baselines, Figma work, the first draft of a look from a reference, 3D / motion / spatial UI, work where what to build is still undecided, judgement calls on authentication, authorization, billing, DB migration, security boundaries or data integrity.

## Budget

Even in surplus, implementation, mass production, fact-checking and CSS production stay on SWE-2. Astra stays UI-only even when Codex is in surplus. Devin and Grok are not graded.

| Claude is | Design / plan | Promotion from SWE-2 | Stuck debugging | Implementation review | Repository exploration |
|---|---|---|---|---|---|
| surplus | Fable xHigh as a matter of course | after 1 failure, to Claude | after SWE-2 High fails once, go to Fable (skip Max) | Opus 5 High, fixed | Sonnet 5 |
| normal | as the role table | as the role table | as the role table | as the role table | as the role table |
| tight | Opus 5 High | after 2 failures, to Codex × Sol High | fallback tries Sol High first | Sonnet 5; Opus 5 for important diffs only | Haiku 4.5 |

- Codex in surplus: always follow implementation review with a second review by Sol High.
- Codex tight: send plan review to Claude × Opus 5 (fresh session) and keep Astra for UI only.

## Formations

- SWE-2 entirely dead → each row's fallback column.
- Fusion's Fable lead runs out → SWE-2 alone.
- Astra and Fable dead at the same time → second formation: SWE-2 + Opus 5.
