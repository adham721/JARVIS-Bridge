param(
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [string]$ProjectsDir = "",
    [switch]$IncludeDisabled,
    [string]$Project = "",
    [int]$MaxCycles = 0,
    [int]$RetryCount = 3,
    [int]$RetryBackoffSeconds = 20,
    [int]$JarvisRunnerTimeoutMinutes = 45,
    [int]$EtsyFallbackMaxResults = 30,
    [double]$EtsyFallbackMinIntervalHours = 6,
    [int]$AmazonFallbackMaxResults = 30,
    [double]$AmazonFallbackMinIntervalHours = 6,
    [int]$YouTubeFallbackMaxResults = 30,
    [double]$YouTubeFallbackMinIntervalHours = 6,
    [int]$InstagramFallbackMaxResults = 30,
    [double]$InstagramFallbackMinIntervalHours = 6,
    [int]$SocialFallbackMaxResults = 20,
    [double]$SocialFallbackMinIntervalHours = 6,
    [double]$SocialFallbackBlockThreshold = 0.25,
    [switch]$RunHealthSnapshotEachCycle = $true,
    [switch]$RunExecutionBoardEachCycle = $true,
    [switch]$RunCycleDeltaSummaryEachCycle = $true,
    [switch]$RunLongRunningAlertEachCycle = $true,
    [int]$LongRunningAlertMinutes = 90,
    [int]$LongRunningAlertCooldownMinutes = 60,
    [switch]$LongRunningAlertSendTelegram = $true,
    [int]$LongRunningAlertMaxRuns = 8,
    [switch]$EnableStaleRunCleanup = $true,
    [int]$StaleRunCleanupMinutes = 180,
    [int]$ProjectStaleRunCleanupMinutes = 60
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$message, [string]$level = "INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts][$level][master] $message"
    Write-Host $line
    Add-Content -Path $script:LogPath -Value $line -Encoding utf8
}

function Get-ProjectIds(
    [string]$pythonExe,
    [string]$repoRootPath,
    [string]$projectsPath,
    [bool]$includeDisabledProfiles,
    [string]$singleProject
) {
    if (-not [string]::IsNullOrWhiteSpace($singleProject)) {
        return @($singleProject)
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
    $raw = & $pythonExe -c $py $repoRootPath $projectsPath ([string]$includeDisabledProfiles)
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to load profiles from $projectsPath"
    }
    $jsonText = (($raw | ForEach-Object { [string]$_ }) -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($jsonText)) {
        return @()
    }
    $decoded = $jsonText | ConvertFrom-Json
    if ($decoded -is [System.Array]) {
        return @($decoded | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    if ($null -ne $decoded -and -not [string]::IsNullOrWhiteSpace([string]$decoded)) {
        return @([string]$decoded)
    }
    return @()
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$defaultProjectsDir = Join-Path $repoRoot "projects"
if ([string]::IsNullOrWhiteSpace($ProjectsDir)) {
    $ProjectsDir = $defaultProjectsDir
} elseif (-not [System.IO.Path]::IsPathRooted($ProjectsDir)) {
    $ProjectsDir = Join-Path $repoRoot $ProjectsDir
}

$startupWarnings = @()
if (-not (Test-Path $DriveRoot)) {
    $driveCandidate = [string]::Concat([string]$DriveRoot, " Drive")
    if ((-not [string]::IsNullOrWhiteSpace($DriveRoot)) -and (Test-Path $driveCandidate)) {
        $startupWarnings += "DriveRoot path not found: $DriveRoot | auto-corrected to: $driveCandidate"
        $DriveRoot = $driveCandidate
    } else {
        $startupWarnings += "DriveRoot path not found: $DriveRoot"
    }
}
if (-not (Test-Path $ProjectsDir)) {
    if (($ProjectsDir -ne $defaultProjectsDir) -and (Test-Path $defaultProjectsDir)) {
        $startupWarnings += "ProjectsDir not found: $ProjectsDir | auto-corrected to default: $defaultProjectsDir"
        $ProjectsDir = $defaultProjectsDir
    } else {
        $startupWarnings += "ProjectsDir not found: $ProjectsDir"
    }
}

$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$projectRunnerScript = Join-Path $repoRoot "tools\run_project_autopilot.ps1"
$healthScript = Join-Path $repoRoot "tools\run_health_snapshot_alert.ps1"
$executionBoardScript = Join-Path $repoRoot "tools\master_execution_board.py"
$cleanupScript = Join-Path $repoRoot "tools\cleanup_stale_runs.py"
$cycleDeltaSummaryScript = Join-Path $repoRoot "tools\master_cycle_delta_summary.py"
$longRunningAlertScript = Join-Path $repoRoot "tools\long_running_runs_alert.py"
if (-not (Test-Path $projectRunnerScript)) {
    throw "Missing script: $projectRunnerScript"
}

$runtimeDir = Join-Path $repoRoot "data\runtime"
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$script:LogPath = Join-Path $logDir "master_autopilot.log"
Write-Log "Master autopilot started. cycle=${CycleMinutes}m max_cycles=$MaxCycles include_disabled=$IncludeDisabled"
Write-Log "ProjectsDir=$ProjectsDir DriveRoot=$DriveRoot"
foreach ($warnLine in $startupWarnings) {
    Write-Log $warnLine "WARN"
}
if ($EnableStaleRunCleanup -and (Test-Path $cleanupScript)) {
    Write-Log "Stale-run cleanup: enabled (global=${StaleRunCleanupMinutes}m, project=${ProjectStaleRunCleanupMinutes}m)"
} elseif ($EnableStaleRunCleanup) {
    Write-Log "Stale-run cleanup is enabled but script not found: $cleanupScript" "WARN"
} else {
    Write-Log "Stale-run cleanup: disabled"
}
if ($RunCycleDeltaSummaryEachCycle -and (Test-Path $cycleDeltaSummaryScript)) {
    Write-Log "Cycle delta summary: enabled"
} elseif ($RunCycleDeltaSummaryEachCycle) {
    Write-Log "Cycle delta summary is enabled but script not found: $cycleDeltaSummaryScript" "WARN"
} else {
    Write-Log "Cycle delta summary: disabled"
}
if ($RunLongRunningAlertEachCycle -and (Test-Path $longRunningAlertScript)) {
    Write-Log "Long-running alert: enabled (threshold=${LongRunningAlertMinutes}m cooldown=${LongRunningAlertCooldownMinutes}m telegram=$([bool]$LongRunningAlertSendTelegram))"
} elseif ($RunLongRunningAlertEachCycle) {
    Write-Log "Long-running alert is enabled but script not found: $longRunningAlertScript" "WARN"
} else {
    Write-Log "Long-running alert: disabled"
}
if ($RunExecutionBoardEachCycle -and (Test-Path $executionBoardScript)) {
    Write-Log "Execution board: enabled"
} elseif ($RunExecutionBoardEachCycle) {
    Write-Log "Execution board is enabled but script not found: $executionBoardScript" "WARN"
} else {
    Write-Log "Execution board: disabled"
}

$cycle = 0
while ($true) {
    if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
        break
    }

    $cycle += 1
    $cycleStartUtc = (Get-Date).ToUniversalTime().ToString("o")
    Write-Log "Master cycle $cycle started."

    if ($EnableStaleRunCleanup -and (Test-Path $cleanupScript)) {
        try {
            $globalCutoff = [math]::Max(30, [int]$StaleRunCleanupMinutes)
            $cleanupRaw = & $python $cleanupScript `
                "--db-path" (Join-Path $repoRoot "data\jarvis_ops.db") `
                "--older-than-minutes" ([string]$globalCutoff) `
                "--tag" "master_cycle_pre"
            $cleanupJsonText = (($cleanupRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
            if (-not [string]::IsNullOrWhiteSpace($cleanupJsonText)) {
                $cleanupPayload = $cleanupJsonText | ConvertFrom-Json
                $matched = [int]($cleanupPayload.matched_count)
                $updated = [int]($cleanupPayload.updated_count)
                Write-Log "Global stale cleanup completed (matched=$matched updated=$updated cutoff=${globalCutoff}m)."
            }
        } catch {
            Write-Log ("Global stale cleanup failed: " + $_.Exception.Message) "WARN"
        }
    }

    $projectIds = @()
    try {
        $projectIds = Get-ProjectIds `
            -pythonExe $python `
            -repoRootPath $repoRoot `
            -projectsPath $ProjectsDir `
            -includeDisabledProfiles ([bool]$IncludeDisabled) `
            -singleProject ([string]$Project)
    } catch {
        Write-Log ("Failed to load project list: " + $_.Exception.Message) "ERROR"
    }

    if (-not $projectIds -or $projectIds.Count -eq 0) {
        Write-Log "No projects resolved for this cycle." "WARN"
    } else {
        foreach ($projectId in $projectIds) {
            $projectKey = [string]$projectId
            if ([string]::IsNullOrWhiteSpace($projectKey)) {
                continue
            }

            Write-Log "Project cycle start: $projectKey"
            $args = @(
                "-ExecutionPolicy", "Bypass",
                "-File", $projectRunnerScript,
                "-ProjectId", $projectKey,
                "-DriveRoot", $DriveRoot,
                "-CycleMinutes", "1",
                "-MaxCycles", "1",
                "-RetryCount", ([string]$RetryCount),
                "-RetryBackoffSeconds", ([string]$RetryBackoffSeconds),
                "-JarvisRunnerTimeoutMinutes", ([string]$JarvisRunnerTimeoutMinutes),
                "-EtsyFallbackMaxResults", ([string]$EtsyFallbackMaxResults),
                "-EtsyFallbackMinIntervalHours", ([string]$EtsyFallbackMinIntervalHours),
                "-AmazonFallbackMaxResults", ([string]$AmazonFallbackMaxResults),
                "-AmazonFallbackMinIntervalHours", ([string]$AmazonFallbackMinIntervalHours),
                "-YouTubeFallbackMaxResults", ([string]$YouTubeFallbackMaxResults),
                "-YouTubeFallbackMinIntervalHours", ([string]$YouTubeFallbackMinIntervalHours),
                "-InstagramFallbackMaxResults", ([string]$InstagramFallbackMaxResults),
                "-InstagramFallbackMinIntervalHours", ([string]$InstagramFallbackMinIntervalHours),
                "-SocialFallbackMaxResults", ([string]$SocialFallbackMaxResults),
                "-SocialFallbackMinIntervalHours", ([string]$SocialFallbackMinIntervalHours),
                "-SocialFallbackBlockThreshold", ([string]$SocialFallbackBlockThreshold)
            )
            try {
                & powershell.exe @args
                $exitCode = $LASTEXITCODE
                if ($exitCode -eq 0) {
                    Write-Log "Project cycle success: $projectKey"
                } else {
                    Write-Log "Project cycle failed: $projectKey (exit=$exitCode)" "WARN"
                }
            } catch {
                Write-Log ("Project cycle exception: $projectKey | " + $_.Exception.Message) "ERROR"
            }

            if ($EnableStaleRunCleanup -and (Test-Path $cleanupScript)) {
                try {
                    $projectCutoff = [math]::Max(30, [int]$ProjectStaleRunCleanupMinutes)
                    $cleanupRaw = & $python $cleanupScript `
                        "--db-path" (Join-Path $repoRoot "data\jarvis_ops.db") `
                        "--project" $projectKey `
                        "--older-than-minutes" ([string]$projectCutoff) `
                        "--tag" ("master_project_post_" + $projectKey)
                    $cleanupJsonText = (($cleanupRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
                    if (-not [string]::IsNullOrWhiteSpace($cleanupJsonText)) {
                        $cleanupPayload = $cleanupJsonText | ConvertFrom-Json
                        $matched = [int]($cleanupPayload.matched_count)
                        $updated = [int]($cleanupPayload.updated_count)
                        if ($matched -gt 0 -or $updated -gt 0) {
                            Write-Log "Project stale cleanup ($projectKey) matched=$matched updated=$updated cutoff=${projectCutoff}m."
                        }
                    }
                } catch {
                    Write-Log ("Project stale cleanup failed ($projectKey): " + $_.Exception.Message) "WARN"
                }
            }
        }
    }

    if ($RunHealthSnapshotEachCycle -and (Test-Path $healthScript)) {
        try {
            & powershell.exe -ExecutionPolicy Bypass -File $healthScript -MaxCycles 1
            if ($LASTEXITCODE -ne 0) {
                Write-Log "Health snapshot cycle returned exit=$LASTEXITCODE" "WARN"
            } else {
                Write-Log "Health snapshot cycle completed."
            }
        } catch {
            Write-Log ("Health snapshot cycle exception: " + $_.Exception.Message) "ERROR"
        }
    }

    if ($RunExecutionBoardEachCycle -and (Test-Path $executionBoardScript)) {
        try {
            $boardRaw = & $python $executionBoardScript `
                "--projects-master" (Join-Path $repoRoot "projects_master.csv") `
                "--capacity" (Join-Path $repoRoot "data\runtime\capacity.json") `
                "--projects-dir" $ProjectsDir `
                "--data-dir" (Join-Path $repoRoot "data") `
                "--db-path" (Join-Path $repoRoot "data\jarvis_ops.db") `
                "--output-json" (Join-Path $repoRoot "data\reports\_summary\full_focus_plan_latest.json") `
                "--output-md" (Join-Path $repoRoot "data\reports\_summary\full_focus_plan_latest.md")
            if ($LASTEXITCODE -ne 0) {
                Write-Log "Execution board command returned exit=$LASTEXITCODE" "WARN"
            } else {
                $boardJsonText = (($boardRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
                if (-not [string]::IsNullOrWhiteSpace($boardJsonText)) {
                    $boardPayload = $boardJsonText | ConvertFrom-Json
                    $boardMode = [string]($boardPayload.today_mode)
                    $boardProjects = [int]($boardPayload.project_count)
                    $boardOpenTasks = [int]($boardPayload.open_tasks_total)
                    $boardReauth = @($boardPayload.projects_with_reauth).Count
                    Write-Log "Execution board updated (mode=$boardMode projects=$boardProjects open_tasks=$boardOpenTasks reauth_projects=$boardReauth)."
                }
            }
        } catch {
            Write-Log ("Execution board exception: " + $_.Exception.Message) "ERROR"
        }
    }

    if ($RunCycleDeltaSummaryEachCycle -and (Test-Path $cycleDeltaSummaryScript) -and $projectIds -and $projectIds.Count -gt 0) {
        try {
            $deltaArgs = @(
                $cycleDeltaSummaryScript,
                "--projects-dir", $ProjectsDir,
                "--data-dir", (Join-Path $repoRoot "data"),
                "--cycle-number", ([string]$cycle),
                "--cycle-start-utc", $cycleStartUtc,
                "--projects"
            )
            $deltaArgs += @($projectIds | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
            $deltaRaw = & $python @deltaArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Log "Cycle delta summary returned exit=$LASTEXITCODE" "WARN"
            } else {
                $deltaJsonText = (($deltaRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
                if (-not [string]::IsNullOrWhiteSpace($deltaJsonText)) {
                    $deltaPayload = $deltaJsonText | ConvertFrom-Json
                    $ok = [bool]($deltaPayload.ok)
                    $changedProjects = [int]($deltaPayload.changed_project_count)
                    $staleProjects = [int]($deltaPayload.stale_project_count)
                    $missingProjects = [int]($deltaPayload.missing_project_count)
                    $outputPath = [string]($deltaPayload.output_path)
                    Write-Log "Cycle delta summary completed (ok=$ok changed_projects=$changedProjects stale_projects=$staleProjects missing_projects=$missingProjects file=$outputPath)."
                }
            }
        } catch {
            Write-Log ("Cycle delta summary exception: " + $_.Exception.Message) "ERROR"
        }
    }

    if ($RunLongRunningAlertEachCycle -and (Test-Path $longRunningAlertScript)) {
        try {
            $alertArgs = @(
                $longRunningAlertScript,
                "--db-path", (Join-Path $repoRoot "data\jarvis_ops.db"),
                "--older-than-minutes", ([string]([math]::Max(1, [int]$LongRunningAlertMinutes))),
                "--cooldown-minutes", ([string]([math]::Max(1, [int]$LongRunningAlertCooldownMinutes))),
                "--state-path", (Join-Path $repoRoot "data\runtime\long_running_alert.state.json"),
                "--max-alert-runs", ([string]([math]::Max(1, [int]$LongRunningAlertMaxRuns)))
            )
            if ($LongRunningAlertSendTelegram) {
                $alertArgs += "--send-telegram"
            }

            $alertRaw = & $python @alertArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Log "Long-running alert returned exit=$LASTEXITCODE" "WARN"
            } else {
                $alertJsonText = (($alertRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
                if (-not [string]::IsNullOrWhiteSpace($alertJsonText)) {
                    $alertPayload = $alertJsonText | ConvertFrom-Json
                    $ok = [bool]($alertPayload.ok)
                    $candidateCount = [int]($alertPayload.alert_candidates_count)
                    $cooldownActive = [bool]($alertPayload.cooldown_active)
                    $alertSent = [bool]($alertPayload.alert_sent)
                    Write-Log "Long-running alert completed (ok=$ok candidates=$candidateCount cooldown_active=$cooldownActive alert_sent=$alertSent)."
                }
            }
        } catch {
            Write-Log ("Long-running alert exception: " + $_.Exception.Message) "ERROR"
        }
    }

    Write-Log "Master cycle $cycle completed."

    if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
        break
    }
    Start-Sleep -Seconds ([math]::Max(1, $CycleMinutes) * 60)
}

Write-Log "Master autopilot stopped after $cycle cycle(s)."
