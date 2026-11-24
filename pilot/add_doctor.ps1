# Give a pilot doctor a personal access code (or take it away).
#   .\pilot\add_doctor.ps1 -Name "Dr Rahman"        -> prints the code once; send it to the doctor privately
#   .\pilot\add_doctor.ps1 -Name "Dr Rahman" -Remove -> the code stops working at once (also ends their session)
#   .\pilot\add_doctor.ps1 -List
# Codes are stored only as salted hashes in pilot\doctors.json (never committed). A lost code: run it again for a new one.
param([string]$Name, [switch]$Remove, [switch]$List)
$ErrorActionPreference = "Stop"
$file = Join-Path $PSScriptRoot "doctors.json"
$docs = [ordered]@{}
if (Test-Path $file) {
    $json = Get-Content $file -Raw | ConvertFrom-Json
    foreach ($p in $json.PSObject.Properties) { $docs[$p.Name] = $p.Value }
}
function Save-Doctors { ($docs | ConvertTo-Json -Depth 3) | Out-File $file -Encoding ascii }

if ($List) { foreach ($k in $docs.Keys) { "{0,-14} {1}" -f $k, $docs[$k].name }; return }
if (-not $Name) { throw 'Give a name: .\pilot\add_doctor.ps1 -Name "Dr Rahman"' }
$id = ($Name.ToLower() -replace '^dr\.?\s*', '' -replace '[^a-z0-9]+', '_').Trim('_')
if (-not $id) { throw "Use letters in the name." }

if ($Remove) {
    if (-not $docs.Contains($id)) { throw "No doctor '$Name' (id $id). See: .\pilot\add_doctor.ps1 -List" }
    $docs.Remove($id); Save-Doctors
    Write-Host "Removed $Name. Their code no longer works."
    return
}

$alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"     # no 0/O, 1/I/L: easy to read out
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 10; $rng.GetBytes($bytes)
$code = -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
$saltBytes = New-Object byte[] 16; $rng.GetBytes($saltBytes)
$salt = -join ($saltBytes | ForEach-Object { $_.ToString("x2") })
$sha = [System.Security.Cryptography.SHA256]::Create()
$hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($salt + $code)) | ForEach-Object { $_.ToString("x2") })
$docs[$id] = [ordered]@{ name = $Name; salt = $salt; hash = $hash; added = (Get-Date -Format s) }
Save-Doctors

$link = if (Test-Path (Join-Path $PSScriptRoot "link.txt")) { (Get-Content (Join-Path $PSScriptRoot "link.txt") -Raw).Trim() } else { "(start the pilot to get the link)" }
Write-Host ""
Write-Host "Doctor:      $Name"
Write-Host "Access code: $($code.Substring(0,5))-$($code.Substring(5))"
Write-Host "Link:        $link"
Write-Host ""
Write-Host "Send the code privately (phone call or a private message). It is shown only now."
