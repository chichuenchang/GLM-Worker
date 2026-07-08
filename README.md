# glm-worker-mcp

An MCP server that lets **Claude orchestrate** and a **GLM-5.2 worker** do bounded,
mechanical file work. Claude delegates a sub-task through one tool call; GLM runs its own
multi-turn loop over a **files-only** tool set and returns a structured, verifiable manifest.
The point: move bulk token volume to a cheaper model while Claude keeps planning + verification.

Same spec as [deepseek-worker-mcp](../deepseek-worker-mcp) with GLM-5.2 (z.ai) as the backend.

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
