param(
    [Parameter(Mandatory = $true)]
    [string]$MongoUri,
    [string]$MongoDb = "jarvis_intel",
    [string]$JobsCollection = "intel_jobs",
    [string]$PacketsCollection = "intel_packets",
    [string]$BridgeApiKey = ""
)

$ErrorActionPreference = "Stop"

function Set-Or-AppendEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $escaped = [regex]::Escape($Key)
    $content = Get-Content -Raw $Path
    if ($content -match "(?m)^$escaped=") {
        $updated = [regex]::Replace($content, "(?m)^$escaped=.*$", "$Key=$Value")
        Set-Content -Path $Path -Value $updated -Encoding UTF8
    } else {
        Add-Content -Path $Path -Value "$Key=$Value" -Encoding UTF8
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $repoRoot ".env"
if (-not (Test-Path $envPath)) {
    throw ".env file not found at $envPath"
}

if (-not $BridgeApiKey) {
    $raw = Get-Content -Raw $envPath
    $m = [regex]::Match($raw, "(?m)^JARVIS_BRIDGE_API_KEY=(.*)$")
    if ($m.Success) {
        $BridgeApiKey = $m.Groups[1].Value.Trim()
    }
    if (-not $BridgeApiKey) {
        $BridgeApiKey = [guid]::NewGuid().ToString("N")
    }
}

Set-Or-AppendEnvValue -Path $envPath -Key "JARVIS_MONGO_BRIDGE_ENABLED" -Value "1"
Set-Or-AppendEnvValue -Path $envPath -Key "JARVIS_MONGO_URI" -Value $MongoUri
Set-Or-AppendEnvValue -Path $envPath -Key "JARVIS_MONGO_DB" -Value $MongoDb
Set-Or-AppendEnvValue -Path $envPath -Key "JARVIS_MONGO_JOBS_COLLECTION" -Value $JobsCollection
Set-Or-AppendEnvValue -Path $envPath -Key "JARVIS_MONGO_INTEL_COLLECTION" -Value $PacketsCollection
Set-Or-AppendEnvValue -Path $envPath -Key "JARVIS_BRIDGE_API_KEY" -Value $BridgeApiKey

Write-Host "[OK] Mongo bridge enabled in .env"
Write-Host "[OK] DB=$MongoDb jobs=$JobsCollection packets=$PacketsCollection"
Write-Host "[OK] Bridge key set"
