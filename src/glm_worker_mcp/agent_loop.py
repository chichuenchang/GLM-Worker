from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from openai import APIConnectionError, APIError, OpenAI, RateLimitError

from .config import Config
from .tools import ChangeTracker, build_tool_schemas, execute_tool

logger = logging.getLogger(__name__)

API_RETRY_ATTEMPTS = 2
API_RETRY_BACKOFF_SECONDS = 2.0
# old_string carries file content just like content/new_string — redact all three.
SENSITIVE_TOOL_ARG_KEYS = {"content", "new_string", "old_string"}

SYSTEM_PROMPT_TEMPLATE = """You are GLM working as a sub-agent for Claude.

You are given a focused task to complete autonomously within a workspace.
You have local file tools (no shell, no code execution): {tools}

Rules:
1. Stay strictly within the workspace: {workspace}
2. Read before editing. Do not guess file contents.
3. For batch tasks (translate / extract / refactor many files), iterate file-by-file.
4. Do NOT ask the parent (Claude) questions. Make reasonable assumptions and document them.
5. If a tool returns "ERROR: ...", read it and decide: retry with fixed input, skip, or stop. Do not loop on the same error.
6. When finished, reply with a short summary for the parent. End with two OPTIONAL labeled
   sections, one item per line:
   ASSUMPTIONS:
   - <assumption you made>
   COULD NOT DO:
   - <file or item>: <reason>
"""


class AgentLoopError(Exception):
    """Agent loop failed (unrecoverable API error)."""


# A list marker is "-", "*", or "1." / "1)" — bare digits are content.
_ITEM_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")


def _section_for(line: str) -> str | None:
    low = line.strip().lower()
    if low.startswith("assumptions:"):
        return "assumptions"
    if low.startswith(("could not do:", "couldn't do:", "couldnt do:")):
        return "couldnt"
    return None


def _parse_sections(text: str):
    """Split the worker's final text into (assumptions, couldnt, body).

    The labeled sections are only recognized as a trailing block (headers,
    list items, and blank lines at the end of the message) — a mid-prose
    sentence that happens to start with "Could not do:" stays in the body.
    body is the text with the recognized sections removed; they are rendered
    as separate manifest blocks, so leaving them in would duplicate them.
    """
    lines = text.splitlines()
    first_header = None
    for idx in range(len(lines) - 1, -1, -1):
        s = lines[idx].strip()
        if not s:
            continue
        if _section_for(lines[idx]):
            first_header = idx
            continue
        if _ITEM_MARKER_RE.match(lines[idx]):
            continue
        break  # prose above (or below) the trailing block ends the scan
    if first_header is None:
        return [], [], text.strip()

    assumptions: list[str] = []
    couldnt: list[str] = []
    current = None
    for line in lines[first_header:]:
        section = _section_for(line)
        if section:
            current = assumptions if section == "assumptions" else couldnt
            rest = line.strip().split(":", 1)[1].strip()
            if rest:
                current.append(rest)
            continue
        item = _ITEM_MARKER_RE.sub("", line, count=1).strip()
        if item and current is not None:
            current.append(item)
    body = "\n".join(lines[:first_header]).strip()
    return assumptions, couldnt, body


def _redact_args_for_log(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k in SENSITIVE_TOOL_ARG_KEYS and isinstance(v, str):
            out[k] = f"<{len(v)} chars, redacted>"
        elif isinstance(v, str) and len(v) >= 100:
            out[k] = f"<{len(v)} chars>"
        else:
            out[k] = v
    return out


def run_agent(task, config, model=None, workspace=None, client=None,
              thinking=None, reasoning_effort=None) -> dict:
    ws = workspace or config.workspace
    use_model = model or config.model
    use_thinking = config.thinking if thinking is None else bool(thinking)
    use_effort = reasoning_effort or config.reasoning_effort
    client = client or OpenAI(api_key=config.api_key, base_url=config.base_url)
    tools = build_tool_schemas(config.allowed_tools)
    tracker = ChangeTracker()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        tools=", ".join(config.allowed_tools), workspace=ws
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    total_prompt = total_completion = tool_call_count = 0
    started = time.time()

    def _metrics(turns):
        return {
            "model": use_model,
            "turns_used": turns,
            "tool_calls": tool_call_count,
            "tokens": {
                "prompt": total_prompt,
                "completion": total_completion,
                "total": total_prompt + total_completion,
            },
            "duration_seconds": round(time.time() - started, 2),
        }

    for turn in range(config.max_turns):
        response = _call_with_retry(
            client, use_model, messages, tools, turn,
            thinking=use_thinking, reasoning_effort=use_effort,
        )
        usage = getattr(response, "usage", None)
        if usage:
            total_prompt += usage.prompt_tokens
            total_completion += usage.completion_tokens
        msg = response.choices[0].message
        raw = response.model_dump(exclude_none=True)
        assistant_msg = raw["choices"][0]["message"]
        # CoT (message.reasoning_content) is stateless: re-sending it every
        # turn only inflates prompt tokens, so strip it before appending.
        assistant_msg.pop("reasoning_content", None)
        messages.append(assistant_msg)

        if not msg.tool_calls:
            final = msg.content or "(empty response)"
            assumptions, couldnt, body = _parse_sections(final)
            return {
                "final_message": body or "(no summary)",
                "files_changed": tracker.manifest(),
                "assumptions": assumptions,
                "couldnt_do": couldnt,
                "status": "ok",
                "metrics": _metrics(turn + 1),
            }

        for tc in msg.tool_calls:
            tool_call_count += 1
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                result = f"ERROR: invalid JSON in tool arguments: {e}"
            else:
                logger.info(
                    "Turn %d tool_call: %s(%s)",
                    turn, tc.function.name, _redact_args_for_log(args),
                )
                result = execute_tool(tc.function.name, args, ws, config.denylist, tracker)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    last_text = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            last_text = str(m["content"])
            break
    assumptions, couldnt, body = _parse_sections(last_text)
    return {
        "final_message": f"[stopped: hit max_turns={config.max_turns}] " + (body or "(no final text)"),
        "files_changed": tracker.manifest(),
        "assumptions": assumptions,
        "couldnt_do": couldnt,
        "status": "max_turns",
        "metrics": _metrics(config.max_turns),
    }


def _call_with_retry(client, model, messages, tools, turn,
                     thinking=False, reasoning_effort="max"):
    kwargs = {
        "model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
    }
    # GLM-5.2 is a hybrid reasoning model whose server-side default is thinking ON,
    # so both states are sent explicitly. z.ai's documented shape carries
    # reasoning_effort inside extra_body ("high" | "max").
    if thinking:
        kwargs["extra_body"] = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
        }
    else:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    last_exc = None
    for attempt in range(1 + API_RETRY_ATTEMPTS):
        try:
            return client.chat.completions.create(**kwargs)
        except (APIConnectionError, RateLimitError) as e:
            last_exc = e
        except APIError as e:
            status = getattr(e, "status_code", None)
            if not (status and 500 <= status < 600):
                raise AgentLoopError(f"GLM API error on turn {turn}: {e}") from e
            last_exc = e
        except Exception as e:
            raise AgentLoopError(f"GLM API error on turn {turn}: {e}") from e
        if attempt < API_RETRY_ATTEMPTS:  # no pointless sleep after the last try
            time.sleep(API_RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise AgentLoopError(
        f"GLM API unreachable after {1 + API_RETRY_ATTEMPTS} attempts on turn {turn}: {last_exc}"
    ) from last_exc
