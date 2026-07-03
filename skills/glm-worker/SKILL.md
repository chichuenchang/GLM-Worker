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

Only then report success to the user.
