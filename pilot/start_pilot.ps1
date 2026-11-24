# Start PrescribeX for the doctor pilot: vLLM (both model layers), the audit server, the app with doctor sign-in,
# and the public HTTPS link (Cloudflare Tunnel). Each part opens in its own window: keep them open.
#   .\pilot\start_pilot.ps1          (or double-click "Start PrescribeX Pilot.bat")
# Stop everything: .\pilot\stop_pilot.ps1
$ErrorActionPreference = "Stop"
$Pilot = $PSScriptRoot
$Root = Split-Path $Pilot -Parent
$cf = @("C:\Program Files (x86)\cloudflared\cloudflared.exe", "C:\Program Files\cloudflared\cloudflared.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1

function Test-Url($url) { try { $null = Invoke-RestMethod $url -TimeoutSec 2; $true } catch { $false } }
function Wait-For($url, $what, $seconds) {
    Write-Host "Waiting for $what" -NoNewline
    for ($i = 0; $i -lt $seconds; $i++) { if (Test-Url $url) { Write-Host " ready."; return }; if ($i % 10 -eq 9) { Write-Host "." -NoNewline }; Start-Sleep 1 }
    throw "$what did not start in $seconds s. Look at its window for the error."
}

# 0. Checks
if (-not $cf) { throw "cloudflared is not installed. Run: winget install --id Cloudflare.cloudflared -e" }
if (-not (Test-Path (Join-Path $Pilot "doctors.json"))) { Write-Warning 'No doctors yet. Add one: .\pilot\add_doctor.ps1 -Name "Dr Rahman"' }
$gpu = (nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits).Trim()
if ([double]$gpu -gt 400) { throw "GPU power limit is $gpu W; it must be 400 W or less (the PC shuts off otherwise). Run the GPU power-cap startup task first." }

# 1. vLLM (in WSL2; its window must stay open or WSL stops it)
if (Test-Url "http://127.0.0.1:8000/v1/models") { Write-Host "vLLM already running." }
else {
    Start-Process powershell -WorkingDirectory $Root -WindowStyle Minimized -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command",
        "`$host.UI.RawUI.WindowTitle='PrescribeX vLLM - keep open'; & '$Root\deploy\vllm\start_vllm.ps1'"
    Wait-For "http://127.0.0.1:8000/v1/models" "vLLM (loads the model, 2-4 minutes)" 900
}

# 2. Audit server on vLLM
if (Test-Url "http://127.0.0.1:8765/health") { Write-Host "Audit server already running." }
else {
    Start-Process powershell -WorkingDirectory $Root -WindowStyle Minimized -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$Pilot\run_audit_server.ps1`""
    Wait-For "http://127.0.0.1:8765/health" "audit server" 300
}

# 3. App (built once, then served with doctor sign-in)
$app = Join-Path $Root "prescribex-app"
if (-not (Test-Path (Join-Path $app "node_modules"))) { Push-Location $app; npm install; Pop-Location }
if (-not (Test-Url "http://127.0.0.1:4173/pilot/login")) {
    Push-Location $app
    try { npm run build; if ($LASTEXITCODE) { throw "App build failed." } } finally { Pop-Location }
    Start-Process powershell -WorkingDirectory $app -WindowStyle Minimized -ArgumentList "-NoExit", "-Command",
        "`$host.UI.RawUI.WindowTitle='PrescribeX app - keep open'; `$env:PILOT_MODE='1'; npx vite preview"
    Wait-For "http://127.0.0.1:4173/pilot/login" "app" 120
}

# 4. Public HTTPS link (Cloudflare quick tunnel: a new https://<words>.trycloudflare.com address each start)
$cfLog = Join-Path $Pilot ("cloudflared_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))   # new file each start: never read an old link
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Get-ChildItem $Pilot -Filter "cloudflared*.log" | Where-Object LastWriteTime -lt (Get-Date).AddDays(-7) | Remove-Item -ErrorAction SilentlyContinue
Start-Process powershell -WindowStyle Minimized -ArgumentList "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle='PrescribeX link - keep open'; & '$cf' tunnel --no-autoupdate --url http://127.0.0.1:4173 --logfile '$cfLog'"
$link = $null
for ($i = 0; $i -lt 60 -and -not $link; $i++) {
    Start-Sleep 1
    if (Test-Path $cfLog) { $m = Select-String -Path $cfLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($m) { $link = $m.Matches[0].Value + "/" } }
}
if (-not $link) { throw "The Cloudflare link did not start. See $cfLog" }
$link | Out-File (Join-Path $Pilot "link.txt") -Encoding ascii

# Keep the PC awake while the pilot runs (on mains power).
powercfg /change standby-timeout-ac 0 | Out-Null

Write-Host ""
Write-Host "PrescribeX pilot is running." -ForegroundColor Green
Write-Host "Doctors' link: $link"
Write-Host "Doctors sign in with their own access code. Add one: .\pilot\add_doctor.ps1 -Name ""Dr ..."""
Write-Host "Keep the three PrescribeX windows open. Stop everything: .\pilot\stop_pilot.ps1"
Start-Process $link
