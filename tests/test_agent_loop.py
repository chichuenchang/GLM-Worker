import json
import types
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError

import glm_worker_mcp.agent_loop as agent_loop_mod
from glm_worker_mcp.agent_loop import (
    AgentLoopError,
    API_RETRY_ATTEMPTS,
    _parse_sections,
    _redact_args_for_log,
    run_agent,
)
from glm_worker_mcp.config import Config


def test_parse_sections():
    text = (
        "Did the thing.\n"
        "ASSUMPTIONS:\n- keys are nested\n- utf8 only\n"
        "COULD NOT DO:\n- legacy.js: bad encoding\n"
    )
    assumptions, couldnt, body = _parse_sections(text)
    assert assumptions == ["keys are nested", "utf8 only"]
    assert couldnt == ["legacy.js: bad encoding"]
    assert body == "Did the thing."


def test_parse_sections_absent():
    assert _parse_sections("just a summary") == ([], [], "just a summary")


def test_parse_sections_strips_sections_from_body():
    # Sections are rendered as their own manifest blocks; leaving them in the
    # body duplicated them in the formatted result. Blank lines between the
    # trailing sections are tolerated.
    text = "Did the thing.\nASSUMPTIONS:\n- keys are nested\n\nCOULD NOT DO:\n- legacy.js: enc\n"
    assumptions, couldnt, body = _parse_sections(text)
    assert assumptions == ["keys are nested"]
    assert couldnt == ["legacy.js: enc"]
    assert body == "Did the thing."


def test_parse_sections_midtext_label_stays_in_body():
    # A section label is only a section when it starts the trailing list block.
    # Mid-prose sentences that happen to start with "Could not do:" must not
    # swallow the rest of the message.
    text = (
        "Summary.\n"
        "Could not do: parse the legacy file today.\n"
        "It needs manual review before anything else.\n"
        "Done."
    )
    assumptions, couldnt, body = _parse_sections(text)
    assert assumptions == [] and couldnt == []
    assert body == text


def test_parse_sections_numeric_item_not_mangled():
    # Leading digits are list markers only when followed by "." or ")" —
    # "3 files" is content, not numbering.
    text = "Done.\nASSUMPTIONS:\n- 3 files were already translated\n"
    assumptions, _, _ = _parse_sections(text)
    assert assumptions == ["3 files were already translated"]


def test_final_message_excludes_parsed_sections(tmp_path):
    responses = [
        _FakeResponse(
            _FakeMessage(content="Done.\nASSUMPTIONS:\n- none\n", tool_calls=None),
            {"role": "assistant", "content": "Done.\nASSUMPTIONS:\n- none\n"},
        ),
    ]
    result = run_agent("x", _cfg(tmp_path), client=_FakeClient(responses))
    assert result["final_message"] == "Done."
    assert result["assumptions"] == ["none"]


def test_redact_sensitive():
    out = _redact_args_for_log(
        {"path": "a", "content": "x" * 200, "new_string": "y", "old_string": "z"}
    )
    assert out["path"] == "a"
    assert "redacted" in out["content"]
    assert "redacted" in out["new_string"]
    assert "redacted" in out["old_string"]  # file content leaks via old_string too


# ---- run_agent integration with a fake OpenAI-compatible client ----


class _FakeFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FakeFn(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5


class _FakeResponse:
    def __init__(self, message, dump):
        self.choices = [types.SimpleNamespace(message=message)]
        self.usage = _FakeUsage()
        self._dump = dump

    def model_dump(self, exclude_none=True):
        return {"choices": [{"message": self._dump}]}


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # run_agent mutates the same messages list across turns; snapshot it so
        # each recorded call reflects what was actually sent on that turn.
        snap = dict(kwargs)
        snap["messages"] = list(kwargs["messages"])
        self.calls.append(snap)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)
        self.chat = types.SimpleNamespace(completions=self.completions)


def _cfg(ws):
    return Config(api_key="sk-x", workspace=ws, model="glm-5.2", max_turns=10)


def test_run_agent_writes_file_and_manifests(tmp_path):
    tc = _FakeToolCall("c1", "Write", json.dumps({"path": "out.txt", "content": "hi\n"}))
    responses = [
        _FakeResponse(
            _FakeMessage(content=None, tool_calls=[tc]),
            {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
             "function": {"name": "Write", "arguments": json.dumps({"path": "out.txt", "content": "hi\n"})}}]},
        ),
        _FakeResponse(
            _FakeMessage(content="Done.\nASSUMPTIONS:\n- none\n", tool_calls=None),
            {"role": "assistant", "content": "Done.\nASSUMPTIONS:\n- none\n"},
        ),
    ]
    result = run_agent("write hi", _cfg(tmp_path), client=_FakeClient(responses))
    assert (tmp_path / "out.txt").read_text() == "hi\n"
    assert result["status"] == "ok"
    assert result["files_changed"] == [{"path": "out.txt", "action": "written", "count": 1}]
    assert result["assumptions"] == ["none"]
    assert result["metrics"]["model"] == "glm-5.2"
    assert result["metrics"]["tool_calls"] == 1
    assert result["metrics"]["tokens"]["total"] == 30  # 2 calls * (10 + 5)


def _done_response():
    return _FakeResponse(
        _FakeMessage(content="Done.", tool_calls=None),
        {"role": "assistant", "content": "Done."},
    )


def test_thinking_off_sends_explicit_disabled(tmp_path):
    # GLM-5.2 is a reasoning model: server-side default is thinking ON, so "off"
    # must be sent explicitly rather than omitting the parameter.
    cfg = Config(api_key="sk-x", workspace=tmp_path, thinking=False)
    client = _FakeClient([_done_response()])
    run_agent("x", cfg, client=client)
    kw = client.completions.calls[0]
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kw


def test_thinking_on_sends_extra_body_and_effort(tmp_path):
    client = _FakeClient([_done_response()])
    run_agent("x", _cfg(tmp_path), client=client, thinking=True, reasoning_effort="max")
    kw = client.completions.calls[0]
    # z.ai documented shape: reasoning_effort rides inside extra_body.
    assert kw["extra_body"] == {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}
    assert "reasoning_effort" not in kw


def test_thinking_defaults_from_config(tmp_path):
    cfg = Config(api_key="sk-x", workspace=tmp_path, thinking=True, reasoning_effort="high")
    client = _FakeClient([_done_response()])
    run_agent("x", cfg, client=client)
    kw = client.completions.calls[0]
    assert kw["extra_body"] == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}


def test_reasoning_content_not_resent(tmp_path):
    # z.ai returns CoT as message.reasoning_content; re-sending it every turn
    # inflates prompt tokens for no benefit. It must be stripped before the
    # assistant message is appended back to the conversation.
    tc = _FakeToolCall("c1", "Read", json.dumps({"path": "missing.txt"}))
    responses = [
        _FakeResponse(
            _FakeMessage(content=None, tool_calls=[tc]),
            {"role": "assistant", "reasoning_content": "chain of thought here",
             "tool_calls": [{"id": "c1", "type": "function",
              "function": {"name": "Read", "arguments": json.dumps({"path": "missing.txt"})}}]},
        ),
        _done_response(),
    ]
    client = _FakeClient(responses)
    run_agent("x", _cfg(tmp_path), client=client)
    second_call_messages = client.completions.calls[1]["messages"]
    assert all("reasoning_content" not in m for m in second_call_messages)


def test_retry_sleeps_only_between_attempts(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(agent_loop_mod.time, "sleep", lambda s: sleeps.append(s))

    class _Boom:
        def __init__(self):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        def _create(self, **kwargs):
            raise APIConnectionError(request=httpx.Request("POST", "http://x"))

    with pytest.raises(AgentLoopError):
        run_agent("x", _cfg(tmp_path), client=_Boom())
    # A sleep after the final failed attempt delays the error for nothing.
    assert len(sleeps) == API_RETRY_ATTEMPTS


def test_run_agent_hits_max_turns(tmp_path):
    def always_tool():
        tc = _FakeToolCall("c", "Read", json.dumps({"path": "x"}))
        return _FakeResponse(
            _FakeMessage(content="thinking", tool_calls=[tc]),
            {"role": "assistant", "content": "thinking", "tool_calls": [{"id": "c", "type": "function",
             "function": {"name": "Read", "arguments": json.dumps({"path": "x"})}}]},
        )

    cfg = Config(api_key="sk-x", workspace=tmp_path, max_turns=3)
    result = run_agent("loop", cfg, client=_FakeClient([always_tool() for _ in range(3)]))
    assert result["status"] == "max_turns"
    assert result["metrics"]["turns_used"] == 3


def _always_tool():
    tc = _FakeToolCall("c", "Read", json.dumps({"path": "x"}))
    return _FakeResponse(
        _FakeMessage(content="thinking", tool_calls=[tc]),
        {"role": "assistant", "content": "thinking", "tool_calls": [{"id": "c", "type": "function",
         "function": {"name": "Read", "arguments": json.dumps({"path": "x"})}}]},
    )


def _warning_messages(client):
    return [
        m
        for kw in client.completions.calls
        for m in kw["messages"]
        if m.get("role") == "user" and "[turn budget warning]" in str(m.get("content", ""))
    ]


def test_system_prompt_states_budget_and_incremental_rule(tmp_path):
    client = _FakeClient([_done_response()])
    run_agent("x", _cfg(tmp_path), client=client)
    system = client.completions.calls[0]["messages"][0]["content"]
    assert "10 conversation turns" in system  # _cfg uses max_turns=10
    assert "incrementally" in system


def test_turn_warning_injected_near_cap(tmp_path):
    # max_turns=10 -> warn when max(2, 10 // 10) = 2 turns remain, i.e. turn 8.
    cfg = Config(api_key="sk-x", workspace=tmp_path, max_turns=10)
    client = _FakeClient([_always_tool() for _ in range(10)])
    result = run_agent("loop", cfg, client=client)
    assert result["status"] == "max_turns"
    warnings = _warning_messages(client)
    assert warnings, "expected a turn budget warning near the cap"
    assert "Only 2 turns remain" in warnings[0]["content"]
    # Injected before the turn-8 call, absent from the turn-7 call.
    assert not any(
        "[turn budget warning]" in str(m.get("content", ""))
        for m in client.completions.calls[7]["messages"]
    )


def test_turn_warning_absent_when_done_early(tmp_path):
    client = _FakeClient([_done_response()])
    run_agent("x", _cfg(tmp_path), client=client)
    assert _warning_messages(client) == []


def test_turn_warning_never_preempts_first_turn(tmp_path):
    # max_turns=2 -> threshold 2 only matches at turn 0, which is skipped:
    # warning a worker before it has done anything would waste the whole budget.
    cfg = Config(api_key="sk-x", workspace=tmp_path, max_turns=2)
    client = _FakeClient([_always_tool() for _ in range(2)])
    run_agent("loop", cfg, client=client)
    assert _warning_messages(client) == []
