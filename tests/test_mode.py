import asyncio
import inspect

import pytest

import glm_worker_mcp.server as server


@pytest.fixture(autouse=True)
def reset_mode(monkeypatch):
    # Start every test from a clean slate: no runtime override, no env.
    monkeypatch.setattr(server, "_runtime_mode_override", None)
    monkeypatch.delenv("GLM_MODE", raising=False)
    yield


def test_default_on():
    assert server._effective_mode() == "on"


def test_env_off():
    import os

    os.environ["GLM_MODE"] = "off"
    try:
        assert server._effective_mode() == "off"
    finally:
        del os.environ["GLM_MODE"]


def test_set_off():
    out = server.glm_set_mode("off")
    assert out.startswith("OK")
    assert "effective now: off" in out.lower()
    assert server._effective_mode() == "off"


def test_set_on_overrides_env_off(monkeypatch):
    monkeypatch.setenv("GLM_MODE", "off")
    server.glm_set_mode("on")
    # runtime override wins over the env launch default
    assert server._effective_mode() == "on"


def test_auto_reverts_to_env(monkeypatch):
    monkeypatch.setenv("GLM_MODE", "off")
    server.glm_set_mode("on")
    assert server._effective_mode() == "on"
    server.glm_set_mode("auto")
    assert server._effective_mode() == "off"  # back to env default


def test_invalid_mode():
    out = server.glm_set_mode("garbage")
    assert out.startswith("ERROR")


def test_delegate_is_async_coroutine():
    # A sync tool blocks FastMCP's event loop for the whole GLM run — ping and
    # cancellation would stall for minutes. The tool must be a coroutine that
    # offloads the blocking agent loop to a worker thread.
    assert inspect.iscoroutinefunction(server.delegate_to_glm)


def test_delegate_short_circuits_when_off(monkeypatch):
    monkeypatch.setattr(server, "_effective_mode", lambda: "off")
    out = asyncio.run(server.delegate_to_glm("do something"))
    assert "off" in out.lower()
    assert "glm_set_mode" in out
