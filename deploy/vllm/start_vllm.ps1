# Start the PrescribeX vLLM server (WSL2 Ubuntu) from Windows. Keep this window open; Ctrl+C stops the server.
#   .\deploy\vllm\start_vllm.ps1                  (default settings)
#   .\deploy\vllm\start_vllm.ps1 -Attn TRITON_ATTN (if vLLM rejects the 512-wide attention heads)
# Then, in another window, with AUDIT_PROVIDER=vllm in .env:  .\run_local.ps1
param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$Attn = "",
    [double]$GpuUtil = 0.95,          # was 0.88 here, which overrode the 0.95 in start_vllm.sh (small KV cache)
    [string]$Quant = "fp8",           # fp8 = online FP8 weights (T02.2: 2.3x faster, same errors caught); "" = bf16
    [string]$KvDtype = "",            # "" = same as the weights; fp8 = FP8 KV cache (twice the doctors per GB)
    [int]$MaxSeqs = 0,                # 0 = vLLM default; requests batched together at most
    [switch]$NoPrefixCache,           # prefix caching is on by default in vLLM 0.34
    [int]$MaxLen = 8192,
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"

# The PC shuts off under full GPU load without the power cap: refuse to start without it.
$q = (nvidia-smi --query-gpu=power.limit,clocks.max.graphics,clocks.gr --format=csv,noheader,nounits) -split ","
$limit = [double]$q[0]; $clock = [double]$q[2]
Write-Host ("GPU power limit {0} W, clock now {1} MHz" -f $limit, $clock)
if ($limit -gt 400) {
    throw "GPU power limit is $limit W; it must be 400 W or less first (run the GPU power-cap startup task as admin)."
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use (a vLLM server may already be running: curl http://127.0.0.1:$Port/v1/models)."
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$wslRepo = (wsl -d $Distro -- wslpath -a ($repo -replace '\\', '/')).Trim()
$envs = "GPU_UTIL=$GpuUtil MAX_LEN=$MaxLen PORT=$Port"
if ($Attn) { $envs += " ATTN=$Attn" }
$envs += " QUANT=$Quant"            # always passed: empty means bf16
if ($KvDtype) { $envs += " KV_DTYPE=$KvDtype" }
if ($MaxSeqs -gt 0) { $envs += " MAX_SEQS=$MaxSeqs" }
if ($NoPrefixCache) { $envs += " PREFIX_CACHE=0" }
Write-Host "Starting vLLM in $Distro (first start takes a few minutes: loading 24 GB and compiling)..."
wsl -d $Distro --cd $wslRepo -- bash -lc "$envs bash deploy/vllm/start_vllm.sh"
