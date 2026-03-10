param(
    [string]$Command = "start",
    [string]$Project = "",
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [switch]$IncludeDisabled,
    [switch]$NoStartMaster,
    [int]$StaleRunMinutes = 10
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$message, [string]$level = "INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts][$level][start] $message"
}

function Get-MasterStatus() {
    try {
        $rows = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match "powershell(\.exe)?$" -and $_.CommandLine -match "run_master_autopilot\.ps1"
        }
        return @($rows)
    } catch {
        return @()
    }
}

$normalized = [string]$Command
if ([string]::IsNullOrWhiteSpace($normalized)) {
    $normalized = "start"
}
$normalized = $normalized.Trim().ToLowerInvariant()

# Support shorthand:
# - .\tools\start.ps1 kids_pod
# - .\tools\start.ps1 start kids_pod
if ($normalized -notin @("start", "status")) {
    if ([string]::IsNullOrWhiteSpace($Project)) {
        $Project = $Command
    }
    $normalized = "start"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bootstrap = Join-Path $repoRoot "tools\start_full_stack.ps1"
if (-not (Test-Path $bootstrap)) {
    throw "Missing script: $bootstrap"
}

if ($normalized -eq "status") {
    $masters = @(Get-MasterStatus)
    if ($masters.Count -eq 0) {
        Write-Log "Master status: stopped"
    } else {
        $ids = ($masters | ForEach-Object { [string]$_.ProcessId }) -join ", "
        Write-Log "Master status: running (count=$($masters.Count), pids=$ids)"
    }
    exit 0
}

if (-not [string]::IsNullOrWhiteSpace($Project)) {
    $projectToken = $Project.Trim()
    if ($projectToken -and $projectToken.ToLowerInvariant() -ne "all") {
        $profilePath = Join-Path $repoRoot ("projects\{0}.toml" -f $projectToken)
        if (-not (Test-Path $profilePath)) {
            throw "Project profile not found: $profilePath"
        }
    }
}

if (-not [string]::IsNullOrWhiteSpace($Project)) {
    $projectToken = $Project.Trim()
    if ($projectToken -and $projectToken.ToLowerInvariant() -ne "all") {
        Write-Log "Starting stack for project=$projectToken"
        & $bootstrap `
            -DriveRoot $DriveRoot `
            -CycleMinutes $CycleMinutes `
            -StaleRunMinutes ([math]::Max(1, [int]$StaleRunMinutes)) `
            -Project $projectToken `
            -IncludeDisabled:$IncludeDisabled `
            -NoStartMaster:$NoStartMaster
    } else {
        Write-Log "Starting stack for all enabled projects"
        & $bootstrap `
            -DriveRoot $DriveRoot `
            -CycleMinutes $CycleMinutes `
            -StaleRunMinutes ([math]::Max(1, [int]$StaleRunMinutes)) `
            -IncludeDisabled:$IncludeDisabled `
            -NoStartMaster:$NoStartMaster
    }
} else {
    Write-Log "Starting stack for all enabled projects"
    & $bootstrap `
        -DriveRoot $DriveRoot `
        -CycleMinutes $CycleMinutes `
        -StaleRunMinutes ([math]::Max(1, [int]$StaleRunMinutes)) `
        -IncludeDisabled:$IncludeDisabled `
        -NoStartMaster:$NoStartMaster
}
exit $LASTEXITCODE
