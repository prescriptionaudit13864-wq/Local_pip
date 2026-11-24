# Audit server for the pilot: .env settings, AUDIT_PROVIDER forced to vllm. Started by start_pilot.ps1.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$host.UI.RawUI.WindowTitle = "PrescribeX audit server - keep open"
foreach ($line in Get-Content (Join-Path $Root ".env")) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        [Environment]::SetEnvironmentVariable($Matches[1], ($Matches[2] -replace '\s+#.*$', ''))
    }
}
$env:AUDIT_PROVIDER = "vllm"
$env:PRESCRIPTION_DATA_DIR = Join-Path $Root "prescribex-app\backend\data"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"
# Project venv: here, or in the main checkout when this is a git worktree.
$python = @((Join-Path $Root ".venv\Scripts\python.exe"),
            (Join-Path $Root "..\..\..\.venv\Scripts\python.exe")) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { $python = "python" }
Set-Location $Root
& $python -m audit_pipeline.app_server serve
