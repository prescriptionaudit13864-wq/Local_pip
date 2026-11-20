# Install a newly trained fine-tune into the app in one step (and undo it the same way).
#
#   .\install_model.ps1                     installs mapping_v5 (round-2 v7 data)
#   .\install_model.ps1 -Model <folder>     installs another merged model folder
#   .\install_model.ps1 -Undo               puts back the model and rule list that were there before
#
# What it changes, all together (a v7 model with the old rule list, or the reverse, gives wrong prompts):
#   1. .env HF_ADAPTER                       -> the new run's adapter_gold (HF_MODEL stays the base model, so the
#                                               clinical reviewer can switch the adapter off; old line kept as comment)
#   2. audit_pipeline/configs/finetune_taxonomy.json  -> LLM_Trainer/data/finetune_taxonomy_v7.json
#      (asks for a "why" per error, and gives the medicine check Past_History and Drug_Facts)
#   3. .env FINETUNE_MODEL_FIRST_EXCLUDE=none -> meal timing is judged by the model again (v7 data teaches it)
# Backups: .env.before_install and configs/finetune_taxonomy.before_install.json
# Then restart with .\run_local.ps1
param(
    [string]$Model = "C:/Users/HEAT/Documents/LLM_Trainer/outputs/unsloth__gemma-4-12B-it-qat-q4_0-unquantized__mapping_v5/merged",
    [string]$Taxonomy = "C:/Users/HEAT/Documents/LLM_Trainer/data/finetune_taxonomy_v7.json",
    [switch]$Undo
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$envFile = Join-Path $root ".env"
$taxFile = Join-Path $root "audit_pipeline/configs/finetune_taxonomy.json"
$envBackup = Join-Path $root ".env.before_install"
$taxBackup = Join-Path $root "audit_pipeline/configs/finetune_taxonomy.before_install.json"

if ($Undo) {
    if (-not (Test-Path $envBackup) -or -not (Test-Path $taxBackup)) { throw "No backups found: nothing to undo." }
    Copy-Item $envBackup $envFile -Force
    Copy-Item $taxBackup $taxFile -Force
    Write-Host "Restored the previous model and rule list. Restart with .\run_local.ps1" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path (Join-Path $Model "config.json"))) { throw "No merged model at $Model (config.json missing). Has training finished?" }
if (-not (Test-Path $Taxonomy)) { throw "Rule list not found: $Taxonomy" }

Copy-Item $envFile $envBackup -Force
Copy-Item $taxFile $taxBackup -Force

# Two-layer app (since 2026-11-14): HF_MODEL stays the BASE model and the fine-tune is installed as HF_ADAPTER, so the
# clinical reviewer can switch the adapter off. The adapter is the run's adapter_gold folder, next to merged/.
$adapter = (Join-Path (Split-Path $Model -Parent) "adapter_gold") -replace '\\', '/'
if (-not (Test-Path (Join-Path $adapter "adapter_config.json"))) { throw "No adapter at $adapter (adapter_config.json missing)." }
$lines = Get-Content $envFile -Encoding utf8
$out = New-Object System.Collections.Generic.List[string]
$adapterDone = $false; $excludeDone = $false
foreach ($line in $lines) {
    if (-not $adapterDone -and $line -match '^HF_ADAPTER=') {
        $out.Add("HF_ADAPTER=$adapter")
        $out.Add("# previous: $line")
        $adapterDone = $true
        continue
    }
    if ($line -match '^# previous: HF_ADAPTER=') { continue }   # keep only the latest previous adapter
    if ($line -match '^FINETUNE_MODEL_FIRST_EXCLUDE=') { $out.Add("FINETUNE_MODEL_FIRST_EXCLUDE=none"); $excludeDone = $true; continue }
    $out.Add($line)
}
if (-not $adapterDone) { $out.Add("HF_ADAPTER=$adapter") }
if (-not $excludeDone) { $out.Add("FINETUNE_MODEL_FIRST_EXCLUDE=none  # v7 data teaches meal timing; set to Rx.timing to hand it back to the rule engine") }
[System.IO.File]::WriteAllLines($envFile, $out, (New-Object System.Text.UTF8Encoding($false)))
Copy-Item $Taxonomy $taxFile -Force

Write-Host "Installed:" -ForegroundColor Green
Write-Host "  adapter   $adapter   (base model HF_MODEL unchanged, so the clinical reviewer keeps working)"
Write-Host "  rule list $Taxonomy"
Write-Host "Restart the app with .\run_local.ps1   (undo: .\install_model.ps1 -Undo)"
