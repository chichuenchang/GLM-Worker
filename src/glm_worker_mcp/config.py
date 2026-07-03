from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".glm-mcp" / "config.json"
DEFAULT_MODEL = "glm-5.2"
DEFAULT_MAX_TURNS = 50
FILES_ONLY_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"
DEFAULT_THINKING = True
DEFAULT_REASONING_EFFORT = "max"
# GLM-5.2 exposes exactly two reasoning-effort levels (unlike DeepSeek's four).
VALID_REASONING_EFFORT = ("high", "max")

logger = logging.getLogger(__name__)


@dataclass
class Config:
    api_key: str
    workspace: Path
    model: str = DEFAULT_MODEL
    max_turns: int = DEFAULT_MAX_TURNS
    allowed_tools: list = field(default_factory=lambda: list(FILES_ONLY_TOOLS))
    denylist: list = field(default_factory=list)
    base_url: str = DEFAULT_BASE_URL
    thinking: bool = DEFAULT_THINKING
    reasoning_effort: str = DEFAULT_REASONING_EFFORT

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Config":
        path = config_path or CONFIG_PATH
        data: dict = {}
        if path.exists():
            try:
                # utf-8-sig tolerates the BOM that Windows PowerShell 5.1
                # `Set-Content -Encoding utf8` prepends; plain utf-8 files
                # decode identically.
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON in {path}: {e}") from e
            if not isinstance(data, dict):
                raise RuntimeError(f"Top-level of {path} must be a JSON object")

        # GLM_API_KEY is this server's own variable; ZAI_API_KEY (z.ai,
        # international) and ZHIPUAI_API_KEY (bigmodel.cn, mainland) are the
        # platform conventions many users already export — accept as fallbacks.
        api_key = (
            os.getenv("GLM_API_KEY")
            or os.getenv("ZAI_API_KEY")
            or os.getenv("ZHIPUAI_API_KEY")
            or data.get("api_key", "")
        ).strip()
        if not api_key or api_key == "PASTE_YOUR_GLM_KEY_HERE":
            raise RuntimeError(
                f"GLM API key not configured. Set GLM_API_KEY (or ZAI_API_KEY / "
                f"ZHIPUAI_API_KEY) or edit {path}"
            )

        workspace_str = os.getenv("GLM_WORKSPACE") or data.get("workspace", "")
        if workspace_str:
            workspace = Path(os.path.expanduser(workspace_str)).resolve()
            if not workspace.exists():
                logger.warning("workspace %s missing; falling back to cwd", workspace)
                workspace = Path.cwd()
        else:
            workspace = Path.cwd()

        try:
            max_turns = int(data.get("max_turns", DEFAULT_MAX_TURNS))
        except (TypeError, ValueError):
            max_turns = DEFAULT_MAX_TURNS
        if max_turns < 1:
            max_turns = DEFAULT_MAX_TURNS

        allowed = data.get("allowed_tools", list(FILES_ONLY_TOOLS))
        if not isinstance(allowed, list) or not all(isinstance(t, str) for t in allowed):
            allowed = list(FILES_ONLY_TOOLS)
        allowed = [t for t in allowed if t in FILES_ONLY_TOOLS]
        if not allowed:
            allowed = list(FILES_ONLY_TOOLS)

        denylist = data.get("denylist", [])
        if not isinstance(denylist, list) or not all(isinstance(d, str) for d in denylist):
            denylist = []

        thinking = bool(data.get("thinking", DEFAULT_THINKING))

        effort = str(data.get("reasoning_effort", DEFAULT_REASONING_EFFORT)).strip().lower()
        if effort not in VALID_REASONING_EFFORT:
            effort = DEFAULT_REASONING_EFFORT

        return cls(
            api_key=api_key,
            workspace=workspace,
            model=str(data.get("model", DEFAULT_MODEL)),
            max_turns=max_turns,
            allowed_tools=allowed,
            denylist=denylist,
            base_url=str(data.get("base_url", DEFAULT_BASE_URL)),
            thinking=thinking,
            reasoning_effort=effort,
        )
