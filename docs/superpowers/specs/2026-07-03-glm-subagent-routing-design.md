# GLM subagent routing — Design Spec

**Date:** 2026-07-03
**Status:** Approved
**Author:** Jim Zheng (with Claude)

## 1. Purpose

Make GLM-5.2 the standing bulk-work subagent: whenever the session agent decides to
spawn a subagent for bounded, mechanical, files-only work, it routes to the GLM worker
automatically (no explicit ask), via a `glm` agent type. DeepSeek is de-routed as the
default worker at the same time.

Scope decision (user): **mechanical-only** routing — reasoning subagents (Explore,
Plan, review, synthesis) stay Claude. Worker choice (user): **GLM only** — remove the
DeepSeek skill/agent from defaults; the deepseek MCP server stays registered but
unrouted.

## 2. Changes

### 2.1 New agent type `~/.claude/agents/glm.md`

Clone of the existing `deepseek.md` thin-proxy pattern:

- Frontmatter: `name: glm`; `tools: mcp__glm__delegate_to_glm, mcp__glm__glm_set_mode,
  ToolSearch`; `model: haiku` (proxy does no reasoning).
- Behavior: load both GLM tools via ToolSearch if absent; call `delegate_to_glm` with
  the task verbatim (`thinking='off'` for pure-mechanical bulk, `'on'` only if the task
  explicitly needs judgment); on the "GLM delegation is OFF" response, one
  `glm_set_mode(mode='on')` + one retry; return the worker manifest verbatim; never do
  file work itself; errors returned verbatim (fail closed).

### 2.2 Global `~/.claude/CLAUDE.md` routing rule

Replace the "Workflow → DeepSeek routing" section with "Subagent/Workflow → GLM
routing":

- Covers both spawn paths: `Agent` tool (`subagent_type: 'glm'`) and Workflow scripts
  (`agent(prompt, {agentType: 'glm'})`).
- Qualification gate unchanged from the DeepSeek rule: bounded AND files-only
  (Read/Write/Edit/Glob/Grep) AND high-volume AND mechanical/low-variance AND cheap
  Claude-side verification exists. Files-only ≠ reasoning-free; judgment work stays
  Claude.
- Fail-closed rules retargeted: route only when `mcp__glm__ping` reports `mode=on`;
  every GLM bulk stage is followed by a Claude verify stage; treat ERROR / OFF / empty
  manifest as stage failure.

### 2.3 DeepSeek de-route (not uninstall)

- Delete `~/.claude/skills/delegate-to-deepseek/` and `~/.claude/agents/deepseek.md`.
- `deepseek-mcp` server registration untouched; `mcp__deepseek__*` tools remain
  manually callable. Rollback = restore the two deleted files (content preserved in
  the deepseek-worker-mcp project repo).

## 3. Error handling

GLM off / disconnected / ERROR → proxy returns the error verbatim → orchestrator does
the work itself or reports; never silently skipped. The proxy never falls back to doing
file work (it has no file tools).

## 4. Testing

- File-level: `glm.md` frontmatter/tool names match the server's actual tool names and
  OFF-message text; CLAUDE.md block references `glm` agent type consistently.
- Live: from a fresh session, `Agent(subagent_type: 'glm')` with a trivial file task
  returns a manifest and the file verifies. (Agent-type list is loaded per session, so
  activation requires a restart.)

## 5. Out of scope

- Hook-based rewriting of `Agent` calls (brittle; would break Explore/Plan).
- Any change to the deepseek-mcp server or its config.
- Blanket routing of all subagents to GLM (rejected during design).
