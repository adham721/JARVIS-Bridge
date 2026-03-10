param(
    [string]$ProjectId = "kids_pod",
    [string]$DriveRoot = "G:\My Drive",
    [int]$CycleMinutes = 30,
    [int]$MaxCycles = 0,
    [int]$RetryCount = 3,
    [int]$RetryBackoffSeconds = 20,
    [int]$JarvisRunnerTimeoutMinutes = 45,
    [int]$IntelImportTimeoutMinutes = 50,
    [switch]$EnableRawImport = $true,
    [switch]$EnableResultImport = $true,
    [switch]$EnableCycleDeltaReport = $true,
    [switch]$EnableEtsyFallbackHardening = $true,
    [string]$EtsyFallbackUrlsFile = "",
    [int]$EtsyFallbackMaxResults = 30,
    [double]$EtsyFallbackMinIntervalHours = 6,
    [switch]$EnableAmazonFallbackHardening = $true,
    [string]$AmazonFallbackUrlsFile = "",
    [int]$AmazonFallbackMaxResults = 30,
    [double]$AmazonFallbackMinIntervalHours = 6,
    [switch]$EnableYouTubeFallbackHardening = $true,
    [string]$YouTubeFallbackUrlsFile = "",
    [int]$YouTubeFallbackMaxResults = 30,
    [double]$YouTubeFallbackMinIntervalHours = 6,
    [switch]$EnableInstagramFallbackHardening = $true,
    [string]$InstagramFallbackUrlsFile = "",
    [int]$InstagramFallbackMaxResults = 30,
    [double]$InstagramFallbackMinIntervalHours = 6,
    [switch]$EnableTikTokFallbackHardening = $true,
    [string]$TikTokFallbackUrlsFile = "",
    [switch]$EnableFacebookFallbackHardening = $true,
    [string]$FacebookFallbackUrlsFile = "",
    [int]$SocialFallbackMaxResults = 20,
    [double]$SocialFallbackMinIntervalHours = 6,
    [double]$SocialFallbackBlockThreshold = 0.25
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$message, [string]$level = "INFO") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts][$level][$ProjectId] $message"
    Write-Host $line
    Add-Content -Path $script:LogPath -Value $line -Encoding utf8
}

function Get-FileHashOrEmpty([string]$path) {
    if (-not (Test-Path $path)) {
        return ""
    }
    try {
        return (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLowerInvariant()
    } catch {
        return ""
    }
}

function Save-State([hashtable]$state) {
    $state | ConvertTo-Json | Set-Content -Path $script:StatePath -Encoding utf8
}

function Invoke-WithRetry([scriptblock]$action, [string]$name) {
    for ($attempt = 1; $attempt -le $RetryCount; $attempt++) {
        try {
            & $action
            return $true
        } catch {
            $msg = $_.Exception.Message
            if ($attempt -ge $RetryCount) {
                Write-Log "$name failed after $attempt/$RetryCount attempts: $msg" "ERROR"
                return $false
            }
            Write-Log "${name} failed at attempt ${attempt}/${RetryCount}: $msg; retrying in ${RetryBackoffSeconds}s" "WARN"
            Start-Sleep -Seconds $RetryBackoffSeconds
        }
    }
    return $false
}

function Get-ProjectSeedQueries(
    [string]$pythonExe,
    [string]$projectProfilePath,
    [string]$projectId
) {
    $fallback = @(([string]$projectId).Replace("_", " ").Trim())
    if ([string]::IsNullOrWhiteSpace($pythonExe)) {
        return $fallback
    }

    $py = @'
import json
import pathlib
import sys

try:
    import tomllib
except Exception:
    tomllib = None

profile = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("")
project_id = str(sys.argv[2]) if len(sys.argv) > 2 else ""

queries = []

def add_many(values):
    if not isinstance(values, list):
        return
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text:
            queries.append(text)

if tomllib and profile.exists():
    data = tomllib.loads(profile.read_text(encoding="utf-8"))
    add_many((data.get("youtube") or {}).get("queries") or [])
    add_many((data.get("signals") or {}).get("queries") or [])
    add_many((data.get("trends") or {}).get("keywords") or [])
    niche = str((data.get("brand") or {}).get("niche") or "").strip()
    if niche:
        queries.append(niche)

if not queries:
    queries = [project_id.replace("_", " ")]

seen = set()
out = []
for query in queries:
    key = query.casefold()
    if key in seen:
        continue
    seen.add(key)
    out.append(query)

print(json.dumps(out[:12], ensure_ascii=False))
'@

    try {
        $raw = $py | & $pythonExe - $projectProfilePath ([string]$projectId)
        if ($LASTEXITCODE -ne 0) {
            return $fallback
        }
        $jsonText = (($raw | ForEach-Object { [string]$_ }) -join "`n").Trim()
        if ([string]::IsNullOrWhiteSpace($jsonText)) {
            return $fallback
        }
        $decoded = $jsonText | ConvertFrom-Json
        if ($decoded -is [System.Array]) {
            $arr = @($decoded | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
            if ($arr.Count -gt 0) {
                return $arr
            }
            return $fallback
        }
        if ($null -ne $decoded -and -not [string]::IsNullOrWhiteSpace([string]$decoded)) {
            return @([string]$decoded)
        }
    } catch {
        return $fallback
    }
    return $fallback
}

function Build-AutoFallbackUrlsFile(
    [string]$repoRoot,
    [string]$projectId,
    [string]$platform,
    [string[]]$seedQueries
) {
    $normalized = @()
    foreach ($query in @($seedQueries)) {
        $q = [string]$query
        if ([string]::IsNullOrWhiteSpace($q)) {
            continue
        }
        $q = ($q -replace "\s+", " ").Trim()
        if (-not [string]::IsNullOrWhiteSpace($q)) {
            $normalized += $q
        }
    }
    if ($normalized.Count -eq 0) {
        $fallbackSeed = ([string]$projectId).Replace("_", " ").Trim()
        if (-not [string]::IsNullOrWhiteSpace($fallbackSeed)) {
            $normalized = @($fallbackSeed)
        }
    }

    $urls = @()
    foreach ($query in $normalized) {
        $encoded = [System.Uri]::EscapeDataString([string]$query)
        $url = ""
        switch ([string]$platform) {
            "etsy" { $url = "https://www.etsy.com/search?q=$encoded" }
            "amazon" { $url = "https://www.amazon.com/s?k=$encoded" }
            "youtube" { $url = "https://www.youtube.com/results?search_query=$encoded" }
            "instagram" { $url = "https://www.instagram.com/explore/search/keyword/?q=$encoded" }
            "tiktok" { $url = "https://www.tiktok.com/search?q=$encoded" }
            "facebook" { $url = "https://www.facebook.com/search/top/?q=$encoded" }
            default { $url = "" }
        }
        if (-not [string]::IsNullOrWhiteSpace($url)) {
            $urls += $url
        }
        if ($urls.Count -ge 12) {
            break
        }
    }
    if ($urls.Count -eq 0) {
        return ""
    }

    $dedup = New-Object System.Collections.Generic.HashSet[string]
    $ordered = @()
    foreach ($url in $urls) {
        if ($dedup.Add([string]$url)) {
            $ordered += [string]$url
        }
    }
    if ($ordered.Count -eq 0) {
        return ""
    }

    try {
        $runtimeSeedDir = Join-Path $repoRoot ("data\runtime\fallback_urls\{0}" -f $projectId)
        New-Item -ItemType Directory -Path $runtimeSeedDir -Force | Out-Null
        $outPath = Join-Path $runtimeSeedDir ("{0}_fallback_urls.auto.txt" -f $platform)
        $lines = @(
            "# auto-generated fallback URLs"
            ("# project={0}" -f $projectId)
            ("# platform={0}" -f $platform)
            "# source=project profile queries"
            ("# generated_at={0}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"))
            ""
        ) + $ordered
        Set-Content -Path $outPath -Value $lines -Encoding utf8
        return (Resolve-Path $outPath).Path
    } catch {
        return ""
    }
}

function Resolve-FallbackUrlsFile(
    [string]$repoRoot,
    [string]$projectId,
    [string]$platform,
    [string]$overridePath,
    [string[]]$seedQueries
) {
    if (-not [string]::IsNullOrWhiteSpace($overridePath)) {
        $candidate = $overridePath
        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            $candidate = Join-Path $repoRoot $candidate
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
        return ""
    }

    $projectSpecific = Join-Path $repoRoot ("tools\{0}_fallback_urls.{1}.txt" -f $platform, $projectId)
    $manualProjectSpecific = Join-Path $repoRoot ("data\manual\{0}_urls_{1}_seed.txt" -f $platform, $projectId)
    $globalDefault = Join-Path $repoRoot ("tools\{0}_fallback_urls.txt" -f $platform)
    $manualGlobal = Join-Path $repoRoot ("data\manual\{0}_urls_seed.txt" -f $platform)
    if (Test-Path $projectSpecific) {
        return (Resolve-Path $projectSpecific).Path
    }
    if (Test-Path $manualProjectSpecific) {
        return (Resolve-Path $manualProjectSpecific).Path
    }
    $autoGenerated = Build-AutoFallbackUrlsFile -repoRoot $repoRoot -projectId $projectId -platform $platform -seedQueries $seedQueries
    if (-not [string]::IsNullOrWhiteSpace($autoGenerated)) {
        return [string]$autoGenerated
    }
    if (Test-Path $globalDefault) {
        return (Resolve-Path $globalDefault).Path
    }
    if (Test-Path $manualGlobal) {
        return (Resolve-Path $manualGlobal).Path
    }
    return ""
}

function Get-LatestPlatformMeta([string]$repoRoot, [string]$projectId, [string]$platform) {
    $reportsRoot = Join-Path $repoRoot ("data\reports\{0}" -f $projectId)
    if (-not (Test-Path $reportsRoot)) {
        return $null
    }
    $latest = Get-ChildItem -Path $reportsRoot -Filter "platform_report.json" -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latest) {
        return $null
    }
    try {
        $payload = Get-Content -Raw -Encoding utf8 $latest.FullName | ConvertFrom-Json
    } catch {
        return $null
    }
    if (-not $payload -or -not $payload.platforms) {
        return $null
    }
    $meta = $payload.platforms.$platform
    return $meta
}

function Get-PlatformBlockRate([object]$meta) {
    if (-not $meta) { return -1.0 }
    $candidate = $meta.effective_block_rate
    if ($null -eq $candidate -or [string]::IsNullOrWhiteSpace([string]$candidate)) {
        $candidate = $meta.block_rate
    }
    try {
        return [double]$candidate
    } catch {
        return -1.0
    }
}

function Get-PlatformEffectiveGate([object]$meta) {
    if (-not $meta) { return "" }
    $candidate = $meta.effective_quality_gate
    if ($null -eq $candidate -or [string]::IsNullOrWhiteSpace([string]$candidate)) {
        $candidate = $meta.quality_gate
    }
    return [string]$candidate
}

function Get-ActiveProjectRunnerCount([string]$projectId) {
    $pattern = "--project\\s+" + [regex]::Escape([string]$projectId) + "(\\s|$)"
    try {
        $rows = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -match "^python(\\.exe)?$" -and
            $_.CommandLine -match "jarvis_runner\\.py" -and
            $_.CommandLine -match $pattern
        }
        return @($rows).Count
    } catch {
        return 0
    }
}

function Invoke-PowershellFileWithTimeout(
    [string]$repoRoot,
    [string]$relativeScriptPath,
    [string[]]$arguments,
    [int]$timeoutMinutes,
    [string]$operationName
) {
    $timeout = [math]::Max(1, [int]$timeoutMinutes)
    $safeName = ([string]$operationName -replace "[^a-zA-Z0-9_-]", "_")
    $stdoutPath = Join-Path $script:RuntimeDir ("{0}.{1}.stdout.log" -f $ProjectId, $safeName)
    $stderrPath = Join-Path $script:RuntimeDir ("{0}.{1}.stderr.log" -f $ProjectId, $safeName)
    Remove-Item -Force $stdoutPath -ErrorAction SilentlyContinue
    Remove-Item -Force $stderrPath -ErrorAction SilentlyContinue

    $scriptPath = Join-Path $repoRoot $relativeScriptPath
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scriptPath) + @($arguments)

    $proc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $argList `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath

    $finished = $proc.WaitForExit($timeout * 60 * 1000)
    if (-not $finished) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
        $tailOut = ""
        $tailErr = ""
        if (Test-Path $stdoutPath) { $tailOut = (Get-Content -Tail 8 $stdoutPath -ErrorAction SilentlyContinue) -join " | " }
        if (Test-Path $stderrPath) { $tailErr = (Get-Content -Tail 8 $stderrPath -ErrorAction SilentlyContinue) -join " | " }
        if (-not [string]::IsNullOrWhiteSpace($tailOut)) { Write-Log "$operationName timeout stdout tail: $tailOut" "WARN" }
        if (-not [string]::IsNullOrWhiteSpace($tailErr)) { Write-Log "$operationName timeout stderr tail: $tailErr" "WARN" }
        throw "$operationName timeout after ${timeout}m"
    }

    $exitCode = [int]$proc.ExitCode
    if ($exitCode -ne 0) {
        $tailOut = ""
        $tailErr = ""
        if (Test-Path $stdoutPath) { $tailOut = (Get-Content -Tail 8 $stdoutPath -ErrorAction SilentlyContinue) -join " | " }
        if (Test-Path $stderrPath) { $tailErr = (Get-Content -Tail 8 $stderrPath -ErrorAction SilentlyContinue) -join " | " }
        if (-not [string]::IsNullOrWhiteSpace($tailOut)) { Write-Log "$operationName stdout tail: $tailOut" "WARN" }
        if (-not [string]::IsNullOrWhiteSpace($tailErr)) { Write-Log "$operationName stderr tail: $tailErr" "WARN" }
        throw "$operationName exited with code $exitCode"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$runtimeDir = Join-Path $repoRoot "data\runtime"
$logDir = Join-Path $repoRoot "logs"
$cleanupScript = Join-Path $repoRoot "tools\cleanup_stale_runs.py"
$deltaScript = Join-Path $repoRoot "tools\cycle_delta_report.py"
$opsDbPath = Join-Path $repoRoot "data\jarvis_ops.db"
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$script:RuntimeDir = $runtimeDir

$projectProfilePath = Join-Path $repoRoot ("projects\{0}.toml" -f $ProjectId)
$projectSeedQueries = Get-ProjectSeedQueries -pythonExe $python -projectProfilePath $projectProfilePath -projectId $ProjectId

$script:StatePath = Join-Path $runtimeDir "$ProjectId.autopilot.state.json"
$script:LogPath = Join-Path $logDir "$ProjectId.autopilot.log"

$inboxDir = Join-Path $DriveRoot "JARVIS_INTEL_INBOX\$ProjectId"
$rawPath = Join-Path $inboxDir "raw_intel.txt"
$resultPath = Join-Path $inboxDir "intel_result.json"

New-Item -ItemType Directory -Path $inboxDir -Force | Out-Null

$state = @{
    last_raw_hash = ""
    last_result_hash = ""
    last_run_at = ""
    last_success_cycle = 0
    last_etsy_fallback_at = ""
    last_platform_fallback_at = @{}
}

if (Test-Path $script:StatePath) {
    try {
        $loaded = Get-Content -Raw -Encoding utf8 $script:StatePath | ConvertFrom-Json
        if ($loaded.last_raw_hash) { $state.last_raw_hash = [string]$loaded.last_raw_hash }
        if ($loaded.last_result_hash) { $state.last_result_hash = [string]$loaded.last_result_hash }
        if ($loaded.last_run_at) { $state.last_run_at = [string]$loaded.last_run_at }
        if ($loaded.last_success_cycle) { $state.last_success_cycle = [int]$loaded.last_success_cycle }
        if ($loaded.last_etsy_fallback_at) { $state.last_etsy_fallback_at = [string]$loaded.last_etsy_fallback_at }
        if ($loaded.last_platform_fallback_at) {
            $converted = @{}
            foreach ($p in $loaded.last_platform_fallback_at.PSObject.Properties) {
                $converted[[string]$p.Name] = [string]$p.Value
            }
            $state.last_platform_fallback_at = $converted
        }
    } catch {
        Write-Log "State file parse failed; starting with fresh state." "WARN"
    }
}
if (-not ($state.last_platform_fallback_at -is [hashtable])) {
    $state.last_platform_fallback_at = @{}
}
if (-not [string]::IsNullOrWhiteSpace($state.last_etsy_fallback_at) -and -not $state.last_platform_fallback_at.ContainsKey("etsy")) {
    $state.last_platform_fallback_at["etsy"] = [string]$state.last_etsy_fallback_at
}

$etsyFallbackResolved = Resolve-FallbackUrlsFile -repoRoot $repoRoot -projectId $ProjectId -platform "etsy" -overridePath $EtsyFallbackUrlsFile -seedQueries $projectSeedQueries
$amazonFallbackResolved = Resolve-FallbackUrlsFile -repoRoot $repoRoot -projectId $ProjectId -platform "amazon" -overridePath $AmazonFallbackUrlsFile -seedQueries $projectSeedQueries
$youtubeFallbackResolved = Resolve-FallbackUrlsFile -repoRoot $repoRoot -projectId $ProjectId -platform "youtube" -overridePath $YouTubeFallbackUrlsFile -seedQueries $projectSeedQueries
$instagramFallbackResolved = Resolve-FallbackUrlsFile -repoRoot $repoRoot -projectId $ProjectId -platform "instagram" -overridePath $InstagramFallbackUrlsFile -seedQueries $projectSeedQueries
$tiktokFallbackResolved = Resolve-FallbackUrlsFile -repoRoot $repoRoot -projectId $ProjectId -platform "tiktok" -overridePath $TikTokFallbackUrlsFile -seedQueries $projectSeedQueries
$facebookFallbackResolved = Resolve-FallbackUrlsFile -repoRoot $repoRoot -projectId $ProjectId -platform "facebook" -overridePath $FacebookFallbackUrlsFile -seedQueries $projectSeedQueries

Write-Log "Autopilot started. Cycle=${CycleMinutes}m, MaxCycles=$MaxCycles, RetryCount=$RetryCount"
Write-Log "Runner timeout: ${JarvisRunnerTimeoutMinutes}m"
Write-Log "Intel import timeout: ${IntelImportTimeoutMinutes}m"
Write-Log "State file: $script:StatePath"
Write-Log "Log file: $script:LogPath"
Write-Log "Raw path: $rawPath"
Write-Log "Result path: $resultPath"
if ($EnableCycleDeltaReport) {
    Write-Log "Cycle delta report: enabled"
} else {
    Write-Log "Cycle delta report: disabled"
}
if ($EnableEtsyFallbackHardening) {
    if ($etsyFallbackResolved) {
        Write-Log "Etsy fallback hardening: enabled (file=$etsyFallbackResolved, interval=${EtsyFallbackMinIntervalHours}h, max_results=$EtsyFallbackMaxResults)"
    } else {
        Write-Log "Etsy fallback hardening: enabled but URL file not found (set -EtsyFallbackUrlsFile to override)." "WARN"
    }
} else {
    Write-Log "Etsy fallback hardening: disabled"
}
if ($EnableAmazonFallbackHardening) {
    if ($amazonFallbackResolved) {
        Write-Log "Amazon fallback hardening: enabled (file=$amazonFallbackResolved, interval=${AmazonFallbackMinIntervalHours}h, max_results=$AmazonFallbackMaxResults, trigger=quality_fail)"
    } else {
        Write-Log "Amazon fallback hardening: enabled but URL file not found (set -AmazonFallbackUrlsFile to override)." "WARN"
    }
} else {
    Write-Log "Amazon fallback hardening: disabled"
}
if ($EnableYouTubeFallbackHardening) {
    if ($youtubeFallbackResolved) {
        Write-Log "YouTube fallback hardening: enabled (file=$youtubeFallbackResolved, interval=${YouTubeFallbackMinIntervalHours}h, max_results=$YouTubeFallbackMaxResults, trigger=quality_fail)"
    } else {
        Write-Log "YouTube fallback hardening: enabled but URL file not found (set -YouTubeFallbackUrlsFile to override)." "WARN"
    }
} else {
    Write-Log "YouTube fallback hardening: disabled"
}
if ($EnableInstagramFallbackHardening) {
    if ($instagramFallbackResolved) {
        Write-Log "Instagram fallback hardening: enabled (file=$instagramFallbackResolved, interval=${InstagramFallbackMinIntervalHours}h, max_results=$InstagramFallbackMaxResults, trigger=quality_fail)"
    } else {
        Write-Log "Instagram fallback hardening: enabled but URL file not found (set -InstagramFallbackUrlsFile to override)." "WARN"
    }
} else {
    Write-Log "Instagram fallback hardening: disabled"
}
if ($EnableTikTokFallbackHardening) {
    if ($tiktokFallbackResolved) {
        Write-Log "TikTok fallback hardening: enabled (file=$tiktokFallbackResolved, block_threshold=$SocialFallbackBlockThreshold, interval=${SocialFallbackMinIntervalHours}h, max_results=$SocialFallbackMaxResults)"
    } else {
        Write-Log "TikTok fallback hardening: enabled but URL file not found (set -TikTokFallbackUrlsFile to override)." "WARN"
    }
} else {
    Write-Log "TikTok fallback hardening: disabled"
}
if ($EnableFacebookFallbackHardening) {
    if ($facebookFallbackResolved) {
        Write-Log "Facebook fallback hardening: enabled (file=$facebookFallbackResolved, block_threshold=$SocialFallbackBlockThreshold, interval=${SocialFallbackMinIntervalHours}h, max_results=$SocialFallbackMaxResults)"
    } else {
        Write-Log "Facebook fallback hardening: enabled but URL file not found (set -FacebookFallbackUrlsFile to override)." "WARN"
    }
} else {
    Write-Log "Facebook fallback hardening: disabled"
}

$cycle = 0
while ($true) {
    if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
        break
    }
    $cycle += 1
    Write-Log "Cycle $cycle started."

    $fallbackJobs = @(
        [PSCustomObject]@{
            platform = "etsy"
            enabled = [bool]$EnableEtsyFallbackHardening
            file = [string]$etsyFallbackResolved
            mode = "always"
            max_results = [int]$EtsyFallbackMaxResults
            min_interval_hours = [double]$EtsyFallbackMinIntervalHours
            threshold = [double]$SocialFallbackBlockThreshold
        },
        [PSCustomObject]@{
            platform = "amazon"
            enabled = [bool]$EnableAmazonFallbackHardening
            file = [string]$amazonFallbackResolved
            mode = "on_quality_fail"
            max_results = [int]$AmazonFallbackMaxResults
            min_interval_hours = [double]$AmazonFallbackMinIntervalHours
            threshold = [double]$SocialFallbackBlockThreshold
        },
        [PSCustomObject]@{
            platform = "youtube"
            enabled = [bool]$EnableYouTubeFallbackHardening
            file = [string]$youtubeFallbackResolved
            mode = "on_quality_fail"
            max_results = [int]$YouTubeFallbackMaxResults
            min_interval_hours = [double]$YouTubeFallbackMinIntervalHours
            threshold = [double]$SocialFallbackBlockThreshold
        },
        [PSCustomObject]@{
            platform = "instagram"
            enabled = [bool]$EnableInstagramFallbackHardening
            file = [string]$instagramFallbackResolved
            mode = "on_quality_fail"
            max_results = [int]$InstagramFallbackMaxResults
            min_interval_hours = [double]$InstagramFallbackMinIntervalHours
            threshold = [double]$SocialFallbackBlockThreshold
        },
        [PSCustomObject]@{
            platform = "tiktok"
            enabled = [bool]$EnableTikTokFallbackHardening
            file = [string]$tiktokFallbackResolved
            mode = "on_block_rate"
            max_results = [int]$SocialFallbackMaxResults
            min_interval_hours = [double]$SocialFallbackMinIntervalHours
            threshold = [double]$SocialFallbackBlockThreshold
        },
        [PSCustomObject]@{
            platform = "facebook"
            enabled = [bool]$EnableFacebookFallbackHardening
            file = [string]$facebookFallbackResolved
            mode = "on_block_rate"
            max_results = [int]$SocialFallbackMaxResults
            min_interval_hours = [double]$SocialFallbackMinIntervalHours
            threshold = [double]$SocialFallbackBlockThreshold
        }
    )

    foreach ($job in $fallbackJobs) {
        $platform = [string]$job.platform
        if (-not $job.enabled) { continue }
        if ([string]::IsNullOrWhiteSpace($job.file)) { continue }

        $shouldRunFallback = $true
        if ($job.mode -eq "on_block_rate") {
            $meta = Get-LatestPlatformMeta -repoRoot $repoRoot -projectId $ProjectId -platform $platform
            if (-not $meta) {
                $shouldRunFallback = $false
                Write-Log "Skipping $platform fallback hardening (no previous platform_report yet for block-rate trigger)."
            } else {
                $platformBlockRate = Get-PlatformBlockRate -meta $meta
                $threshold = [double]$job.threshold
                if ($platformBlockRate -ge $threshold) {
                    Write-Log ("$platform fallback hardening triggered (block_rate={0:N3} >= threshold={1:N3})." -f $platformBlockRate, $threshold)
                } else {
                    $shouldRunFallback = $false
                    Write-Log ("Skipping $platform fallback hardening (block_rate={0:N3} < threshold={1:N3})." -f $platformBlockRate, $threshold)
                }
            }
        } elseif ($job.mode -eq "on_quality_fail") {
            $meta = Get-LatestPlatformMeta -repoRoot $repoRoot -projectId $ProjectId -platform $platform
            if (-not $meta) {
                $shouldRunFallback = $false
                Write-Log "Skipping $platform fallback hardening (no previous platform_report yet for quality trigger)."
            } else {
                $effectiveGate = (Get-PlatformEffectiveGate -meta $meta).Trim().ToLowerInvariant()
                if ($effectiveGate -eq "pass") {
                    $shouldRunFallback = $false
                    Write-Log "Skipping $platform fallback hardening (effective_quality_gate=pass)."
                } else {
                    Write-Log "$platform fallback hardening triggered (effective_quality_gate=$effectiveGate)."
                }
            }
        }

        $lastFallbackToken = ""
        if ($state.last_platform_fallback_at.ContainsKey($platform)) {
            $lastFallbackToken = [string]$state.last_platform_fallback_at[$platform]
        } elseif ($platform -eq "etsy" -and -not [string]::IsNullOrWhiteSpace($state.last_etsy_fallback_at)) {
            $lastFallbackToken = [string]$state.last_etsy_fallback_at
        }

        if ($shouldRunFallback -and -not [string]::IsNullOrWhiteSpace($lastFallbackToken)) {
            try {
                $lastFallbackAt = [datetime]::Parse($lastFallbackToken)
                $elapsedHours = ((Get-Date) - $lastFallbackAt).TotalHours
                $minInterval = [math]::Max(0.5, [double]$job.min_interval_hours)
                if ($elapsedHours -lt $minInterval) {
                    $shouldRunFallback = $false
                    Write-Log ("Skipping $platform fallback hardening (elapsed={0:N2}h < min={1:N2}h)." -f $elapsedHours, $minInterval)
                }
            } catch {
                $shouldRunFallback = $true
            }
        }

        if ($shouldRunFallback) {
            $jobName = ("{0}_fallback_hardening" -f $platform)
            $fallbackOk = Invoke-WithRetry -name $jobName -action {
                Push-Location $repoRoot
                try {
                    & $python "tools\platform_hardening_capture.py" "--project" $ProjectId "--platform" $platform "--from-urls-file" ([string]$job.file) "--max-results" ([string]$job.max_results)
                    if ($LASTEXITCODE -ne 0) {
                        throw "platform_hardening_capture exited with code $LASTEXITCODE"
                    }
                } finally {
                    Pop-Location
                }
            }
            if ($fallbackOk) {
                $stamp = (Get-Date).ToString("s")
                $state.last_platform_fallback_at[$platform] = $stamp
                if ($platform -eq "etsy") {
                    $state.last_etsy_fallback_at = $stamp
                }
            }
        }
    }

    $runnerOk = Invoke-WithRetry -name "jarvis_runner" -action {
        Push-Location $repoRoot
        try {
            $timeoutMinutes = [math]::Max(5, [int]$JarvisRunnerTimeoutMinutes)
            $stdoutPath = Join-Path $runtimeDir ("{0}.runner.stdout.log" -f $ProjectId)
            $stderrPath = Join-Path $runtimeDir ("{0}.runner.stderr.log" -f $ProjectId)
            Remove-Item -Force $stdoutPath -ErrorAction SilentlyContinue
            Remove-Item -Force $stderrPath -ErrorAction SilentlyContinue

            $proc = Start-Process `
                -FilePath $python `
                -ArgumentList @("jarvis_runner.py", "--project", $ProjectId, "--dedup-hours", "0") `
                -PassThru `
                -NoNewWindow `
                -RedirectStandardOutput $stdoutPath `
                -RedirectStandardError $stderrPath

            $finished = $proc.WaitForExit($timeoutMinutes * 60 * 1000)
            if (-not $finished) {
                try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
                $tailOut = ""
                $tailErr = ""
                if (Test-Path $stdoutPath) { $tailOut = (Get-Content -Tail 8 $stdoutPath -ErrorAction SilentlyContinue) -join " | " }
                if (Test-Path $stderrPath) { $tailErr = (Get-Content -Tail 8 $stderrPath -ErrorAction SilentlyContinue) -join " | " }
                if (-not [string]::IsNullOrWhiteSpace($tailOut)) { Write-Log "jarvis_runner timeout stdout tail: $tailOut" "WARN" }
                if (-not [string]::IsNullOrWhiteSpace($tailErr)) { Write-Log "jarvis_runner timeout stderr tail: $tailErr" "WARN" }
                throw "jarvis_runner timeout after ${timeoutMinutes}m"
            }

            $runnerExit = [int]$proc.ExitCode
            if ($runnerExit -ne 0) {
                $tailOut = ""
                $tailErr = ""
                if (Test-Path $stdoutPath) { $tailOut = (Get-Content -Tail 8 $stdoutPath -ErrorAction SilentlyContinue) -join " | " }
                if (Test-Path $stderrPath) { $tailErr = (Get-Content -Tail 8 $stderrPath -ErrorAction SilentlyContinue) -join " | " }
                if (-not [string]::IsNullOrWhiteSpace($tailOut)) { Write-Log "jarvis_runner stdout tail: $tailOut" "WARN" }
                if (-not [string]::IsNullOrWhiteSpace($tailErr)) { Write-Log "jarvis_runner stderr tail: $tailErr" "WARN" }
                throw "jarvis_runner exited with code $runnerExit"
            }
        } finally {
            Pop-Location
        }
    }

    if (-not $runnerOk) {
        $state.last_run_at = (Get-Date).ToString("s")
        Save-State $state
        if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) { break }
        Start-Sleep -Seconds ($CycleMinutes * 60)
        continue
    }

    # Post-run sanity cleanup:
    # If jarvis_runner exited but a stale `runs.status='running'` row remains for this project,
    # close it quickly instead of waiting for long global cleanup windows.
    if ((Test-Path $cleanupScript) -and (Test-Path $opsDbPath)) {
        $activeRunnerCount = Get-ActiveProjectRunnerCount -projectId $ProjectId
        if ($activeRunnerCount -gt 0) {
            Write-Log "Post-run orphan cleanup skipped ($activeRunnerCount active jarvis_runner process(es) still detected)." "WARN"
        } else {
            try {
                $cleanupJson = & $python $cleanupScript `
                    "--db-path" $opsDbPath `
                    "--project" $ProjectId `
                    "--older-than-minutes" "1" `
                    "--tag" ("project_post_runner_sanity_" + $ProjectId) `
                    "--reason" "Marked failed by post-run sanity cleanup"
                if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($cleanupJson)) {
                    $cleanupPayload = $cleanupJson | ConvertFrom-Json
                    $matched = [int]($cleanupPayload.matched_count)
                    $updated = [int]($cleanupPayload.updated_count)
                    if ($matched -gt 0 -or $updated -gt 0) {
                        Write-Log "Post-run stale cleanup matched=$matched updated=$updated cutoff=1m."
                    }
                }
            } catch {
                Write-Log ("Post-run stale cleanup failed: " + $_.Exception.Message) "WARN"
            }
        }
    }

    if ($EnableCycleDeltaReport -and (Test-Path $deltaScript) -and (Test-Path $opsDbPath)) {
        try {
            $deltaRaw = & $python $deltaScript "--db-path" $opsDbPath "--project" $ProjectId
            if ($LASTEXITCODE -eq 0) {
                $deltaText = (($deltaRaw | ForEach-Object { [string]$_ }) -join "`n").Trim()
                if (-not [string]::IsNullOrWhiteSpace($deltaText)) {
                    $deltaPayload = $deltaText | ConvertFrom-Json
                    $beforeRunToken = "n/a"
                    $afterRunToken = "n/a"
                    if ($null -ne $deltaPayload.before_run_id) { $beforeRunToken = [string]$deltaPayload.before_run_id }
                    if ($null -ne $deltaPayload.after_run_id) { $afterRunToken = [string]$deltaPayload.after_run_id }

                    $coverageBefore = "n/a"
                    $coverageAfter = "n/a"
                    if ($deltaPayload.coverage_block_rate -and $null -ne $deltaPayload.coverage_block_rate.before) {
                        try { $coverageBefore = ("{0:N4}" -f [double]$deltaPayload.coverage_block_rate.before) } catch {}
                    }
                    if ($deltaPayload.coverage_block_rate -and $null -ne $deltaPayload.coverage_block_rate.after) {
                        try { $coverageAfter = ("{0:N4}" -f [double]$deltaPayload.coverage_block_rate.after) } catch {}
                    }

                    $changedPlatforms = @()
                    if ($deltaPayload.platform_changes) {
                        $changedPlatforms = @($deltaPayload.platform_changes.PSObject.Properties | ForEach-Object { [string]$_.Name })
                    }
                    $changedCount = @($changedPlatforms).Count
                    $deltaPath = [string]($deltaPayload.output_latest_path)
                    Write-Log ("Cycle delta report generated (after_run={0}, before_run={1}, changed_platforms={2}, coverage_block_rate={3}->{4}, file={5})." -f $afterRunToken, $beforeRunToken, $changedCount, $coverageBefore, $coverageAfter, $deltaPath)
                }
            } else {
                Write-Log "Cycle delta report command returned non-zero exit code." "WARN"
            }
        } catch {
            Write-Log ("Cycle delta report failed: " + $_.Exception.Message) "WARN"
        }
    }

    if ($EnableRawImport) {
        $rawHash = Get-FileHashOrEmpty $rawPath
        if ($rawHash -and $rawHash -ne $state.last_raw_hash) {
            Write-Log "New raw intel detected: $rawHash"
            $importOk = Invoke-WithRetry -name "import_intel_packet" -action {
                Push-Location $repoRoot
                try {
                    Invoke-PowershellFileWithTimeout `
                        -repoRoot $repoRoot `
                        -relativeScriptPath "tools\import_intel_packet.ps1" `
                        -arguments @("-InputPath", $rawPath, "-ProjectId", $ProjectId, "-DriveRoot", $DriveRoot) `
                        -timeoutMinutes $IntelImportTimeoutMinutes `
                        -operationName "import_intel_packet"
                } finally {
                    Pop-Location
                }
            }
            if ($importOk) {
                $state.last_raw_hash = $rawHash
            }
        }
    }

    if ($EnableResultImport) {
        $resultHash = Get-FileHashOrEmpty $resultPath
        if ($resultHash -and $resultHash -ne $state.last_result_hash) {
            Write-Log "Updated intel_result detected: $resultHash"
            $verifyOk = Invoke-WithRetry -name "verify_intel_bridge" -action {
                Push-Location $repoRoot
                try {
                    Invoke-PowershellFileWithTimeout `
                        -repoRoot $repoRoot `
                        -relativeScriptPath "tools\verify_intel_bridge.ps1" `
                        -arguments @("-ProjectId", $ProjectId, "-DriveRoot", $DriveRoot, "-RunImport") `
                        -timeoutMinutes $IntelImportTimeoutMinutes `
                        -operationName "verify_intel_bridge"
                } finally {
                    Pop-Location
                }
            }
            if ($verifyOk) {
                $state.last_result_hash = $resultHash
            }
        }
    }

    $state.last_run_at = (Get-Date).ToString("s")
    $state.last_success_cycle = $cycle
    Save-State $state
    Write-Log "Cycle $cycle completed successfully."

    if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
        break
    }
    Start-Sleep -Seconds ($CycleMinutes * 60)
}

Write-Log "Autopilot stopped after $cycle cycle(s)."
