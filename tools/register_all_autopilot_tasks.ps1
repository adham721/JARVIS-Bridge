param(
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [string]$ProjectsDir = "",
    [string]$TaskPrefix = "JARVIS-Autopilot",
    [switch]$IncludeDisabled,
    [switch]$DryRun,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($ProjectsDir)) {
    $ProjectsDir = Join-Path $repoRoot "projects"
} elseif (-not [System.IO.Path]::IsPathRooted($ProjectsDir)) {
    $ProjectsDir = Join-Path $repoRoot $ProjectsDir
}

$registerScript = Join-Path $repoRoot "tools\register_project_autopilot_task.ps1"
if (-not (Test-Path $registerScript)) {
    throw "Missing script: $registerScript"
}

$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$py = @"
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
projects_dir = Path(sys.argv[2])
include_disabled = str(sys.argv[3]).strip().lower() in {'1', 'true', 'yes', 'on'}
sys.path.insert(0, str(repo))
from jarvis_engine.profiles import load_profiles  # type: ignore

profiles = load_profiles(projects_dir, include_disabled=include_disabled)
print(json.dumps([p.project_id for p in profiles], ensure_ascii=False))
"@

$projectIdsRaw = & $python -c $py $repoRoot $ProjectsDir ([string]$IncludeDisabled.IsPresent)
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read project profiles from $ProjectsDir"
}
$projectIdsJson = (($projectIdsRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
$decoded = $projectIdsJson | ConvertFrom-Json
$projectIds = @()
if ($decoded -is [System.Array]) {
    $projectIds = @($decoded)
} elseif ($null -ne $decoded -and -not [string]::IsNullOrWhiteSpace([string]$decoded)) {
    $projectIds = @([string]$decoded)
}
if (-not $projectIds -or $projectIds.Count -eq 0) {
    throw "No projects found in $ProjectsDir (IncludeDisabled=$IncludeDisabled)"
}

$results = @()
foreach ($projectId in $projectIds) {
    $taskName = "$TaskPrefix-$projectId"
    if ($DryRun) {
        $results += [PSCustomObject]@{
            task_name = $taskName
            project_id = [string]$projectId
            dry_run = $true
            drive_root = $DriveRoot
            cycle_minutes = $CycleMinutes
            include_disabled = [bool]$IncludeDisabled
            run_now = [bool]$RunNow
        }
        continue
    }
    $args = @{
        ProjectId = [string]$projectId
        DriveRoot = $DriveRoot
        CycleMinutes = $CycleMinutes
        TaskName = $taskName
    }
    if ($RunNow) { $args.RunNow = $true }

    $output = & $registerScript @args | ConvertFrom-Json
    $results += $output
}

$results | ConvertTo-Json -Depth 5
