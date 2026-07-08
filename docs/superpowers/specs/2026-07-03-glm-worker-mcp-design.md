# glm-worker-mcp — Design Spec

**Date:** 2026-07-03
**Status:** Implemented (v0.1.3)
**Author:** Jim Zheng (with Claude)

## 1. Purpose

Port of [deepseek-worker-mcp](../../../../deepseek-worker-mcp/docs/superpowers/specs/2026-06-10-deepseek-worker-mcp-design.md)
(v0.3.0) to a **GLM-5.2 (z.ai) backend**. Same spec by construction: Claude orchestrates,
a files-only GLM worker executes bounded mechanical sub-tasks through one
`delegate_to_glm` call and returns a structured manifest. That document remains the
authoritative description of the architecture (worker tool set, path-jail security
boundary, manifest format, agent-loop/retry semantics, mode precedence, logging,
install shape). This spec records only the deltas.

## 2. Deltas vs deepseek-worker-mcp

| Aspect | deepseek-worker-mcp | glm-worker-mcp |
|---|---|---|
| Backend API | `https://api.deepseek.com` | `https://api.z.ai/api/paas/v4` (OpenAI-compatible) |
| Default model | `deepseek-v4-pro` | `glm-5.2` |
| Cheap/scale variant | `deepseek-v4-flash` per call | no cheap variant; `glm-5.2[1m]` per call for 1M context; cost lever is `thinking="off"` |
| `reasoning_effort` values | `low/medium/high/max` | `high/max` (all GLM-5.2 exposes) |
| Effort wire shape | top-level kwarg | inside `extra_body` (z.ai documented shape) |
| Thinking OFF | omit params (DeepSeek default is off) | send explicit `{"thinking": {"type": "disabled"}}` — GLM server-side default is ON |
| API key env | `DEEPSEEK_API_KEY` | `GLM_API_KEY`, fallbacks `ZAI_API_KEY` (z.ai) then `ZHIPUAI_API_KEY` (bigmodel.cn) |
| Other env | `DEEPSEEK_WORKSPACE`, `DEEPSEEK_MODE` | `GLM_WORKSPACE`, `GLM_MODE` |
| MCP tools | `ping`, `delegate_to_deepseek`, `deepseek_set_mode` | `ping`, `delegate_to_glm`, `glm_set_mode` |
| Config/log dir | `~/.deepseek-mcp/` | `~/.glm-mcp/` |
| Registered server / CLI | `deepseek` / `deepseek-mcp` | `glm` / `glm-mcp` |
| Skill | `delegate-to-deepseek` | `glm-worker` |
| Live smoke env | `DEEPSEEK_LIVE=1` | `GLM_LIVE=1` (+ key), thinking off |
| Package | `deepseek_worker_mcp` 0.3.0 | `glm_worker_mcp` 0.1.0 |

Unchanged by design: `safety.py` and `tools.py` are verbatim copies (backend-agnostic);
`agent_loop.py`/`server.py`/`config.py` differ only by the deltas above; the worker
system prompt says "You are GLM" instead of "You are DeepSeek".

GLM-5.2 returns chain-of-thought as `message.reasoning_content` — the same convention
DeepSeek uses — so the loop's full-message round-trip (`model_dump(exclude_none=True)`
appended each turn) ports unchanged.

**Residual risk (unchanged shape, new endpoint):** file contents the worker reads are
transmitted to `api.z.ai`.

## 3. GLM-5.2 API facts (verified 2026-07-03)

- Released 2026-06-13; OpenAI-compatible chat completions; tool calling supported;
  Anthropic-compatible endpoint also exists (not used here).
- Model ids: `glm-5.2`, `glm-5.2[1m]` (1M input context; output up to 131,072 tokens).
- Thinking: `extra_body={"thinking": {"type": "enabled"|"disabled"}, "reasoning_effort": "high"|"max"}`;
  z.ai recommends `max` for coding.
- Two pay-as-you-go platforms, same OpenAI-compatible v4 API: `https://api.z.ai/api/paas/v4`
  (international) and `https://open.bigmodel.cn/api/paas/v4` (mainland China; SDK key env
  convention `ZHIPUAI_API_KEY`). Keys are platform-specific; the installer asks which and
  sets `base_url`. The `/api/coding/paas/v4` variants are the GLM Coding Plan (subscription)
  endpoints — override `base_url` if on that plan.
- `thinking`/`reasoning_effort` shapes verified against z.ai docs; bigmodel.cn shares the
  v4 API family and is expected to match (unverified there — a 400 on `reasoning_effort`
  would surface as a clear AgentLoopError; workaround `thinking="off"` or dropping effort).

## 4. Testing

Full suite ported (80 tests: 78 offline + skip-gated live smoke + skip-gated symlink
case on Windows). New/changed coverage: ZAI fallback + GLM_API_KEY precedence,
effort validation rejects DeepSeek-style `low`/`medium`, explicit
`{"thinking": {"type": "disabled"}}` when off, `reasoning_effort` inside `extra_body`,
BOM-tolerant config read (0.1.1), section-stripped `final_message` (0.1.1), the
0.1.2 hardening pass (denylist default `[".git"]` + Glob/Grep enforcement, hidden
files on 3.11+, trailing-block manifest parsing, size caps, edit-count accumulation,
async delegate), and the 0.1.3 turn-budget pass (system prompt states max_turns +
incremental-output rule, `[turn budget warning]` injected near the cap, never on the
first turn, absent on early finish).
