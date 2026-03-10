param(
    [string]$InputPath = "",
    [string]$ProjectId = "kids_pod",
    [string]$DriveRoot = "G:\My Drive",
    [int]$PollSeconds = 10,
    [int]$TimeoutMinutes = 60,
    [switch]$ImportExisting,
    [switch]$RunOnce,
    [switch]$ArchiveOnSuccess
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

function Get-FileHashOrEmpty([string]$path) {
    if (-not (Test-Path $path)) {
        return ""
    }
    try {
        return (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLowerInvariant()
    } catch {
        # File can be briefly locked while editor/save is in progress.
        return ""
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$importScript = Join-Path $PSScriptRoot "import_intel_packet.ps1"
if (-not (Test-Path $importScript)) {
    Fail "Missing helper script: $importScript"
}

$inboxDir = Join-Path $DriveRoot "JARVIS_INTEL_INBOX\$ProjectId"
if (-not (Test-Path $inboxDir)) {
    New-Item -ItemType Directory -Path $inboxDir -Force | Out-Null
}

if (-not $InputPath) {
    $InputPath = Join-Path $inboxDir "raw_intel.txt"
}

$inputDir = Split-Path -Parent $InputPath
if ($inputDir -and -not (Test-Path $inputDir)) {
    New-Item -ItemType Directory -Path $inputDir -Force | Out-Null
}

Info "Project: $ProjectId"
Info "Watching raw intel: $InputPath"
Info "Inbox dir: $inboxDir"
Info "Poll=${PollSeconds}s | Timeout=${TimeoutMinutes}m"
Info "Paste GPT output into this file, save, and watcher will auto-import."

$baselineHash = Get-FileHashOrEmpty $InputPath
Info "Baseline hash: $baselineHash"

if ($ImportExisting -and $baselineHash) {
    Info "ImportExisting is enabled. Importing current file..."
    Push-Location $repoRoot
    try {
        powershell -ExecutionPolicy Bypass -File $importScript -InputPath $InputPath -ProjectId $ProjectId -DriveRoot $DriveRoot
    } finally {
        Pop-Location
    }
    if ($RunOnce) {
        exit 0
    }
}

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
while ((Get-Date) -lt $deadline) {
    $currentHash = Get-FileHashOrEmpty $InputPath
    if ($currentHash -and $currentHash -ne $baselineHash) {
        Info "Detected raw intel update: $currentHash"
        $ok = $true
        Push-Location $repoRoot
        try {
            powershell -ExecutionPolicy Bypass -File $importScript -InputPath $InputPath -ProjectId $ProjectId -DriveRoot $DriveRoot
        } catch {
            $ok = $false
            Warn "Import failed. Fix raw JSON and save file again. Error: $($_.Exception.Message)"
        } finally {
            Pop-Location
        }

        if ($ok) {
            if ($ArchiveOnSuccess) {
                $archiveDir = Join-Path $inboxDir "_raw_archive"
                if (-not (Test-Path $archiveDir)) {
                    New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
                }
                $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
                $archivePath = Join-Path $archiveDir "raw_intel-$stamp.txt"
                Copy-Item -Path $InputPath -Destination $archivePath -Force
                Info "Archived raw input: $archivePath"
            }
            if ($RunOnce) {
                Info "RunOnce complete."
                exit 0
            }
        }
        $baselineHash = Get-FileHashOrEmpty $InputPath
    }
    Start-Sleep -Seconds $PollSeconds
}

Warn "Timeout with no new raw intel update."
exit 2
