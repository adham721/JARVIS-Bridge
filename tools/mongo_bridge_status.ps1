param(
    [string]$ProjectId = "cat_pod_us"
)

$ErrorActionPreference = "Stop"

function Info([string]$Message) {
    Write-Host "[INFO] $Message"
}

function Warn([string]$Message) {
    Write-Host "[WARN] $Message"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $repoRoot ".env"
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

if (-not (Test-Path $envPath)) {
    Warn ".env not found at $envPath"
    exit 1
}

$raw = Get-Content -Raw $envPath
function Get-EnvValue([string]$Key) {
    $pattern = "(?m)^" + [regex]::Escape($Key) + "=(.*)$"
    $m = [regex]::Match($raw, $pattern)
    if ($m.Success) {
        return $m.Groups[1].Value.Trim()
    }
    return ""
}

$enabled = Get-EnvValue "JARVIS_MONGO_BRIDGE_ENABLED"
$uri = Get-EnvValue "JARVIS_MONGO_URI"
$db = Get-EnvValue "JARVIS_MONGO_DB"
$jobs = Get-EnvValue "JARVIS_MONGO_JOBS_COLLECTION"
$packets = Get-EnvValue "JARVIS_MONGO_INTEL_COLLECTION"
$bridgeKey = Get-EnvValue "JARVIS_BRIDGE_API_KEY"

Info "Mongo bridge enabled: $enabled"
Info "Mongo db: $db | jobs: $jobs | packets: $packets"
Info ("Bridge API key set: " + ($(if ($bridgeKey) { "yes" } else { "no" })))
if (-not $uri) {
    Warn "JARVIS_MONGO_URI is empty in .env"
}

Push-Location $repoRoot
try {
    & $python ".\tools\mongo_bridge_diag.py" "--project" $ProjectId
    if ($LASTEXITCODE -ne 0) {
        Warn "mongo_bridge_diag.py returned code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
