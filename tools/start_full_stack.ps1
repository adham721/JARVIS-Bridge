param(
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [switch]$IncludeDisabled,
    [string]$Project = "",
    [switch]$NoStartMaster,
    [int]$StaleRunMinutes = 10
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$message, [string]$level = "INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts][$level][full-stack] $message"
}

function Stop-ProcessTree([int]$RootPid) {
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    if (-not $all) { return }

    $childrenByParent = @{}
    foreach ($p in $all) {
        $parent = [int]$p.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parent)) {
            $childrenByParent[$parent] = New-Object System.Collections.Generic.List[int]
        }
        $childrenByParent[$parent].Add([int]$p.ProcessId)
    }

    $toStop = New-Object System.Collections.Generic.HashSet[int]
    $stack = New-Object System.Collections.Generic.Stack[int]
    $stack.Push([int]$RootPid)
    while ($stack.Count -gt 0) {
        $current = [int]$stack.Pop()
        if (-not $toStop.Add($current)) { continue }
        if ($childrenByParent.ContainsKey($current)) {
            foreach ($child in $childrenByParent[$current]) {
                $stack.Push([int]$child)
            }
        }
    }

    foreach ($procId in $toStop) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } catch {
        }
    }
}

function Quote-Arg([string]$value) {
    if ($null -eq $value) {
        return '""'
    }
    $text = [string]$value
    if ($text -notmatch '[\s"]') {
        return $text
    }
    $escaped = $text -replace '"', '\"'
    return ('"' + $escaped + '"')
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$masterScript = Join-Path $repoRoot "tools\run_master_autopilot.ps1"
$boardScript = Join-Path $repoRoot "tools\master_execution_board.py"
$cleanupScript = Join-Path $repoRoot "tools\cleanup_stale_runs.py"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if (Test-Path $cleanupScript) {
    try {
        $cleanupRaw = & $python $cleanupScript `
            "--db-path" (Join-Path $repoRoot "data\jarvis_ops.db") `
            "--older-than-minutes" ([string]([math]::Max(1, [int]$StaleRunMinutes))) `
            "--tag" "full_stack_bootstrap"
        if ($LASTEXITCODE -eq 0) {
            $payload = (($cleanupRaw | ForEach-Object { [string]$_ }) -join "`n").Trim() | ConvertFrom-Json
            Write-Log "Stale run cleanup: matched=$($payload.matched_count) updated=$($payload.updated_count)."
        }
    } catch {
        Write-Log ("Stale run cleanup failed: " + $_.Exception.Message) "WARN"
    }
}

$allPs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$masterProcs = @($allPs | Where-Object {
    $_.Name -match "powershell(\.exe)?$" -and $_.CommandLine -match "run_master_autopilot\.ps1"
})
$masterPids = @($masterProcs | ForEach-Object { [int]$_.ProcessId })

$projectProcs = @($allPs | Where-Object {
    $_.Name -match "powershell(\.exe)?$" -and $_.CommandLine -match "run_project_autopilot\.ps1"
})

$orphanCount = 0
foreach ($proc in $projectProcs) {
    $parent = [int]$proc.ParentProcessId
    if ($masterPids -contains $parent) {
        continue
    }
    $orphanCount += 1
    Write-Log "Stopping orphan project runner pid=$($proc.ProcessId) parent=$parent" "WARN"
    Stop-ProcessTree -RootPid ([int]$proc.ProcessId)
}
if ($orphanCount -gt 0) {
    Write-Log "Orphan project runners cleaned: $orphanCount"
}

if (Test-Path $boardScript) {
    try {
        & $python $boardScript `
            "--projects-master" (Join-Path $repoRoot "projects_master.csv") `
            "--capacity" (Join-Path $repoRoot "data\runtime\capacity.json") `
            "--projects-dir" (Join-Path $repoRoot "projects") `
            "--data-dir" (Join-Path $repoRoot "data") `
            "--db-path" (Join-Path $repoRoot "data\jarvis_ops.db") `
            "--output-json" (Join-Path $repoRoot "data\reports\_summary\full_focus_plan_latest.json") `
            "--output-md" (Join-Path $repoRoot "data\reports\_summary\full_focus_plan_latest.md") | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Execution board refreshed."
        }
    } catch {
        Write-Log ("Execution board refresh failed: " + $_.Exception.Message) "WARN"
    }
}

if ($NoStartMaster) {
    Write-Log "NoStartMaster set; bootstrap completed without launching master."
    exit 0
}

$allPs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$masterProcs = @($allPs | Where-Object {
    $_.Name -match "powershell(\.exe)?$" -and $_.CommandLine -match "run_master_autopilot\.ps1"
})

if ($masterProcs.Count -gt 0) {
    Write-Log "Master already running (count=$($masterProcs.Count)); no new instance started."
    exit 0
}

$args = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $masterScript,
    "-DriveRoot",
    $DriveRoot,
    "-CycleMinutes",
    ([string]$CycleMinutes)
)
if ($IncludeDisabled) {
    $args += "-IncludeDisabled"
}
if (-not [string]::IsNullOrWhiteSpace($Project)) {
    $args += @("-Project", $Project)
}

$argString = (($args | ForEach-Object { Quote-Arg ([string]$_) }) -join " ")
$started = Start-Process -FilePath "powershell.exe" -ArgumentList $argString -WorkingDirectory $repoRoot -PassThru
Write-Log "Master started. pid=$($started.Id)"
