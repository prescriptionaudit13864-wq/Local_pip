# Windows launcher for PrescribeX with a LOCAL model (no cloud calls).
# Reads .env, starts the model server for AUDIT_PROVIDER, then the audit bridge and the app. Ctrl+C stops everything.
#   .\run_local.ps1
#
# .env picks the model:
#   AUDIT_PROVIDER=local   -> ANY Hugging Face chat model: set HF_MODEL=<hub id or folder> (downloaded and run in-process)
#                             optional HF_ADAPTER, HF_QUANT=none|4bit|8bit, HF_TOKEN for gated models.
#                             If LLM_BASE_URL is set (Ollama, LM Studio...) that server is used instead.
#   AUDIT_PROVIDER=vllm    -> both layers on a vLLM server (WSL2, deploy/vllm/start_vllm.sh): HF_MODEL = base model,
#                             VLLM_BASE_URL (default http://127.0.0.1:8000/v1), VLLM_LORA_NAME (default prescribex).
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
# The audit server loads the model itself, so it needs torch/transformers: prefer the project venv, else the PATH python.
$Python = $null
$candidates = @((Join-Path $Root ".venv\Scripts\python.exe"))
$candidates += @(Get-Command python, py -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
$candidates += "C:\Program Files\Python311\python.exe"
foreach ($cand in $candidates) {
    if (-not (Test-Path $cand)) { continue }
    $ErrorActionPreference = "Continue"
    & $cand -c "import torch, transformers" *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = "Stop"
    if ($ok) { $Python = $cand; break }
}
if (-not $Python) { throw "No Python with torch + transformers found. Install them (pip install torch transformers accelerate peft) into .venv." }

# Load .env. It always wins over values left in this PowerShell window by an earlier run
# (otherwise an old AUDIT_PROVIDER from the previous start silently overrides your edit).
$envFile = Join-Path $Root ".env"
foreach ($stale in "AUDIT_PROVIDER", "HF_MODEL", "HF_ADAPTER", "HF_QUANT", "HF_TOKEN", "HF_PORT", "LLM_BASE_URL", "LLM_MODEL", "GEMMA_SERVER_URL", "GEMMA_UNSUPPORTED", "VLLM_BASE_URL", "VLLM_LORA_NAME", "AUDIT_CONCURRENCY") {
    [Environment]::SetEnvironmentVariable($stale, $null)
}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            # Drop a trailing "  # comment" so HF_QUANT=none  # note still reads as "none".
            [Environment]::SetEnvironmentVariable($Matches[1], ($Matches[2] -replace '\s+#.*$', ''))
        }
    }
}
$provider = if ($env:AUDIT_PROVIDER) { $env:AUDIT_PROVIDER.ToLower() } else { "local" }
$env:AUDIT_PROVIDER = $provider
$env:PRESCRIPTION_DATA_DIR = Join-Path $Root "prescribex-app\backend\data"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"

function Wait-Ready($url, $what, $proc, $seconds, $log) {
    Write-Host "Waiting for $what"
    for ($i = 0; $i -lt $seconds; $i++) {
        $health = $null
        try { $health = Invoke-RestMethod $url -TimeoutSec 2 } catch {}
        if ($health -and $health.ok) { Write-Host "  ready."; return }
        # The server answers but could not load the model (wrong HF_MODEL, missing package...): retrying cannot fix it.
        if ($health -and $health.error) { throw "$what failed to start: $($health.error)" }
        if ($proc -and $proc.HasExited) {
            if ($log -and (Test-Path $log)) { Get-Content $log -Tail 8 | ForEach-Object { Write-Host "  $_" } }
            throw "$what stopped (details above and in $log)."
        }
        # Show download / loading progress from the model server log every 20 s.
        if ($log -and $i % 20 -eq 19 -and (Test-Path $log)) {
            $last = ((Get-Content $log -Tail 1) -split "`r")[-1]
            Write-Host ("  [{0}s] {1}" -f ($i + 1), $last.Substring(0, [Math]::Min(110, $last.Length)))
        }
        Start-Sleep 1
    }
    throw "$what did not become ready in $seconds s."
}

# An old server still holding a port would answer the health check and the new model would never start.
foreach ($port in 8765) {
    $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        throw "Port $port is still used by an older run (process $($busy[0].OwningProcess)). Stop it first:`n  Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force }"
    }
}
Write-Host "Provider: $provider$(if ($env:HF_MODEL -and $provider -in @('local','hf')) { ' / ' + $env:HF_MODEL })"

$procs = @()
try {
    if ($provider -in @("local", "hf")) {
        if ($env:LLM_BASE_URL) { Write-Host "Using the model server at $($env:LLM_BASE_URL)" }
        else { Write-Host "Model: $($env:HF_MODEL) (downloaded from Hugging Face on first start, then loaded by the audit server)" }
    }
    if ($provider -eq "vllm") {
        # Both layers on the vLLM server in WSL2 (deploy/vllm/start_vllm.sh must be running first).
        $vllm = if ($env:VLLM_BASE_URL) { $env:VLLM_BASE_URL } else { "http://127.0.0.1:8000/v1" }
        Write-Host "Using vLLM at $vllm (base $($env:HF_MODEL) = clinical reviewer, LoRA = fast check)"
        $up = $false
        for ($i = 0; $i -lt 600 -and -not $up; $i++) {
            try { $null = Invoke-RestMethod "$($vllm.TrimEnd('/'))/models" -TimeoutSec 2; $up = $true } catch { Start-Sleep 1 }
            if (-not $up -and $i -eq 0) { Write-Host "Waiting for the vLLM server (start it with .\deploy\vllm\start_vllm.ps1)" }
        }
        if (-not $up) { throw "No vLLM server at $vllm. Start it first: .\deploy\vllm\start_vllm.ps1" }
    }

    $bridge = Start-Process -PassThru -NoNewWindow -WorkingDirectory $Root -FilePath $Python `
        -ArgumentList "-m", "audit_pipeline.app_server", "serve" `
        -RedirectStandardOutput (Join-Path $Root "audit_server.log") -RedirectStandardError (Join-Path $Root "audit_server.err.log")
    $procs += $bridge
    Wait-Ready "http://127.0.0.1:8765/health" "audit server (loads the model first; large models take a few minutes)" $bridge 1800 (Join-Path $Root "audit_server.err.log")

    Set-Location (Join-Path $Root "prescribex-app")
    npm run dev -- --open
} finally {
    Set-Location $Root   # so ".\run_local.ps1" works again in the same window
    foreach ($p in $procs) { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
}
