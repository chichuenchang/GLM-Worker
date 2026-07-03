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
- Anything needing project idioms you cannot put into task/context.

If verifying would cost as much as doing it, do it yourself.

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
