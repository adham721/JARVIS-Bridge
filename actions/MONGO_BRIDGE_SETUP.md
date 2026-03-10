# Mongo Bridge Setup (Azure Functions + MongoDB)

This replaces the fragile Drive Action path with a queue-style bridge.

## 1) Enable Mongo bridge in `.env`

```ini
JARVIS_MONGO_BRIDGE_ENABLED=1
JARVIS_MONGO_URI=mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority
JARVIS_MONGO_DB=jarvis_intel
JARVIS_MONGO_JOBS_COLLECTION=intel_jobs
JARVIS_MONGO_INTEL_COLLECTION=intel_packets
JARVIS_MONGO_CONNECT_TIMEOUT_MS=6000
JARVIS_MONGO_RETRY_ATTEMPTS=3
JARVIS_MONGO_RETRY_BACKOFF_SECONDS=1.2
JARVIS_MONGO_INTEL_MAX_FILES=20
JARVIS_BRIDGE_API_KEY=change_me
```

Helper script:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\enable_mongo_bridge.ps1 -MongoUri "mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority"
```

## 2) What changes in runner

- `jarvis_runner.py` now does both:
  - writes `intel_input.md` to outbox (old behavior, still available),
  - enqueues the same intel request into Mongo `intel_jobs` (new behavior).
- Manual intel import now reads:
  - local inbox JSON files (old path),
  - Mongo `intel_packets` with status `queued|ready|new` and `imported!=true`.

## 3) Bridge API (Azure Functions)

Files:

- `bridge/azure_functions/function_app.py`
- `bridge/azure_functions/requirements.txt`
- `bridge/azure_functions/local.settings.example.json`
- `tools/sync_azure_local_settings.py` (generate `local.settings.json` from `.env`)

Routes:

- `GET /api/v1/health`
- `POST /api/v1/jobs/create`
- `GET /api/v1/jobs/next?project_id=...`
- `POST /api/v1/jobs/{job_id}/complete`
- `POST /api/v1/jobs/{job_id}/fail`

Auth:

- Header: `x-jarvis-key`
- Configure secret: `JARVIS_BRIDGE_API_KEY`

Generate local settings quickly:

```powershell
python tools/sync_azure_local_settings.py
```

### 3B) Bridge API on Render (recommended for Plan B)

Files:

- `bridge/render_api/main.py`
- `bridge/render_api/requirements.txt`
- `render.yaml`

Deploy:

1. Push repo to GitHub.
2. In Render: **New +** -> **Blueprint** -> select repo.
3. Render reads `render.yaml` and creates web service automatically.
4. Set required secret env vars in Render:
   - `JARVIS_MONGO_URI`
   - `JARVIS_BRIDGE_API_KEY`
5. Wait for deploy completion and copy URL:
   - `https://<your-service>.onrender.com`

Health check from PowerShell:

```powershell
$headers = @{ "x-jarvis-key" = "change_me_bridge_key" }
Invoke-RestMethod -Method GET -Uri "https://<your-service>.onrender.com/api/v1/health" -Headers $headers
```

Then update OpenAPI server URL:

```powershell
python tools/set_mongo_bridge_server_url.py --url "https://<your-service>.onrender.com" --update-env
```

## 4) Custom GPT Action (Bridge)

Import:

- `actions/mongo_intel_bridge.openapi.yaml`

Use system instructions:

- `actions/MULTI_PROJECT_MONGO_BRIDGE_GPT_SYSTEM_PROMPT.md`

## 5) Local quick checks

### A) Run project once (generates queued Mongo job)

```powershell
python jarvis_runner.py --project cat_pod_us --dedup-hours 0
```

### B) Diagnose Mongo queue

```powershell
python tools/mongo_bridge_diag.py --project cat_pod_us
```

### C) Manually enqueue from outbox latest (if needed)

```powershell
python tools/mongo_enqueue_from_outbox.py --project cat_pod_us
```

If Mongo is temporarily unavailable, the command now falls back to local pending files:
- `data/mongo_bridge_pending/<project_id>/pending_*.json`

To skip Mongo immediately and enqueue local pending without waiting for network timeouts:

```powershell
python tools/mongo_enqueue_from_outbox.py --project cat_pod_us --force-local
```

If configured outbox path is inaccessible (for example `G:` permissions), pass an explicit input file:

```powershell
python tools/mongo_enqueue_from_outbox.py --project cat_pod_us --force-local --input-path "data\\intel_bridge_exports\\cat_pod_us\\<file>.intel_input.md"
```

### D) Manually save result JSON to Mongo (if needed)

```powershell
python tools/mongo_save_intel_result.py --project cat_pod_us --result-path path\\to\\intel_result.json
```

### E) Full local bridge flow from terminal (claim/complete/fail)

```powershell
# Health
python tools/mongo_bridge_cli.py health

# Preview next queued job (without claim)
python tools/mongo_bridge_cli.py claim --project cat_pod_us --dry-run

# Claim next queued job
python tools/mongo_bridge_cli.py claim --project cat_pod_us

# Complete claimed job with result JSON
python tools/mongo_bridge_cli.py complete --job-id <JOB_ID> --result-path path\\to\\intel_result.json

# Mark job failed
python tools/mongo_bridge_cli.py fail --job-id <JOB_ID> --error "schema_validation_failed"
```

### F) Ingest completed packets into tasks immediately

```powershell
python tools/manual_intel_ingest_once.py --project cat_pod_us
```

### G) Preflight + OpenAPI URL update

```powershell
# Readiness report (env/openapi/mongo/local settings)
python tools/mongo_bridge_preflight.py

# Inspect current OpenAPI server URL
python tools/set_mongo_bridge_server_url.py --inspect

# Set Azure Function URL in OpenAPI (+ optional env var)
python tools/set_mongo_bridge_server_url.py --url "https://<your-function-app>.azurewebsites.net" --update-env
```

### H) Diagnose Mongo TLS/DNS connectivity issues

```powershell
python tools/mongo_tls_diag.py
```

### I) Flush locally pending jobs after Mongo recovers

```powershell
python tools/mongo_flush_pending_jobs.py --project cat_pod_us
```

### J) Full local fallback loop (when Mongo TLS is down)

```powershell
# 1) Create local pending from outbox (automatic fallback)
python tools/mongo_enqueue_from_outbox.py --project cat_pod_us

# 2) Claim next pending local job
python tools/local_pending_bridge_cli.py claim --project cat_pod_us

# 3) Save your result JSON and complete local job (writes into intel_inbox)
python tools/local_pending_bridge_cli.py complete --project cat_pod_us --job-id <LOCAL_JOB_ID> --result-path path\\to\\intel_result.json

# 4) Ingest to tasks immediately
python tools/manual_intel_ingest_once.py --project cat_pod_us
```

### K) Telegram Project Focus Bot (single-project operating mode)

This bot keeps one active project at a time and exposes simple buttons:
- `ابدأ مشروع`
- `تشغيل قاعدة البيانات`
- `جهز رسالة GPT`
- `متابعة الرد`
- `استيراد النتيجة`
- `حالة`

Run:

```powershell
python tools/telegram_focus_bot.py
```

Optional flags:

```powershell
python tools/telegram_focus_bot.py --watch-interval-seconds 30 --wake-wait-seconds 90
```

Notes:
- Uses `TELEGRAM_BOT_TOKEN` and `JARVIS_TELEGRAM_FOCUS_CHAT_ID` (or `TELEGRAM_CHAT_ID`).
- Stores bot state at `data/telegram_focus/state.json`.
- Stores raw GPT packets exactly as received under `data/telegram_focus/raw_packets/<project_id>/`.

## 6) Recommended migration path

1. Keep Drive path active for fallback during first tests.
2. Run bridge flow for one project (`cat_pod_us`) until stable.
3. Move remaining projects in batches.
4. After stable phase, keep Drive as backup/archive only.
