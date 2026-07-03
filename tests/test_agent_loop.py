import json
import types
from pathlib import Path

from glm_worker_mcp.agent_loop import (
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
    # body duplicated them in the formatted result.
    text = "Did the thing.\nASSUMPTIONS:\n- keys are nested\n\nTrailer."
    assumptions, couldnt, body = _parse_sections(text)
    assert assumptions == ["keys are nested"]
    assert couldnt == []
    assert body == "Did the thing.\nTrailer."


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
    out = _redact_args_for_log({"path": "a", "content": "x" * 200, "new_string": "y"})
    assert out["path"] == "a"
    assert "redacted" in out["content"]
    assert "redacted" in out["new_string"]


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
        self.calls.append(kwargs)
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
