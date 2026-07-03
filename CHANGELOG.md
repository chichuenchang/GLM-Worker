# Changelog

## 0.1.2 — 2026-07-03

Production-hardening pass: 10 fixes from a full pre-release audit.

- Installers: re-running no longer nests the deployed skill
  (`~/.claude/skills/glm-worker/glm-worker/`) — the previous deploy is removed
  before copying.
- `delegate_to_glm` is now async and runs the agent loop in a worker thread, so
  the MCP event loop keeps serving `ping`, `glm_set_mode`, and cancellations
  during a delegation.
- Default `denylist` is now `[".git"]` (config default, both installer
  templates): the worker can no longer rewrite `.git` hooks/config unless the
  user explicitly opts in with `"denylist": []`.
- Glob/Grep now honor the denylist — previously Grep could read (and Glob could
  list) files that Read/Write/Edit were blocked from.
- Glob/Grep now see hidden dot-files/dirs on Python 3.11+ (`include_hidden`);
  on 3.10 they remain skipped.
- Manifest sections (`ASSUMPTIONS:` / `COULD NOT DO:`) are only parsed as a
  trailing block; a mid-prose "Could not do: …" sentence no longer swallows the
  rest of the summary, and numeric list items ("3 files …") are no longer
  mangled.
- `reasoning_content` (CoT) is stripped before re-appending assistant messages:
  it is stateless, and re-sending it inflated prompt tokens every turn.
- Repeated `Edit` calls to the same file now accumulate the edit count in the
  manifest instead of reporting only the last call's count.
- Read/Edit refuse files over 10 MB and Grep skips them, instead of loading
  them into memory whole.
- API retry no longer sleeps after the final failed attempt.
- `old_string` is redacted from tool-call logs (it carries file content, like
  `content`/`new_string`).

## 0.1.1 — 2026-07-03

- Read `~/.glm-mcp/config.json` with `utf-8-sig`: tolerate the BOM that Windows
  PowerShell 5.1 `Set-Content -Encoding utf8` prepends, which made the server
  report `NOT_CONFIGURED (Invalid JSON ...)` after hand-editing the config.
- `install.ps1` writes the config template without a BOM (was the repo's own
  BOM source on Windows PowerShell 5.1).
- Manifest no longer duplicates the worker's `ASSUMPTIONS:` / `COULD NOT DO:`
  text: the parsed sections are stripped from `final_message` and rendered only
  as their `--- assumptions ---` / `--- could not do ---` blocks.

## 0.1.0 — 2026-07-03

Initial release. Port of `deepseek-worker-mcp` v0.3.0 to a GLM-5.2 (z.ai) backend —
same spec: files-only worker loop (Read/Write/Edit/Glob/Grep, no shell), complete
path-jail sandbox, structured result manifest, runtime `glm_set_mode` toggle,
thinking mode on by default.

GLM-specific deltas vs the DeepSeek original:

- Backend: `https://api.z.ai/api/paas/v4`, default model `glm-5.2`
  (`glm-5.2[1m]` = 1M-context per-call override).
- `reasoning_effort` accepts only `high`/`max` (GLM-5.2 exposes two levels;
  DeepSeek had four) and is sent inside `extra_body` per z.ai's documented shape.
- Thinking OFF is sent explicitly as `{"thinking": {"type": "disabled"}}` — GLM's
  server-side default is ON, so omitting the parameter would not disable it.
- API key env: `GLM_API_KEY`, with `ZAI_API_KEY` (z.ai) then `ZHIPUAI_API_KEY`
  (bigmodel.cn) accepted as fallbacks.
- Tools renamed: `delegate_to_glm`, `glm_set_mode`; config at `~/.glm-mcp/`;
  mode env `GLM_MODE`; skill deployed as `glm-worker`.
