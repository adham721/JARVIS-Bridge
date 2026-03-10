# Kids Pod Action Fix Runbook (`404 File not found: .`)

This runbook executes the debug-first plan for `kids_pod`.

## Scope

- Project: `kids_pod`
- Outbox folder id: `1AkpTKV4Z6oC8yDObV7nQ9Hfr6B3BXBz3`
- Inbox folder id: `1IQE4KeralZmsOmxNEt4Tor-Ge7_oobpn`
- OAuth scope: `https://www.googleapis.com/auth/drive`

## Step 1 — Prepare Debug GPT

1. Create/open GPT named `kids_pod-debug`.
2. In **Actions**, delete old Google Drive action (if any).
3. Re-import: `actions/google_drive_intel.openapi.yaml`.
4. Set OAuth:
   - Auth URL: `https://accounts.google.com/o/oauth2/v2/auth`
   - Token URL: `https://oauth2.googleapis.com/token`
   - Scope: `https://www.googleapis.com/auth/drive`
5. Disconnect then reconnect Google account.
6. Paste system instructions from:
   - `actions/KIDS_POD_GPT_SYSTEM_PROMPT.md`

## Step 2 — Debug Test Sequence (inside GPT chat)

### A) Run diagnostics

Send:

```text
Diag
```

Expected:

- GPT returns first items from outbox folder with real ids.
- No `fileId='.'` error.

### B) Run full flow

Send:

```text
Start
```

Expected:

- Reads `intel_input.md`
- Writes/updates `intel_result.json`
- Returns output file id in final message

## Step 3 — Local Verification (PowerShell)

From `JARVIS-ContentEngine`:

```powershell
.\tools\verify_intel_bridge.ps1 -ProjectId kids_pod -DriveRoot "G:\My Drive"
```

Then import:

```powershell
.\tools\verify_intel_bridge.ps1 -ProjectId kids_pod -DriveRoot "G:\My Drive" -RunImport
```

Acceptance after import:

- `G:\My Drive\JARVIS_INTEL_INBOX\kids_pod\intel_result.json` updated
- `intel_result.json.imported` exists/updated
- runner completes without intel parse errors

## Step 4 — Promote to Production GPT

After debug GPT passes:

1. Open production GPT (`JARVIS - Kids Pod ...`).
2. Replace action by re-importing same OpenAPI file.
3. Reconnect OAuth.
4. Copy same system instructions.
5. Run `Diag` then `Start`.

## Troubleshooting Matrix

- `404 File not found: .`
  - Action still stale or malformed call path.
  - Fix: delete action, re-import OpenAPI, reconnect OAuth, retry `Diag`.

- `No input file found in Outbox folder`
  - Wrong folder id or wrong Google account.
  - Fix: confirm ids above and account access; rerun `Diag`.

- Can read but cannot write `intel_result.json`
  - Inbox folder id mismatch or token scope issue.
  - Fix: verify inbox id and reconnect with `drive` scope.
