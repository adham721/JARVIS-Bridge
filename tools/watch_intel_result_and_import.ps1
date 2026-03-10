param(
    [string]$ProjectId = "kids_pod",
    [string]$DriveRoot = "G:\My Drive",
    [int]$PollSeconds = 15,
    [int]$TimeoutMinutes = 45,
    [switch]$RegenerateOutbox
)

$ErrorActionPreference = "Stop"

function Info([string]$message) {
    Write-Host "[INFO] $message"
}

function Warn([string]$message) {
    Write-Host "[WARN] $message"
}

function Fail([string]$message) {
    Write-Host "[FAIL] $message"
    exit 1
}

function FileHashOrEmpty([string]$path) {
    if (-not (Test-Path $path)) {
        return ""
    }
    return (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLowerInvariant()
}

function ReadMarkerHash([string]$markerPath) {
    if (-not (Test-Path $markerPath)) {
        return ""
    }
    try {
        $raw = Get-Content -Raw -Encoding utf8 $markerPath
        $obj = $raw | ConvertFrom-Json
        if ($obj -and $obj.sha256) {
            return [string]$obj.sha256
        }
    } catch {
        # Ignore marker parse errors; caller falls back to file hash.
    }
    return ""
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$outboxLatest = Join-Path $DriveRoot "JARVIS_INTEL_OUTBOX\$ProjectId\intel_input.md"
$inboxResult = Join-Path $DriveRoot "JARVIS_INTEL_INBOX\$ProjectId\intel_result.json"
$inboxMarker = "$inboxResult.imported"

if ($RegenerateOutbox) {
    Info "Regenerating outbox input via jarvis_runner..."
    Push-Location $repoRoot
    try {
        & $python "jarvis_runner.py" "--project" $ProjectId "--dedup-hours" "0"
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $outboxLatest)) {
    Fail "Missing outbox input: $outboxLatest"
}

$baselineHash = ReadMarkerHash $inboxMarker
if (-not $baselineHash) {
    $baselineHash = FileHashOrEmpty $inboxResult
}

Info "Watching for new intel_result.json..."
Info "Project=$ProjectId | Poll=${PollSeconds}s | Timeout=${TimeoutMinutes}m"
Info "Outbox input: $outboxLatest"
Info "Inbox result: $inboxResult"
Info "Baseline hash: $baselineHash"
Info "Now trigger your Custom GPT with: Start"

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
while ((Get-Date) -lt $deadline) {
    $currentHash = FileHashOrEmpty $inboxResult
    if ($currentHash -and ($currentHash -ne $baselineHash)) {
        Info "Detected new/updated intel_result.json hash: $currentHash"
        Push-Location $repoRoot
        try {
            powershell -ExecutionPolicy Bypass -File ".\tools\verify_intel_bridge.ps1" -ProjectId $ProjectId -DriveRoot $DriveRoot -RunImport
            Info "Import cycle completed."
            exit 0
        } finally {
            Pop-Location
        }
    }
    Start-Sleep -Seconds $PollSeconds
}

Warn "Timeout reached with no new intel_result.json update."
Warn "If GPT returned connector errors (e.g. fileId='.'), reconnect Drive in GPT Actions and retry."
exit 2
