from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

from pymongo import MongoClient


OPENAPI_PATH = REPO_ROOT / "actions" / "mongo_intel_bridge.openapi.yaml"
LOCAL_SETTINGS_PATH = REPO_ROOT / "bridge" / "azure_functions" / "local.settings.json"


def _load_env() -> None:
    if load_dotenv is None:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _mask(value: str) -> str:
    if not value:
        return "<empty>"
    return f"<set len={len(value)}>"


def _check_openapi_server() -> Dict[str, Any]:
    if not OPENAPI_PATH.exists():
        return {"ok": False, "error": f"missing file: {OPENAPI_PATH}"}
    text = OPENAPI_PATH.read_text(encoding="utf-8")
    url = ""
    in_servers = False
    for raw in text.splitlines():
        s = raw.strip()
        if s == "servers:":
            in_servers = True
            continue
        if in_servers and s.startswith("- url:"):
            url = s.split(":", 1)[1].strip()
            break
        if in_servers and s and not s.startswith("-"):
            break
    if not url:
        return {"ok": False, "error": "servers.url not found"}
    return {
        "ok": True,
        "url": url,
        "is_placeholder": "YOUR_AZURE_FUNCTION_HOST" in url,
    }


def _check_mongo() -> Dict[str, Any]:
    uri = _env("JARVIS_MONGO_URI")
    db_name = _env("JARVIS_MONGO_DB", "jarvis_intel")
    jobs_name = _env("JARVIS_MONGO_JOBS_COLLECTION", "intel_jobs")
    packets_name = _env("JARVIS_MONGO_INTEL_COLLECTION", "intel_packets")
    timeout = int(_env("JARVIS_MONGO_CONNECT_TIMEOUT_MS", "6000") or "6000")
    if not uri:
        return {"ok": False, "error": "JARVIS_MONGO_URI is empty"}
    client = MongoClient(uri, serverSelectionTimeoutMS=max(1000, timeout))
    ping = dict(client.admin.command("ping") or {})
    db = client[db_name]
    jobs = db[jobs_name]
    packets = db[packets_name]
    queued = jobs.count_documents({"status": {"$in": ["queued", "new"]}})
    processing = jobs.count_documents({"status": "processing"})
    ready_packets = packets.count_documents(
        {
            "$and": [
                {"$or": [{"status": {"$in": ["queued", "ready", "new"]}}, {"status": {"$exists": False}}]},
                {"$or": [{"imported": {"$ne": True}}, {"imported": {"$exists": False}}]},
            ]
        }
    )
    return {
        "ok": True,
        "ping": ping,
        "queue_counts": {"queued_or_new_jobs": int(queued), "processing_jobs": int(processing)},
        "ready_packets_not_imported": int(ready_packets),
    }


def run(skip_mongo: bool) -> Dict[str, Any]:
    _load_env()
    checks: Dict[str, Any] = {}
    warnings: List[str] = []

    enabled = _env("JARVIS_MONGO_BRIDGE_ENABLED", "0")
    bridge_key = _env("JARVIS_BRIDGE_API_KEY")
    checks["env"] = {
        "bridge_enabled": enabled,
        "mongo_uri": _mask(_env("JARVIS_MONGO_URI")),
        "bridge_api_key": _mask(bridge_key),
        "server_url_env": _env("JARVIS_BRIDGE_SERVER_URL"),
    }
    if enabled not in {"1", "true", "True", "yes", "on"}:
        warnings.append("JARVIS_MONGO_BRIDGE_ENABLED is not enabled")
    if not bridge_key:
        warnings.append("JARVIS_BRIDGE_API_KEY is empty")

    checks["openapi"] = _check_openapi_server()
    if checks["openapi"].get("ok") and checks["openapi"].get("is_placeholder"):
        warnings.append("OpenAPI server URL is still placeholder")

    checks["local_settings"] = {
        "exists": LOCAL_SETTINGS_PATH.exists(),
        "path": str(LOCAL_SETTINGS_PATH),
    }
    if not LOCAL_SETTINGS_PATH.exists():
        warnings.append("local.settings.json missing (run tools/sync_azure_local_settings.py)")

    if skip_mongo:
        checks["mongo"] = {"ok": True, "skipped": True}
    else:
        try:
            checks["mongo"] = _check_mongo()
        except Exception as e:
            checks["mongo"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            warnings.append("Mongo connectivity check failed")

    ok = not warnings
    return {"ok": ok, "warnings": warnings, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for Mongo Bridge readiness.")
    parser.add_argument("--skip-mongo", action="store_true", help="Skip live Mongo connectivity check")
    args = parser.parse_args()

    payload = run(skip_mongo=bool(args.skip_mongo))
    print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
