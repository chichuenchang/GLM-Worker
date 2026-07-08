---
name: glm-worker
description: Use when a task is bounded, mechanical, and high-volume (bulk extraction, translation, pattern refactors across many files, codegen from a template, file-output ETL) and a cheap verification gate exists. Delegates the execution to a GLM-5.2 worker via the glm-mcp server to save orchestrator tokens.
---

# Delegating to the GLM worker

You are the orchestrator. GLM-5.2 is a files-only worker (Read/Write/Edit/Glob/Grep, no shell)
reached through the `delegate_to_glm` MCP tool. Delegating moves bulk token volume to a
cheaper model — but only pays off when you can verify the result cheaply.

## Delegate when ALL hold
- The task is bounded and mechanical (clear, repeatable transformation).
- Success criteria are explicit and expressible in the task text.
- A cheap verification gate exists: tests you can run, or output you can sample-check.
- Volume is high enough that doing it yourself would burn many tokens.

Good fits: extract i18n keys across N files, bulk translate, apply one refactor pattern to many
files, generate boilerplate from a template, file-output ETL.

## Do NOT delegate
- Architectural or cross-file judgment, API design.
- Bug root-cause analysis.
- **Audits, code reviews, consistency checks, bug hunts, multi-category analysis.** These
  are judgment work even though they only touch files — "files-only" does not mean
  "reasoning-free". A project-wide audit once burned the worker's entire 50-turn budget
  with zero output. This rule has no pressure valve: if Claude subagents are unavailable
  (session limit, quota), do the review inline or defer it — never route it to GLM as
  the "only worker left".
- Anything needing project idioms you cannot put into task/context.

If verifying would cost as much as doing it, do it yourself.

## Size shards to the turn budget
The worker runs a hard loop cap (`max_turns`, default 50); each read, edit, glob is a
turn. One over-stuffed delegation dies at the cap with work stranded in context. Shard
instead:
- Rule of thumb: a shard should need well under ~40 turns — for per-file transforms,
  roughly ≤10 files per shard; fewer when files are large or the transform needs
  several edits per file.
- Fan shards out in parallel via the `glm` proxy agent; each gets a fresh budget.
- The server warns the worker when ~10% of turns remain so it flushes partial output,
  but that is a safety net, not a sizing strategy.

## Inline call or `glm` proxy agent
Same worker, two routes — pick by shape:
- **One delegation, mid-conversation** → call `delegate_to_glm` yourself.
- **Fan-out (parallel shards, Workflow stages)** → spawn a `glm` proxy subagent per shard:
  Agent tool `subagent_type: "glm"`, or Workflow `agent(prompt, {agentType: "glm"})`. The
  proxy forwards your prompt as `task` (thinking off by default — the cost lever is already
  pulled), returns the worker's manifest verbatim as its result — you still inspect
  files-changed / assumptions / could-not-do — and keeps per-shard tool traffic out of your
  context. It never edits files itself; on the OFF response it self-enables once, retries
  once, else reports the error verbatim. The agent type is installed alongside this skill
  (`~/.claude/agents/glm.md`); if absent, inline is the only route.

## Mode gate
Before delegating — always before a fan-out — `ping` must report `mode=on` and a configured
server; don't launch shards into an OFF or disconnected worker. The OFF response names its
own fix: `glm_set_mode(mode="on")` once, then one retry. `ERROR:` results are failures, not
toggles — read them, don't blind-retry.

## How to write the task
Give the worker, in `task`: the exact goal, success criteria, file paths/globs, and output
format. Put conventions and related-file pointers in `context`. The worker will not ask you
questions — it makes documented assumptions, so be explicit.

**Require incremental output.** When the task produces an output file (report, manifest,
extraction), the task text MUST say: create the output file first, then append/update it
after EACH file or item processed. Never write "produce X at the end" — if the worker hits
the turn cap, an end-of-run write means total loss; incremental writes mean you keep
everything up to the cap. (The worker's system prompt also enforces this, but the task
text should name the concrete output path and cadence.)

## Model
Default is `glm-5.2`. When the job spans a huge file set that must be held in one worker
context, pass `model="glm-5.2[1m]"` for the 1M-token context variant. The main cost lever is
thinking mode, not model choice.

## Thinking mode
Thinking (chain-of-thought) is ON by default at `reasoning_effort="max"`. For high-volume purely
mechanical batches (bulk rename, format conversion, template stamping), pass `thinking="off"` to
cut completion tokens — the chain-of-thought is billed but never returned to you. Per-call knobs:
`thinking="on"/"off"`, `reasoning_effort="high"/"max"` (GLM-5.2 exposes only these two levels).

## After delegation — ALWAYS verify
The tool returns a manifest (files changed, assumptions, could-not-do, metrics). Do NOT trust the
summary alone:
1. Read a sample of the changed files.
2. Run tests / the build if they exist.
3. Check the "assumptions" and "could not do" sections for anything wrong.

Fail closed: an `ERROR:` result, an OFF response, or a manifest reporting `files_changed=0`
on a task that must change files is a FAILED delegation — re-delegate or do that slice
yourself; never count it done or let it flow downstream.

Only then report success to the user.
