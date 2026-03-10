param(
    [string]$ProjectId = "kids_pod",
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [string]$TaskName = "",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = "JARVIS-Autopilot-$ProjectId"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runnerScript = Join-Path $repoRoot "tools\run_project_autopilot.ps1"
if (-not (Test-Path $runnerScript)) {
    throw "Autopilot script not found: $runnerScript"
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$runnerScript`" -ProjectId `"$ProjectId`" -DriveRoot `"$DriveRoot`" -CycleMinutes $CycleMinutes"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$description = "JARVIS autopilot for $ProjectId"
$registeredWith = "Register-ScheduledTask"
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description $description | Out-Null
} catch {
    $registeredWith = "schtasks"
    & schtasks /Delete /TN $TaskName /F | Out-Null
    $taskRun = "powershell.exe $actionArgs"
    & schtasks /Create /SC ONLOGON /TN $TaskName /TR $taskRun /RL LIMITED /F | Out-Null
}

if ($RunNow) {
    try {
        Start-ScheduledTask -TaskName $TaskName
    } catch {
        & schtasks /Run /TN $TaskName | Out-Null
    }
}

$stateValue = ""
try {
    $task = Get-ScheduledTask -TaskName $TaskName
    $stateValue = [string]$task.State
} catch {
    $stateValue = ""
}

[PSCustomObject]@{
    task_name = $TaskName
    user = $userId
    state = $stateValue
    trigger = "AtLogOn"
    cycle_minutes = $CycleMinutes
    command = "powershell.exe $actionArgs"
    registered_with = $registeredWith
} | ConvertTo-Json -Depth 3
