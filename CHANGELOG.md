# Changelog

## 0.1.1 — 2026-07-03

- Read `~/.glm-mcp/config.json` with `utf-8-sig`: tolerate the BOM that Windows
  PowerShell 5.1 `Set-Content -Encoding utf8` prepends, which made the server
  report `NOT_CONFIGURED (Invalid JSON ...)` after hand-editing the config.

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
- API key env: `GLM_API_KEY`, with `ZAI_API_KEY` accepted as fallback.
- Tools renamed: `delegate_to_glm`, `glm_set_mode`; config at `~/.glm-mcp/`;
  mode env `GLM_MODE`; skill deployed as `glm-worker`.
