import json
import pytest
from pathlib import Path

from glm_worker_mcp.config import Config, FILES_ONLY_TOOLS


def write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    monkeypatch.delenv("GLM_WORKSPACE", raising=False)
    yield


def test_load_basic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = write_cfg(tmp_path, {"api_key": "sk-abc"})
    cfg = Config.load(config_path=p)
    assert cfg.api_key == "sk-abc"
    assert cfg.model == "glm-5.2"
    assert cfg.base_url == "https://api.z.ai/api/paas/v4"
    assert cfg.allowed_tools == FILES_ONLY_TOOLS
    assert cfg.workspace == Path.cwd()


def test_env_key_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-env")
    p = write_cfg(tmp_path, {"api_key": "sk-file"})
    assert Config.load(config_path=p).api_key == "sk-env"


def test_zai_key_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "sk-zai")
    p = write_cfg(tmp_path, {"api_key": "sk-file"})
    assert Config.load(config_path=p).api_key == "sk-zai"


def test_glm_key_beats_zai_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "sk-glm")
    monkeypatch.setenv("ZAI_API_KEY", "sk-zai")
    p = write_cfg(tmp_path, {"api_key": "sk-file"})
    assert Config.load(config_path=p).api_key == "sk-glm"


def test_zhipuai_key_fallback(tmp_path, monkeypatch):
    # bigmodel.cn (mainland) SDK convention.
    monkeypatch.setenv("ZHIPUAI_API_KEY", "sk-zhipu")
    p = write_cfg(tmp_path, {"api_key": "sk-file"})
    assert Config.load(config_path=p).api_key == "sk-zhipu"


def test_zai_key_beats_zhipuai_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "sk-zai")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "sk-zhipu")
    p = write_cfg(tmp_path, {"api_key": "sk-file"})
    assert Config.load(config_path=p).api_key == "sk-zai"


def test_missing_key_raises(tmp_path):
    p = write_cfg(tmp_path, {"model": "x"})
    with pytest.raises(RuntimeError):
        Config.load(config_path=p)


def test_bom_config_loads(tmp_path):
    # Windows PowerShell 5.1 `Set-Content -Encoding utf8` prepends a UTF-8 BOM.
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"api_key": "sk-bom"}), encoding="utf-8-sig")
    assert Config.load(config_path=p).api_key == "sk-bom"


def test_bad_json_raises(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        Config.load(config_path=p)


def test_allowed_tools_sanitized(tmp_path):
    p = write_cfg(tmp_path, {"api_key": "sk-x", "allowed_tools": ["Read", "Bash", "Write"]})
    cfg = Config.load(config_path=p)
    assert "Bash" not in cfg.allowed_tools
    assert cfg.allowed_tools == ["Read", "Write"]


def test_thinking_defaults_on_max(tmp_path):
    p = write_cfg(tmp_path, {"api_key": "sk-x"})
    cfg = Config.load(config_path=p)
    assert cfg.thinking is True
    assert cfg.reasoning_effort == "max"


def test_thinking_parsed(tmp_path):
    p = write_cfg(tmp_path, {"api_key": "sk-x", "thinking": False, "reasoning_effort": "high"})
    cfg = Config.load(config_path=p)
    assert cfg.thinking is False
    assert cfg.reasoning_effort == "high"


def test_bad_reasoning_effort_falls_back(tmp_path):
    p = write_cfg(tmp_path, {"api_key": "sk-x", "reasoning_effort": "bogus"})
    assert Config.load(config_path=p).reasoning_effort == "max"


def test_deepseek_style_effort_rejected(tmp_path):
    # GLM-5.2 exposes only "high" and "max"; DeepSeek-style "low"/"medium" fall back.
    p = write_cfg(tmp_path, {"api_key": "sk-x", "reasoning_effort": "low"})
    assert Config.load(config_path=p).reasoning_effort == "max"


def test_workspace_missing_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = write_cfg(tmp_path, {"api_key": "sk-x", "workspace": str(tmp_path / "nope")})
    assert Config.load(config_path=p).workspace == Path.cwd()
