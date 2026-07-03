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
(`api.z.ai`). Don't point it at secrets you don't want transmitted. An optional, empty-by-
default `denylist` lets you refuse secret-pattern paths (e.g. `.env*`, `.git`, `*.pem`).

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
and deploys the `glm-worker` skill. Restart Claude Code afterward to load the new MCP server.

## Config — `~/.glm-mcp/config.json`

```json
{
  "api_key": "...",
  "model": "glm-5.2",
  "max_turns": 50,
  "workspace": "",
  "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
  "denylist": [],
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

## Usage

In a Claude Code session, just describe a bounded mechanical job; the `glm-worker` skill
guides Claude on when to call the tool. Or check health: ask Claude to call `ping`.

Per-call overrides: `delegate_to_glm(task, context, model="glm-5.2[1m]", workspace="./src", thinking="off")` (each arg differs from a default; `reasoning_effort` is ignored when `thinking="off"`).

### Turning delegation on/off

- **Soft (instruction):** tell Claude *"do this yourself, don't delegate"* / *"delegate this to glm"*. Instant, mid-conversation.
- **Hard, on the fly (no restart):** ask Claude to *"turn glm off"* / *"on"* — it calls the `glm_set_mode` tool (`on` / `off` / `auto`). Session-scoped: the override lasts until changed again or the server restarts, then reverts to the launch default. `ping` shows the effective mode.
- **At launch:** start Claude with `GLM_MODE=off` to default the session off.

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
