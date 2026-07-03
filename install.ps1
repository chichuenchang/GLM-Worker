# install.ps1 — set up glm-worker-mcp and register it with Claude Code.
$ErrorActionPreference = "Stop"
$proj = $PSScriptRoot
$venv = Join-Path $proj ".venv"
$cfgDir = Join-Path $HOME ".glm-mcp"
$cfg = Join-Path $cfgDir "config.json"
$skillsDir = Join-Path $HOME ".claude\skills\glm-worker"

Write-Host "glm-worker-mcp installer"

if (-not (Test-Path $venv)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) { uv venv $venv }
    else {
        & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ required (pyproject requires-python >=3.10)" }
        python -m venv $venv
    }
}
$py = Join-Path $venv "Scripts\python.exe"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $py -e $proj
} else {
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -e $proj
}
$cli = Join-Path $venv "Scripts\glm-mcp.exe"

New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
if (-not (Test-Path $cfg)) {
    $plat = Read-Host "Key platform: [1] z.ai (international)  [2] bigmodel.cn (mainland China)  (1/2, default 1)"
    $baseUrl = if ($plat.Trim() -eq "2") { "https://open.bigmodel.cn/api/paas/v4" }
               else { "https://api.z.ai/api/paas/v4" }
    $key = Read-Host "Paste GLM API key (Enter to skip)"
    if (-not $key) { $key = "PASTE_YOUR_GLM_KEY_HERE" }
    $json = @{ api_key = $key; model = "glm-5.2"; max_turns = 50; workspace = "";
       allowed_tools = @("Read", "Write", "Edit", "Glob", "Grep"); denylist = @();
       base_url = $baseUrl; thinking = $true; reasoning_effort = "max" } |
        ConvertTo-Json
    # Not Set-Content -Encoding utf8: Windows PowerShell 5.1 would prepend a BOM.
    [System.IO.File]::WriteAllText($cfg, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "wrote $cfg (base_url=$baseUrl)"
}

if (Get-Command claude -ErrorAction SilentlyContinue) {
    $listed = claude mcp list 2>$null
    if ($listed -notmatch "\bglm\b") { claude mcp add glm -s user -- $cli }
    else { Write-Host "already registered" }
} else {
    Write-Host "claude CLI not found; register manually: claude mcp add glm -- $cli"
}

New-Item -ItemType Directory -Force -Path (Split-Path $skillsDir) | Out-Null
Copy-Item -Recurse -Force (Join-Path $proj "skills\glm-worker") $skillsDir
Write-Host "deployed skill to $skillsDir"
Write-Host "Done. Restart Claude Code to load the new MCP server."
