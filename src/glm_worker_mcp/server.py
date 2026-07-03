from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    # ProactorEventLoop (the Windows default) is unreliable for MCP stdio pipes;
    # the Selector policy is the standard workaround. The policy API is deprecated
    # for removal in 3.16 — suppress that future-deprecation noise, keep behavior.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from functools import partial

import anyio
from mcp.server.fastmcp import FastMCP

from . import __version__
from .agent_loop import AgentLoopError, run_agent
from .config import Config, VALID_REASONING_EFFORT

_LOG_DIR = Path.home() / ".glm-mcp"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_SERVER_LOG = _LOG_DIR / "server.log"
_USAGE_LOG = _LOG_DIR / "usage.log"

try:
    os.chmod(_LOG_DIR, 0o700)
except OSError:
    pass
for _p in (_SERVER_LOG, _USAGE_LOG):
    if not _p.exists():
        try:
            _p.touch(mode=0o600)
        except OSError:
            pass
    try:
        os.chmod(_p, 0o600)
    except OSError:
        pass

logging.basicConfig(
    filename=str(_SERVER_LOG),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP("glm-mcp")

# Session-scoped delegation override set via the glm_set_mode tool.
# None = fall back to the GLM_MODE env default. Resets when the server restarts,
# so a stale "off" can never silently disable delegation in a future session.
_runtime_mode_override: str | None = None


def _shorten_path(p: Path) -> str:
    s = str(p)
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    return s


def _effective_mode() -> str:
    """Resolve delegation mode: runtime override > GLM_MODE env > 'on'."""
    if _runtime_mode_override in ("on", "off"):
        return _runtime_mode_override
    env = os.getenv("GLM_MODE", "").strip().lower()
    return "off" if env == "off" else "on"


@mcp.tool()
def ping() -> str:
    """Health check. Returns version, mode, and config status."""
    try:
        cfg = Config.load()
        think = f"on(effort={cfg.reasoning_effort})" if cfg.thinking else "off"
        status = (
            f"workspace={_shorten_path(cfg.workspace)} (sandbox), "
            f"model={cfg.model}, thinking={think}"
        )
    except Exception as e:
        status = f"NOT_CONFIGURED ({e})"
    return f"pong from glm-worker-mcp v{__version__} | mode={_effective_mode()} | {status}"


@mcp.tool()
def glm_set_mode(mode: str) -> str:
    """Turn GLM delegation on or off for the rest of this session — no restart.

    Use this when the user says things like "turn glm off", "stop delegating",
    or "re-enable glm". Takes effect on the next delegate_to_glm call and
    lasts until changed again or the server restarts.

    Args:
        mode: "off" disables delegation, "on" enables it, "auto" reverts to the
              GLM_MODE launch default.
    """
    global _runtime_mode_override
    m = (mode or "").strip().lower()
    if m not in ("on", "off", "auto"):
        return f"ERROR: mode must be 'on', 'off', or 'auto' (got {mode!r})"
    _runtime_mode_override = None if m == "auto" else m
    return f"OK: delegation override set to '{m}'. Effective now: {_effective_mode()}."


@mcp.tool()
async def delegate_to_glm(
    task: str, context: str = "", model: str = "", workspace: str = "",
    thinking: str = "", reasoning_effort: str = "",
) -> str:
    """Delegate a focused, mechanical file task to GLM-5.2 as a files-only sub-agent.

    GLM runs its own loop with Read/Write/Edit/Glob/Grep inside the workspace
    (no shell). Good for batch extraction, translation, pattern refactors, codegen,
    file-output ETL. Returns a manifest of files changed + assumptions + metrics.
    ALWAYS verify by reading a sample of changed files before declaring success.

    Args:
        task: What to accomplish, with success criteria and file paths.
        context: Optional conventions / output format / related files.
        model: Optional model override (e.g. "glm-5.2[1m]" for the 1M-context
               variant). Empty = config default.
        workspace: Optional workspace override (must exist). Empty = config default.
        thinking: Optional "on"/"off" to enable/disable GLM reasoning
                  (chain-of-thought) for this call. Empty = config default. Costs
                  more tokens; use for tasks needing reasoning, not pure
                  mechanical batch work.
        reasoning_effort: Optional "high"/"max" depth when thinking is on.
                  Empty = config default. Ignored if thinking is off.
    """
    if _effective_mode() == "off":
        return (
            "GLM delegation is OFF (re-enable with the glm_set_mode tool, "
            "mode='on'). Continue the task yourself."
        )
    try:
        config = Config.load()
    except Exception as e:
        return f"ERROR: glm-worker-mcp not configured: {e}"

    ws_override = None
    if workspace:
        ws_path = Path(os.path.expanduser(workspace)).resolve()
        if not ws_path.exists():
            return f"ERROR: workspace override does not exist: {workspace}"
        ws_override = ws_path

    think_override = None
    t = (thinking or "").strip().lower()
    if t in ("on", "true", "1", "yes"):
        think_override = True
    elif t in ("off", "false", "0", "no"):
        think_override = False
    elif t:
        return f"ERROR: thinking must be 'on' or 'off' (got {thinking!r})"

    effort_override = None
    e = (reasoning_effort or "").strip().lower()
    if e:
        if e not in VALID_REASONING_EFFORT:
            return f"ERROR: reasoning_effort must be one of {VALID_REASONING_EFFORT} (got {reasoning_effort!r})"
        effort_override = e

    full_task = task if not context else f"{task}\n\n# Additional context\n{context}"
    logger.info(
        "delegate invoked. task_len=%d context_len=%d model=%s thinking=%s",
        len(task), len(context), model or config.model,
        think_override if think_override is not None else config.thinking,
    )
    try:
        # run_agent blocks (HTTP calls, retry sleeps) — offload it so the
        # event loop keeps serving ping/set_mode and MCP cancellations.
        result = await anyio.to_thread.run_sync(
            partial(
                run_agent, full_task, config, model=model or None,
                workspace=ws_override, thinking=think_override,
                reasoning_effort=effort_override,
            )
        )
    except AgentLoopError as e:
        logger.exception("agent loop failed")
        return f"ERROR: GLM agent loop failed: {e}"
    except Exception as e:
        logger.exception("unexpected delegation failure")
        return f"ERROR: unexpected failure: {e}"

    _log_usage(task, result)
    return _format_result(result)


def _format_result(result: dict) -> str:
    parts = [result["final_message"].strip()]
    fc = result["files_changed"]
    if fc:
        lines = []
        for c in fc:
            if c["action"] == "written":
                lines.append(f"- written  {c['path']}  ({c['count']} lines)")
            else:
                lines.append(f"- edited   {c['path']}  ({c['count']} edit(s))")
        parts.append("--- files changed ---\n" + "\n".join(lines))
    if result["assumptions"]:
        parts.append("--- assumptions ---\n" + "\n".join(f"- {a}" for a in result["assumptions"]))
    if result["couldnt_do"]:
        parts.append("--- could not do ---\n" + "\n".join(f"- {c}" for c in result["couldnt_do"]))
    m = result["metrics"]
    t = m["tokens"]
    status = result.get("status", "ok")
    suffix = "" if status == "ok" else f"  status={status}"
    parts.append(
        "--- metrics ---\n"
        f"model={m['model']}  turns={m['turns_used']}  tool_calls={m['tool_calls']}  "
        f"tokens={t['total']} (prompt {t['prompt']} / completion {t['completion']})  "
        f"duration={m['duration_seconds']}s{suffix}"
    )
    return "\n\n".join(parts)


def _log_usage(task: str, result: dict) -> None:
    try:
        if _USAGE_LOG.exists() and _USAGE_LOG.stat().st_size > 10 * 1024 * 1024:
            try:
                _USAGE_LOG.replace(_USAGE_LOG.with_suffix(".log.1"))
            except OSError:
                pass
        m = result["metrics"]
        with open(_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(
                f"{m['duration_seconds']:.1f}s  turns={m['turns_used']:>2}  "
                f"tools={m['tool_calls']:>2}  tokens={m['tokens']['total']:>6}  "
                f"status={result.get('status', 'ok')}  task={task[:60]!r}\n"
            )
        try:
            os.chmod(_USAGE_LOG, 0o600)
        except OSError:
            pass
    except Exception:
        pass


def main() -> None:
    logger.info(
        "glm-worker-mcp v%s starting (mode=%s)",
        __version__, _effective_mode(),
    )
    try:
        mcp.run()
    except Exception as e:
        logger.exception("MCP server crashed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
