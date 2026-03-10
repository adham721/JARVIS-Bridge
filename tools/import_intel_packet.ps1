param(
    [string]$InputPath,
    [string]$ProjectId = "kids_pod",
    [string]$DriveRoot = "G:\My Drive"
)

$ErrorActionPreference = "Stop"

function Info([string]$message) {
    Write-Host "[INFO] $message"
}

function Fail([string]$message) {
    Write-Host "[FAIL] $message"
    exit 1
}

if (-not $InputPath) {
    Fail "Usage: .\tools\import_intel_packet.ps1 -InputPath <raw_gpt_output.txt> [-ProjectId kids_pod] [-DriveRoot 'G:\My Drive']"
}

if (-not (Test-Path $InputPath)) {
    Fail "Input file not found: $InputPath"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$inboxDir = Join-Path $DriveRoot "JARVIS_INTEL_INBOX\$ProjectId"
$outPath = Join-Path $inboxDir "intel_result.json"

if (-not (Test-Path $inboxDir)) {
    New-Item -ItemType Directory -Path $inboxDir -Force | Out-Null
}

$raw = Get-Content -Raw -Encoding utf8 $InputPath

# Strip code fences if present.
$text = $raw -replace "^\s*```(?:json)?\s*", "" -replace "\s*```\s*$", ""

# Extract the largest JSON-looking block.
$startObj = $text.IndexOf("{")
$endObj = $text.LastIndexOf("}")
$startArr = $text.IndexOf("[")
$endArr = $text.LastIndexOf("]")

$candidate = $null
if ($startObj -ge 0 -and $endObj -gt $startObj) {
    $candidate = $text.Substring($startObj, $endObj - $startObj + 1)
}
if ($startArr -ge 0 -and $endArr -gt $startArr) {
    $arrCandidate = $text.Substring($startArr, $endArr - $startArr + 1)
    if (-not $candidate -or $arrCandidate.Length -gt $candidate.Length) {
        $candidate = $arrCandidate
    }
}

if (-not $candidate) {
    Fail "No JSON block found in input text."
}

# Normalize markdown-link URLs: [https://x](https://x) -> https://x
$candidate = [regex]::Replace(
    $candidate,
    "\[(https?://[^\]\s]+)\]\(\1\)",
    '$1',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

# Validate JSON strictly.
try {
    $null = $candidate | ConvertFrom-Json
} catch {
    Fail "Invalid JSON after normalization. Ask GPT to output strict JSON only (no markdown links, no unescaped quotes). Details: $($_.Exception.Message)"
}

Set-Content -Path $outPath -Value $candidate -Encoding utf8
Info "Wrote normalized packet: $outPath"

Push-Location $repoRoot
try {
    powershell -ExecutionPolicy Bypass -File ".\tools\verify_intel_bridge.ps1" -ProjectId $ProjectId -DriveRoot $DriveRoot -RunImport
} finally {
    Pop-Location
}
