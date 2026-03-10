# Custom GPT (Actions) — Google Drive Intel Workflow

Goal: trigger Deep Research with a single message (e.g. "ابدأ") without copy/paste.

High-level loop:

1) **JARVIS** writes `intel_input.md` into the Drive-synced **outbox**.
2) You send **"ابدأ"** to your **Custom GPT**.
3) The **Custom GPT** reads `intel_input.md` from Drive via Actions, runs research, and writes `intel_result.json` into the Drive-synced **inbox**.
4) **JARVIS** imports the JSON and continues the pipeline (tasks + reports + Telegram).

---

## Debug-first workflow (recommended)

For `kids_pod`, use a debug GPT first, then promote to production.

- Debug runbook: `actions/KIDS_POD_DEBUG_RUNBOOK.md`
- Debug/prod system instructions: `actions/KIDS_POD_GPT_SYSTEM_PROMPT.md`
- Action schema to import: `actions/google_drive_intel.openapi.yaml`

Pinned constants for `kids_pod`:

- `PROJECT_ID = kids_pod`
- `OUTBOX_PROJECT_FOLDER_ID = 1AkpTKV4Z6oC8yDObV7nQ9Hfr6B3BXBz3`
- `INBOX_PROJECT_FOLDER_ID = 1IQE4KeralZmsOmxNEt4Tor-Ge7_oobpn`
- OAuth scope: `https://www.googleapis.com/auth/drive`

---

## 0) Important security note (folder-only access)

Google OAuth **scopes cannot restrict access to a single folder**.

To keep this safe:

- Use a **dedicated Google account** (recommended) whose Drive contains only these JARVIS folders, **or**
- Use a backend "Drive Bridge" proxy (later) that enforces folder allow-lists.

---

## 1) Prepare the Drive folders (recommended structure)

Create two folders on Drive:

- `JARVIS_INTEL_OUTBOX` (contains prompts from JARVIS)
- `JARVIS_INTEL_INBOX` (contains results from the GPT)

Inside each one, create a folder per project (folder name equals `project_id`):

- `JARVIS_INTEL_OUTBOX/kids_pod/`
- `JARVIS_INTEL_INBOX/kids_pod/`

JARVIS writes a stable "latest" file:

- Outbox: `.../<project_id>/intel_input.md`

The Custom GPT should write a stable result file:

- Inbox: `.../<project_id>/intel_result.json`

JARVIS can re-import if the same file is overwritten (it uses hash markers).

---

## 2) Point JARVIS to your Drive-synced local folders

In `JARVIS-ContentEngine/.env`:

```ini
JARVIS_INTEL_OUTBOX_DIR=C:\Path\To\GoogleDrive\JARVIS_INTEL_OUTBOX
JARVIS_INTEL_DIR=C:\Path\To\GoogleDrive\JARVIS_INTEL_INBOX
JARVIS_INTEL_REQUESTS_MAX=2

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Run once to generate outbox files:

```powershell
python jarvis_runner.py --project kids_pod --dedup-hours 0
```

---

## 3) Create the Google OAuth app (Google Cloud Console)

You need an OAuth Client (web application) with Drive API enabled.

1) Create a Google Cloud project
2) Enable **Google Drive API**
3) Configure OAuth consent screen
4) Create OAuth Client ID (type: Web application)

You will need:

- Client ID
- Client secret

### Redirect / callback URLs (from OpenAI Actions docs)

Add both callback URLs (use your GPT ID in place of `{GPT_ID}`):

- `https://chat.openai.com/aip/{GPT_ID}/oauth/callback`
- `https://chatgpt.com/aip/{GPT_ID}/oauth/callback`

---

## 4) Add the Action to your Custom GPT

In the GPT editor:

1) Open **Actions**
2) Import `actions/google_drive_intel.openapi.yaml`
3) Set Authentication = **OAuth**
4) If Action was already imported before, **remove and re-import it** to pick up validation updates (`fileId` guard + `supportsAllDrives`).

OAuth settings:

- Authorization URL: `https://accounts.google.com/o/oauth2/v2/auth`
- Token URL: `https://oauth2.googleapis.com/token`
- Scope:
  - Recommended (simplest): `https://www.googleapis.com/auth/drive`
  - Safer (but may not read non-app-created files): `https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file`

---

## 5) Custom GPT Instructions (suggested)

Because you chose **one GPT per project**, you can hardcode these constants in the GPT instructions:

- `PROJECT_ID` (example: `kids_pod`)
- `OUTBOX_PROJECT_FOLDER_ID` (Drive folder id for `JARVIS_INTEL_OUTBOX/<PROJECT_ID>`)
- `INBOX_PROJECT_FOLDER_ID` (Drive folder id for `JARVIS_INTEL_INBOX/<PROJECT_ID>`)

This keeps calls minimal and avoids ambiguity.

Add these rules to your GPT instructions:

- If the user says **"ابدأ"** or **"Start"**:
  1) Use Drive search to find `intel_input.md` inside `OUTBOX_PROJECT_FOLDER_ID` (order by modifiedTime desc):
     - `q="'<OUTBOX_PROJECT_FOLDER_ID>' in parents and name='intel_input.md' and trashed=false"`
     - `supportsAllDrives=true`
  2) Download it with `alt=media`.
  3) Do the research and produce JSON output **matching the schema in the input file**.
  4) Create or update `intel_result.json` inside `INBOX_PROJECT_FOLDER_ID`:
     - If file exists: upload with `PATCH /upload/drive/v3/files/{fileId}?uploadType=media`
     - If missing: create metadata + upload content
- Never write outside `INBOX_PROJECT_FOLDER_ID`.
- Every finding must include at least one evidence URL.
- Output must be valid JSON.

Tip: keep API calls minimal (Drive API calls can time out).

---

## 6) `kids_pod` quick-fix profile (copy into your GPT instructions)

Use this when the GPT says `No input file found in Outbox folder`.

1) Set constants:

- `PROJECT_ID = kids_pod`
- `OUTBOX_PROJECT_FOLDER_ID = <Drive folder id for JARVIS_INTEL_OUTBOX/kids_pod>`
- `INBOX_PROJECT_FOLDER_ID = <Drive folder id for JARVIS_INTEL_INBOX/kids_pod>`

2) Trigger logic:

- If user message is `Start` or `ابدأ`, execute this flow:
  1) `drive_list_files` with:
     - `q="'<OUTBOX_PROJECT_FOLDER_ID>' in parents and name='intel_input.md' and trashed=false"`
     - `orderBy="modifiedTime desc"`
     - `pageSize=1`
     - `supportsAllDrives=true`
  2) If file exists:
     - download with `drive_get_file(fileId=..., alt=media, supportsAllDrives=true)`
     - produce JSON matching schema from input
  3) Upsert result in inbox:
     - find file with:
       - `q="'<INBOX_PROJECT_FOLDER_ID>' in parents and name='intel_result.json' and trashed=false"`
       - `pageSize=1`
       - `supportsAllDrives=true`
      - if exists: `drive_upload_file_content(fileId=..., uploadType=media)`
      - if missing: `drive_create_file_metadata(name='intel_result.json', parents=['<INBOX_PROJECT_FOLDER_ID>'], mimeType='application/json')`, then upload content

3) Diagnostic fallback (required):

- If step (1) returns no file, do **not** stop with generic error.
- Run `drive_list_files` with:
  - `q="'<OUTBOX_PROJECT_FOLDER_ID>' in parents and trashed=false"`
  - `orderBy="modifiedTime desc"`
  - `pageSize=5`
- Return a diagnostic response that includes:
  - `OUTBOX_PROJECT_FOLDER_ID` value currently used
  - Names of the first 5 items found (or "folder empty")
  - A hint that root outbox folder id is wrong if `intel_input.md` is missing
