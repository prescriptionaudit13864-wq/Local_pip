# Adds the Gemma 4 models to the latest benchmark results folder, after the run that is writing it has finished.
# Also drops Llama3-Med42-8B (gated) from that folder. Run from the project root: .\benchmark\add_gemma4.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:PYTHONIOENCODING = "utf-8"

$folder = Get-ChildItem "benchmark\results" -Directory | Sort-Object Name | Select-Object -Last 1
Write-Host "Results folder: $($folder.FullName)"

# The GPU can only hold one benchmark at a time: wait for the current run to end.
while (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*run_benchmark.py*" }) {
    Write-Host ("[{0}] main benchmark still running, checking again in 2 minutes..." -f (Get-Date -Format HH:mm))
    Start-Sleep 120
}

# Models dropped from the comparison after the main run had started.
foreach ($dropped in "m42-health__Llama3-Med42-8B.json", "unsloth__Qwen2.5-32B-Instruct-bnb-4bit.json") {
    Remove-Item (Join-Path $folder.FullName $dropped) -ErrorAction SilentlyContinue
}
& ".venv\Scripts\python.exe" "benchmark\run_benchmark.py" --out $folder.FullName --models `
    unsloth/gemma-4-E4B-it `
    unsloth/gemma-4-12B-it-qat-q4_0-unquantized
