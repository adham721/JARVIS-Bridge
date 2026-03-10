param(
    [string]$TaskName = "JARVIS-Autopilot-Master",
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [string]$ProjectsDir = "",
    [switch]$IncludeDisabled,
    [string]$Project = "",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scriptPath = Join-Path $repoRoot "tools\run_master_autopilot.ps1"
if (-not (Test-Path $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
$actionArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -DriveRoot `"$DriveRoot`" -CycleMinutes $CycleMinutes"
if (-not [string]::IsNullOrWhiteSpace($ProjectsDir)) {
    $actionArgs += " -ProjectsDir `"$ProjectsDir`""
}
if ($IncludeDisabled) {
    $actionArgs += " -IncludeDisabled"
}
if (-not [string]::IsNullOrWhiteSpace($Project)) {
    $actionArgs += " -Project `"$Project`""
}

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
    -Description "JARVIS master autopilot (sequential projects)." | Out-Null

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
}

$task = Get-ScheduledTask -TaskName $TaskName
[PSCustomObject]@{
    task_name = $TaskName
    user = $userId
    state = [string]$task.State
    trigger = "AtLogOn"
    cycle_minutes = $CycleMinutes
    command = "powershell.exe $actionArgs"
} | ConvertTo-Json -Depth 3

