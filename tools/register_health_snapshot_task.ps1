param(
    [string]$TaskName = "JARVIS-HealthSnapshot",
    [int]$IntervalMinutes = 30,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptPath = Join-Path $repoRoot "tools\run_health_snapshot_alert.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$interval = [math]::Max(1, [int]$IntervalMinutes)
$userId = "$env:USERDOMAIN\$env:USERNAME"
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -LoopIntervalMinutes $interval"

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

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "JARVIS health snapshot + alert loop every $interval minute(s)." | Out-Null

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
}

$task = Get-ScheduledTask -TaskName $TaskName
[PSCustomObject]@{
    task_name = $TaskName
    interval_minutes = $interval
    user = $userId
    state = [string]$task.State
    command = "powershell.exe $actionArgs"
} | ConvertTo-Json -Depth 3
