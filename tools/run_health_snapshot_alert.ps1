param(
    [string]$OutputPath = "",
    [int]$AlertCooldownMinutes = 60,
    [switch]$SendTelegramOnFail = $true,
    [int]$LoopIntervalMinutes = 0,
    [int]$MaxCycles = 0
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$message, [string]$level = "INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts][$level] $message"
    Write-Host $line
    Add-Content -Path $script:LogPath -Value $line -Encoding utf8
}

function Get-DotEnvValue([string]$dotenvPath, [string]$key) {
    if (-not (Test-Path $dotenvPath)) {
        return ""
    }
    $pattern = '^\s*' + [regex]::Escape($key) + '\s*=\s*(.*)$'
    foreach ($raw in Get-Content -Path $dotenvPath -Encoding utf8) {
        $line = [string]$raw
        if ($line.TrimStart().StartsWith("#")) {
            continue
        }
        $m = [regex]::Match($line, $pattern)
        if (-not $m.Success) {
            continue
        }
        $value = [string]$m.Groups[1].Value
        if ($value.StartsWith('"') -and $value.EndsWith('"') -and $value.Length -ge 2) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($value.StartsWith("'") -and $value.EndsWith("'") -and $value.Length -ge 2) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        return $value.Trim()
    }
    return ""
}

function Load-State([string]$statePath) {
    $state = @{
        last_alert_at = ""
        last_ok = $true
    }
    if (-not (Test-Path $statePath)) {
        return $state
    }
    try {
        $loaded = Get-Content -Raw -Encoding utf8 $statePath | ConvertFrom-Json
        if ($loaded.last_alert_at) { $state.last_alert_at = [string]$loaded.last_alert_at }
        if ($null -ne $loaded.last_ok) { $state.last_ok = [bool]$loaded.last_ok }
    } catch {
    }
    return $state
}

function Save-State([string]$statePath, [hashtable]$state) {
    $state | ConvertTo-Json | Set-Content -Path $statePath -Encoding utf8
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$snapshotScript = Join-Path $repoRoot "tools\daily_health_snapshot.py"
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$runtimeDir = Join-Path $repoRoot "data\runtime"
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$script:LogPath = Join-Path $logDir "health_snapshot.log"
$statePath = Join-Path $runtimeDir "health_snapshot_alert.state.json"
$state = Load-State -statePath $statePath

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "cache\daily_health_snapshot_latest.json"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $repoRoot $OutputPath
}

function Invoke-HealthSnapshotCycle {
    Write-Log "Running daily health snapshot..."
    $snapshotJsonText = ""
    try {
        $snapshotJsonText = & $python $snapshotScript "--output" $OutputPath
        if ($LASTEXITCODE -ne 0) {
            throw "daily_health_snapshot.py exited with code $LASTEXITCODE"
        }
    } catch {
        Write-Log ("Snapshot execution failed: " + $_.Exception.Message) "ERROR"
        return 1
    }

    $snapshot = $null
    try {
        $snapshot = (($snapshotJsonText | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json
    } catch {
        try {
            $snapshot = Get-Content -Raw -Encoding utf8 $OutputPath | ConvertFrom-Json
        } catch {
            Write-Log "Failed to parse snapshot payload from stdout and output file." "ERROR"
            return 2
        }
    }

    $ok = [bool]$snapshot.ok
    $failed = @($snapshot.failed_projects)
    $reauth = @($snapshot.projects_with_reauth)
    $offTopic = @($snapshot.projects_off_topic_top)
    Write-Log ("Snapshot ok={0} failed={1} reauth={2} off_topic={3}" -f $ok, $failed.Count, $reauth.Count, $offTopic.Count)

    $shouldAlert = $SendTelegramOnFail -and (-not $ok)
    if ($shouldAlert) {
        $canSend = $true
        if (-not [string]::IsNullOrWhiteSpace([string]$state.last_alert_at)) {
            try {
                $lastAlertAt = [datetime]::Parse([string]$state.last_alert_at)
                $elapsed = ((Get-Date) - $lastAlertAt).TotalMinutes
                if ($elapsed -lt [double]([math]::Max(1, $AlertCooldownMinutes))) {
                    $canSend = $false
                    Write-Log ("Alert cooldown active: elapsed={0:N1}m < cooldown={1}m" -f $elapsed, $AlertCooldownMinutes)
                }
            } catch {
            }
        }

        if ($canSend) {
            $dotenv = Join-Path $repoRoot ".env"
            $token = [string]$env:TELEGRAM_BOT_TOKEN
            $chatId = [string]$env:TELEGRAM_CHAT_ID
            if ([string]::IsNullOrWhiteSpace($token)) { $token = Get-DotEnvValue -dotenvPath $dotenv -key "TELEGRAM_BOT_TOKEN" }
            if ([string]::IsNullOrWhiteSpace($chatId)) { $chatId = Get-DotEnvValue -dotenvPath $dotenv -key "TELEGRAM_CHAT_ID" }

            if ([string]::IsNullOrWhiteSpace($token) -or [string]::IsNullOrWhiteSpace($chatId)) {
                Write-Log "Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing." "WARN"
            } else {
                $failedText = if ($failed.Count -gt 0) { ($failed -join ", ") } else { "-" }
                $reauthText = if ($reauth.Count -gt 0) { ($reauth -join ", ") } else { "-" }
                $offTopicText = if ($offTopic.Count -gt 0) { ($offTopic -join ", ") } else { "-" }
                $message = @(
                    "JARVIS Health Alert",
                    "ok=false",
                    "failed_projects: $failedText",
                    "projects_with_reauth: $reauthText",
                    "projects_off_topic_top: $offTopicText"
                ) -join "`n"

                try {
                    $uri = "https://api.telegram.org/bot$token/sendMessage"
                    Invoke-RestMethod -Method Post -Uri $uri -Body @{
                        chat_id = $chatId
                        text = $message
                        disable_web_page_preview = "true"
                    } | Out-Null
                    Write-Log "Telegram health alert sent."
                    $state.last_alert_at = (Get-Date).ToString("s")
                } catch {
                    Write-Log ("Telegram health alert failed: " + $_.Exception.Message) "ERROR"
                }
            }
        }
    }

    $state.last_ok = $ok
    Save-State -statePath $statePath -state $state
    Write-Log "Health snapshot job completed."
    return 0
}

$interval = [math]::Max(1, [int]$LoopIntervalMinutes)
$cycle = 0
while ($true) {
    if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
        break
    }
    $cycle += 1
    $code = Invoke-HealthSnapshotCycle
    if ($code -ne 0 -and $LoopIntervalMinutes -le 0) {
        exit $code
    }
    if ($LoopIntervalMinutes -le 0) {
        break
    }
    if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
        break
    }
    Start-Sleep -Seconds ($interval * 60)
}

Write-Log "Health snapshot worker stopped after $cycle cycle(s)."
exit 0
