param(
    [string]$ProjectId = "kids_pod",
    [string]$DriveRoot = "G:\My Drive",
    [switch]$RunImport
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

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$outboxLatest = Join-Path $DriveRoot "JARVIS_INTEL_OUTBOX\$ProjectId\intel_input.md"
$inboxDir = Join-Path $DriveRoot "JARVIS_INTEL_INBOX\$ProjectId"
$resultPath = Join-Path $inboxDir "intel_result.json"
$markerPath = "$resultPath.imported"

Info "Repo: $repoRoot"
Info "Project: $ProjectId"
Info "Outbox latest: $outboxLatest"
Info "Inbox result: $resultPath"

if (-not (Test-Path $outboxLatest)) {
    Fail "Missing intel_input.md in outbox. Run: python jarvis_runner.py --project $ProjectId --dedup-hours 0"
}

$outboxItem = Get-Item $outboxLatest
Info "Found outbox input (bytes=$($outboxItem.Length), modified=$($outboxItem.LastWriteTime))"

if (-not (Test-Path $inboxDir)) {
    Warn "Inbox project folder does not exist yet: $inboxDir"
}

if (Test-Path $resultPath) {
    $resultItem = Get-Item $resultPath
    Info "Found intel_result.json (bytes=$($resultItem.Length), modified=$($resultItem.LastWriteTime))"
} else {
    Warn "intel_result.json not found yet. Trigger your Custom GPT with: Start"
}

if ($RunImport) {
    if (-not (Test-Path $resultPath)) {
        Fail "Cannot run import because intel_result.json is missing."
    }

    Info "Running JARVIS import cycle..."
    Push-Location $repoRoot
    try {
        & $python "jarvis_runner.py" "--project" $ProjectId "--dedup-hours" "0"
    } finally {
        Pop-Location
    }

    if (Test-Path $markerPath) {
        $markerItem = Get-Item $markerPath
        Info "Import marker exists: $markerPath (modified=$($markerItem.LastWriteTime))"
        Info "Import verification passed."
    } else {
        Warn "Import marker not found yet: $markerPath"
        Warn "Check runner output for parse errors in intel_result.json."
    }
}
