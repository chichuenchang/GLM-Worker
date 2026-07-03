#!/usr/bin/env bash
# install.sh — set up glm-worker-mcp and register it with Claude Code (macOS/Linux).
set -euo pipefail
PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$PROJ/.venv"
CFG_DIR="$HOME/.glm-mcp"; CFG="$CFG_DIR/config.json"
SKILL_DST="$HOME/.claude/skills/glm-worker"

if [ ! -d "$VENV" ]; then
    if command -v uv >/dev/null 2>&1; then uv venv "$VENV"; else
        python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
            || { echo "Python 3.10+ required (pyproject requires-python >=3.10)" >&2; exit 1; }
        python3 -m venv "$VENV"
    fi
fi
if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV/bin/python" -e "$PROJ"
else
    "$VENV/bin/python" -m pip install --quiet -e "$PROJ"
fi
CLI="$VENV/bin/glm-mcp"

mkdir -p "$CFG_DIR"
if [ ! -f "$CFG" ]; then
    read -rp "Key platform: [1] z.ai (international)  [2] bigmodel.cn (mainland China)  (1/2, default 1): " PLAT < /dev/tty || true
    if [ "${PLAT:-1}" = "2" ]; then BASE_URL="https://open.bigmodel.cn/api/paas/v4"
    else BASE_URL="https://api.z.ai/api/paas/v4"; fi
    read -rsp "Paste GLM API key (Enter to skip): " KEY < /dev/tty || true; echo
    [ -z "$KEY" ] && KEY="PASTE_YOUR_GLM_KEY_HERE"
    ( umask 077; cat > "$CFG" <<EOF
{"api_key":"$KEY","model":"glm-5.2","max_turns":50,"workspace":"","allowed_tools":["Read","Write","Edit","Glob","Grep"],"denylist":[],"base_url":"$BASE_URL","thinking":true,"reasoning_effort":"max"}
EOF
)
    echo "wrote $CFG (base_url=$BASE_URL)"
fi

if command -v claude >/dev/null 2>&1; then
    claude mcp list 2>/dev/null | grep -qw glm || claude mcp add glm -s user -- "$CLI"
else
    echo "claude CLI not found; register manually: claude mcp add glm -- $CLI"
fi
mkdir -p "$(dirname "$SKILL_DST")"
cp -rf "$PROJ/skills/glm-worker" "$SKILL_DST"
echo "Done. Restart Claude Code to load the new MCP server."
