# Stop the doctor pilot: the public link first (doctors are cut off at once), then the app, audit server and vLLM.
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "Public link closed."
foreach ($port in 4173, 8765) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}
wsl -d Ubuntu-24.04 -- pkill -f "vllm serve" 2>$null
Get-Process powershell -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "PrescribeX*keep open" } | Stop-Process -Force
powercfg /change standby-timeout-ac 30 | Out-Null
Write-Host "Pilot stopped. The GPU is free."
