# glm-worker-mcp

An MCP server that lets **Claude orchestrate** and a **GLM-5.2 worker** do bounded,
mechanical file work. Claude delegates a sub-task through one tool call; GLM runs its own
multi-turn loop over a **files-only** tool set and returns a structured, verifiable manifest.
The point: move bulk token volume to a cheaper model while Claude keeps planning + verification.

Same spec as deepseek-worker-mcp (a sibling local project) with GLM-5.2 (z.ai) as the backend.

## Security model

The worker has **no shell and no code execution** — only Read, Write, Edit, Glob, Grep. Because
nothing executes, the path jail (`resolve()` + `relative_to(workspace)`) is a *complete*
filesystem boundary: there is no write-a-script-then-run-it escape, which is the vector that makes
blacklist-based sandboxes leak. Symlinks pointing outside the workspace are filtered.

**Residual risk you accept:** file contents the worker reads are sent to z.ai's API
(`api.z.ai`). Don't point it at secrets you don't want transmitted. The `denylist`
(default `[".git"]`) refuses matching paths — extend it with secret patterns
(e.g. `.env*`, `*.pem`); an explicit `"denylist": []` opts out entirely.

## Install

```powershell
# Windows
./install.ps1
```
```bash
# macOS / Linux
./install.sh
```

**Requires Python 3.10+.** The installer creates a `.venv`, installs the package, writes a config template at
`~/.glm-mcp/config.json` (asks which platform your key is from — z.ai or bigmodel.cn — and
prompts for the key), registers the server with Claude Code (`claude mcp add glm -s user`),
and deploys the `glm-worker` skill plus the `glm` proxy agent type (`~/.claude/agents/glm.md`,
used to route subagent fan-outs to the worker). Restart Claude Code afterward to load both.

## Config — `~/.glm-mcp/config.json`

```json
{
  "api_key": "...",
  "model": "glm-5.2",
  "max_turns": 50,
  "workspace": "",
  "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
  "denylist": [".git"],
  "base_url": "https://api.z.ai/api/paas/v4",
  "thinking": true,
  "reasoning_effort": "max"
}
```

- `base_url` by platform: `https://api.z.ai/api/paas/v4` (z.ai, international) or
  `https://open.bigmodel.cn/api/paas/v4` (bigmodel.cn, mainland China) — the installer asks.
  Both are the pay-as-you-go endpoints; Coding-Plan keys need the `/api/coding/paas/v4`
  variant on either platform instead.
- Env overrides: `GLM_API_KEY` (or `ZAI_API_KEY` / `ZHIPUAI_API_KEY`), `GLM_WORKSPACE`, `GLM_MODE`.
- Empty `workspace` follows Claude Code's launch directory (cwd).
- `allowed_tools` is intersected with the files-only set — **Bash can never be enabled**.
- `thinking` enables GLM-5.2 chain-of-thought (`reasoning_content`); on by default at
  `reasoning_effort: "max"`. Effort levels: `high`/`max` (GLM-5.2 exposes only these two).
  Set `thinking: false` or pass `thinking="off"` per call to save tokens on purely mechanical
  work. GLM's server-side default is thinking ON, so "off" is sent explicitly as
  `{"thinking": {"type": "disabled"}}`.
- The worker states its `max_turns` budget in its system prompt, is instructed to write
  output files incrementally (never one final write), and gets a budget warning injected
  when ~10% of turns remain so partial results are flushed instead of lost.

## Usage

In a Claude Code session, just describe a bounded mechanical job; the `glm-worker` skill
guides Claude on when to call the tool. Or check health: ask Claude to call `ping`.

Per-call overrides: `delegate_to_glm(task, context, model="glm-5.2[1m]", workspace="./src", thinking="off")` (each arg differs from a default; `reasoning_effort` is ignored when `thinking="off"`).

### Turning delegation on/off

- **Soft (instruction):** tell Claude *"do this yourself, don't delegate"* / *"delegate this to glm"*. Instant, mid-conversation.
- **Hard, on the fly (no restart):** ask Claude to *"turn glm off"* / *"on"* — it calls the `glm_set_mode` tool (`on` / `off` / `auto`). Session-scoped: the override lasts until changed again or the server restarts, then reverts to the launch default. `ping` shows the effective mode.
- **At launch:** start Claude with `GLM_MODE=off` to default the session off.

### Optional: auto-route subagent work to GLM

The skill covers delegation Claude does inline. To make Claude also route qualifying
*subagent* stages (Agent tool spawns, Workflow fan-outs) to the `glm` proxy automatically —
no per-task ask — paste this into your global `~/.claude/CLAUDE.md` (or a project
`CLAUDE.md` to scope it to one repo):

```markdown
## Subagent/Workflow → GLM routing

When spawning a subagent for a mechanical/bulk stage, you MAY route it to the GLM-5.2
proxy subagent instead of a default Claude subagent — via either spawn path:

    Agent tool:  subagent_type: 'glm'                # mechanical/bulk -> GLM
    Workflow:    agent(prompt, {agentType: 'glm'})   # mechanical/bulk -> GLM
    Workflow:    agent(prompt, {schema: VERDICT})    # verify/judge/reason -> Claude (default)

**Route to the `glm` agent ONLY when ALL of these hold:** bounded, files-only
(needs only Read/Write/Edit/Glob/Grep), high-volume, mechanical / low-variance (the
transformation is rule-shaped, not open-ended), AND it has a cheap Claude-side
verification gate. Examples: bulk extraction, mechanical translation via a provided
glossary/mapping (NOT free/idiomatic translation, which is reasoning), pattern refactors
across many files, codegen from a template, file-output ETL.

**Files-only does NOT mean reasoning-free.** If the per-item work needs judgment,
classification, disambiguation, or design decisions, keep it on Claude even if it only
touches files. Always keep on the default Claude subagent: verification, judging,
synthesis, planning, and anything needing shell, web, code execution, or non-trivial
reasoning.

**Never route audits/reviews/bug-hunts to GLM — no exceptions.** Consistency audits,
code reviews, open-ended bug hunts, and multi-category analysis are reasoning work.
"Claude subagents unavailable" (session limit, quota) is NOT a reason to route them to
GLM anyway. Do the review inline or defer it. The `glm` proxy agent also refuses these
task shapes on its own.

**Shard + incremental output.** The worker has a hard turn cap (default 50; every
read/edit/glob is a turn). Size each delegation to need well under ~40 turns (roughly
≤10 files per shard for per-file transforms) and fan shards out in parallel. Any task
producing an output file must instruct: create the file first, update after EACH
file/item — never one final write at the end.

**Fail closed.** The `glm` agent is a normal Claude proxy that still spawns even
when glm-mcp is disconnected or delegation mode is OFF — in those cases its
`delegate_to_glm` call returns an `ERROR:` / "GLM delegation is OFF" string
(it does not throw) and the proxy will NOT do the work itself. Therefore:

- Only route to GLM when the worker is actually usable (`mcp__glm__ping` reports
  `mode=on`; the proxy self-enables an OFF mode once, but a disconnected server cannot
  recover).
- EVERY GLM-routed bulk stage MUST be followed by a Claude verify stage that fails
  closed: treat an error / OFF / empty manifest as a failed stage, never let it propagate
  downstream.
```

Not installed automatically: CLAUDE.md is personal config, and blanket auto-routing is a
policy choice each user should opt into deliberately.

### Optional: enforce auto-routing with hooks

The CLAUDE.md block above is advisory — Claude may still forget to route an eligible task.
To make routing *enforced*, add two `PreToolUse` prompt hooks to `~/.claude/settings.json`
(merge into your existing `hooks` object; not installed automatically for the same reason
as above). Each hook runs a small LLM classifier before the tool call:

- **`Agent` gate** — every Agent-tool dispatch is classified. A bounded, mechanical,
  files-only, bulk task on a generic agent type gets denied with an instruction to
  redispatch as `subagent_type: 'glm'`. Reasoning work (review, audit, verification,
  planning, anything needing shell/web/tests) and named agent types (Explore, Plan, …)
  pass through untouched.
- **`Workflow` gate** — the whole workflow script is inspected before it runs. If a
  mechanical/bulk `agent()` stage lacks `agentType: 'glm'`, the call is denied with a
  rewrite checklist (route the stage to glm, shard ≤10 files, incremental output,
  fail-closed Claude verify stage after every glm stage).

Both gates fail open to Claude (uncertain → allow) and set `continueOnBlock: true`, so a
denial feeds the reason back to Claude for a redispatch instead of ending the turn.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Workflow",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "You are a routing gate for Workflow scripts. Hook input: $ARGUMENTS\n\nThe input's tool_input.script is a JavaScript workflow that spawns subagents via agent(prompt, opts). Decide if mechanical bulk stages are correctly routed to the GLM worker.\n\nAnswer ok=true (let the workflow run) if ANY of these hold:\n- tool_input has no script field (invoked via scriptPath or name — cannot inspect; allow)\n- The script contains NO mechanical/bulk stage. Mechanical/bulk = bounded, rule-shaped, files-only work: bulk extraction, mechanical translation via a provided glossary/mapping, pattern refactor across many files, codegen from a template, file-output ETL\n- EVERY mechanical/bulk agent() call already passes agentType: 'glm' in its options, AND each such stage is followed by a Claude verify stage (an agent() WITHOUT agentType:'glm', or explicit result-checking code) that treats error/OFF/empty results as failure\n- All agent() calls are reasoning work: verification, judging, synthesis, planning, review, audit, research, debugging, classification, or need shell/web/code execution — these MUST stay on default Claude agents\n- You are uncertain\n\nAnswer ok=false (block so the orchestrator rewrites the script) ONLY if the script clearly contains one or more mechanical/bulk agent() stages that do NOT pass agentType: 'glm'.\n\nIf blocking, your reason MUST be exactly: \"Workflow has GLM-eligible mechanical stages not routed to the worker. Rewrite the script: (1) add agentType: 'glm' to each mechanical/bulk agent() call; (2) shard to ~<=10 files per glm agent; (3) each glm task must create its output file first and update after EACH item; (4) follow every glm stage with a Claude verify stage that fails closed (error/OFF/empty manifest = failed stage, filter it out); (5) keep verify/judge/synthesis stages on default Claude agents; (6) confirm mcp__glm__ping reports mode=on before resubmitting.\"",
            "continueOnBlock": true,
            "timeout": 45,
            "statusMessage": "Checking workflow GLM routing..."
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "You are a routing gate for subagent dispatches. Hook input: $ARGUMENTS\n\nDecide if this Agent dispatch should be re-routed to the GLM bulk worker.\n\nAnswer ok=true (let the dispatch proceed unchanged) if ANY of these hold:\n- tool_input.subagent_type is already \"glm\"\n- tool_input.subagent_type names a specialized agent (Explore, Plan, claude-code-guide, statusline-setup, code-reviewer, anything starting with \"caveman:\" or \"cavecrew\") — i.e. anything other than missing, \"general-purpose\", or \"claude\"\n- The task involves ANY reasoning work: review, audit, bug hunt, consistency check, verification, judging, synthesis, planning, debugging, research, analysis, classification, disambiguation, design decisions, or free/idiomatic translation\n- The task needs shell, web access, code execution, git, or tests\n- The task is open-ended, low-volume, or not rule-shaped\n- You are uncertain\n\nAnswer ok=false (block so the orchestrator redispatches to GLM) ONLY if ALL of these hold:\n- Bounded, mechanical, rule-shaped work with low variance\n- Files-only: needs nothing beyond Read/Write/Edit/Glob/Grep\n- High-volume/bulk: bulk extraction, mechanical translation via a provided glossary/mapping, pattern refactor across many files, codegen from a template, file-output ETL\n- Cheap Claude-side verification of the output is possible\n\nIf blocking, your reason MUST be exactly: \"GLM-eligible mechanical task. Redispatch with subagent_type 'glm'. Before dispatch: confirm mcp__glm__ping reports mode=on; shard to ~<=10 files per dispatch; instruct worker to create output file first and update after EACH item; afterwards run a Claude verify stage that fails closed (error/OFF/empty manifest = failed stage).\"",
            "continueOnBlock": true,
            "timeout": 30,
            "statusMessage": "Checking GLM routing eligibility..."
          }
        ]
      }
    ]
  }
}
```

Notes:

- Requires the `glm` agent type to be deployed (`~/.claude/agents/glm.md` — the installer
  does this) and the CLAUDE.md routing block above, which supplies the rules the denial
  messages refer to.
- The classifier runs on a small fast model per call; cost is negligible next to what a
  mis-routed bulk stage would burn.
- Known blind spots: Workflow calls via `scriptPath`/`name` pass uninspected (script text
  is not in the hook input), and a denial is an instruction, not a rewrite — the
  orchestrator model performs the redispatch. The CLAUDE.md block still covers both.
- Claude Code hot-reloads `~/.claude/settings.json` if it existed at session start;
  otherwise open `/hooks` once or restart to load the gates.

## Result manifest

Every delegation returns the worker's summary plus:
- **files changed** (written / edited, with counts)
- **assumptions** the worker made
- **could not do** (skips/failures)
- **metrics** (model, turns, tool calls, tokens, duration)

Always verify by reading a sample of changed files / running tests before declaring success.

## Tests

```powershell
.venv\Scripts\python -m pytest tests/ -q
```
```bash
.venv/bin/python -m pytest tests/ -q
```

Offline by default. To run the live smoke test against `glm-5.2`:

```powershell
$env:GLM_LIVE = "1"; .venv\Scripts\python -m pytest tests/test_live_smoke.py -q
```
```bash
GLM_LIVE=1 .venv/bin/python -m pytest tests/test_live_smoke.py -q
```

(needs `GLM_API_KEY` or `ZAI_API_KEY` set, or falls back to skipped)
