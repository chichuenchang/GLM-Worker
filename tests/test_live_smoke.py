import os
import pytest
from pathlib import Path

from glm_worker_mcp.agent_loop import run_agent
from glm_worker_mcp.config import Config

_LIVE_KEY = os.getenv("GLM_API_KEY") or os.getenv("ZAI_API_KEY") or ""

pytestmark = pytest.mark.skipif(
    os.getenv("GLM_LIVE") != "1" or not _LIVE_KEY,
    reason="set GLM_LIVE=1 and GLM_API_KEY (or ZAI_API_KEY) to run live test",
)


def test_live_write(tmp_path):
    cfg = Config(
        api_key=_LIVE_KEY,
        workspace=tmp_path,
        model="glm-5.2",
        max_turns=8,
        thinking=False,  # mechanical smoke task; skip reasoning tokens
    )
    result = run_agent(
        "Create a file named hello.txt containing exactly: hello world",
        cfg,
    )
    assert result["status"] == "ok"
    assert (tmp_path / "hello.txt").exists()
    assert "hello world" in (tmp_path / "hello.txt").read_text()
