from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from dotenv import dotenv_values


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
AZURE_LOCAL_SETTINGS_PATH = REPO_ROOT / "bridge" / "azure_functions" / "local.settings.json"


def _pick(env: Dict[str, str], key: str, default: str = "") -> str:
    return str(env.get(key) or default).strip()


def build_local_settings(env: Dict[str, str]) -> Dict[str, object]:
    return {
        "IsEncrypted": False,
        "Values": {
            "FUNCTIONS_WORKER_RUNTIME": "python",
            "AzureWebJobsStorage": _pick(env, "AzureWebJobsStorage", "UseDevelopmentStorage=true"),
            "JARVIS_BRIDGE_API_KEY": _pick(env, "JARVIS_BRIDGE_API_KEY"),
            "JARVIS_MONGO_URI": _pick(env, "JARVIS_MONGO_URI"),
            "JARVIS_MONGO_DB": _pick(env, "JARVIS_MONGO_DB", "jarvis_intel"),
            "JARVIS_MONGO_JOBS_COLLECTION": _pick(env, "JARVIS_MONGO_JOBS_COLLECTION", "intel_jobs"),
            "JARVIS_MONGO_INTEL_COLLECTION": _pick(env, "JARVIS_MONGO_INTEL_COLLECTION", "intel_packets"),
            "JARVIS_MONGO_CONNECT_TIMEOUT_MS": _pick(env, "JARVIS_MONGO_CONNECT_TIMEOUT_MS", "6000"),
            "JARVIS_BRIDGE_JOB_LOCK_SECONDS": _pick(env, "JARVIS_BRIDGE_JOB_LOCK_SECONDS", "900"),
        },
    }


def main() -> int:
    if not ENV_PATH.exists():
        print(json.dumps({"ok": False, "error": f".env not found: {ENV_PATH}"}, ensure_ascii=True, indent=2))
        return 2

    raw = dotenv_values(ENV_PATH)
    env = {str(k): str(v) for k, v in raw.items() if k and v is not None}
    payload = build_local_settings(env)

    AZURE_LOCAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AZURE_LOCAL_SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    values = payload.get("Values") if isinstance(payload.get("Values"), dict) else {}
    uri_len = len(str(values.get("JARVIS_MONGO_URI") or ""))
    key_len = len(str(values.get("JARVIS_BRIDGE_API_KEY") or ""))
    print(
        json.dumps(
            {
                "ok": True,
                "path": str(AZURE_LOCAL_SETTINGS_PATH),
                "mongo_uri": "<empty>" if uri_len == 0 else f"<set len={uri_len}>",
                "bridge_key": "<empty>" if key_len == 0 else f"<set len={key_len}>",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
