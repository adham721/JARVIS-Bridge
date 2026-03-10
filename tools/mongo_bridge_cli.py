from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

from bson import ObjectId
from pymongo import ASCENDING, MongoClient, ReturnDocument


def _load_env() -> None:
    if load_dotenv is None:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mongo_client() -> MongoClient:
    uri = _env("JARVIS_MONGO_URI")
    if not uri:
        raise RuntimeError("JARVIS_MONGO_URI is required")
    timeout = int(_env("JARVIS_MONGO_CONNECT_TIMEOUT_MS", "6000") or "6000")
    return MongoClient(uri, serverSelectionTimeoutMS=max(1000, timeout))


def _db():
    return _mongo_client()[_env("JARVIS_MONGO_DB", "jarvis_intel")]


def _jobs():
    return _db()[_env("JARVIS_MONGO_JOBS_COLLECTION", "intel_jobs")]


def _packets():
    return _db()[_env("JARVIS_MONGO_INTEL_COLLECTION", "intel_packets")]


def _job_public(doc: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not doc:
        return None
    return {
        "job_id": str(doc.get("_id")),
        "project_id": str(doc.get("project_id") or ""),
        "status": str(doc.get("status") or ""),
        "type": str(doc.get("type") or ""),
        "created_at": _to_iso(doc.get("created_at")),
        "updated_at": _to_iso(doc.get("updated_at")),
        "claimed_at": _to_iso(doc.get("claimed_at")),
        "lock_expires_at": _to_iso(doc.get("lock_expires_at")),
        "input_markdown": str(doc.get("input_markdown") or ""),
        "meta": doc.get("meta") if isinstance(doc.get("meta"), dict) else {},
    }


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_health(_: argparse.Namespace) -> int:
    ping = dict(_mongo_client().admin.command("ping") or {})
    _print_json({"ok": True, "ping": ping, "time": _to_iso(_utc_now())})
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    project_id = str(args.project).strip()
    if not project_id:
        raise RuntimeError("--project is required")

    query = {"project_id": project_id, "$or": [{"status": "queued"}, {"status": "new"}]}
    if args.dry_run:
        doc = _jobs().find_one(query, sort=[("created_at", ASCENDING)])
        _print_json({"ok": True, "project_id": project_id, "dry_run": True, "job": _job_public(doc)})
        return 0

    now = _utc_now()
    lock_for = max(60, int(args.lock_for_seconds))
    lock_expires_at = now + timedelta(seconds=lock_for)
    update = {
        "$set": {
            "status": "processing",
            "claimed_at": now,
            "lock_expires_at": lock_expires_at,
            "updated_at": now,
        }
    }
    doc = _jobs().find_one_and_update(
        query,
        update,
        sort=[("created_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    _print_json({"ok": True, "project_id": project_id, "job": _job_public(doc)})
    return 0


def _load_json_obj(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"result file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise RuntimeError("result JSON root must be object")
    return raw


def _cmd_complete(args: argparse.Namespace) -> int:
    job_id = str(args.job_id).strip()
    if not job_id:
        raise RuntimeError("--job-id is required")

    job = _jobs().find_one({"_id": ObjectId(job_id)})
    if not job:
        _print_json({"ok": False, "error": "job not found", "job_id": job_id})
        return 2

    result_obj = _load_json_obj(Path(args.result_path).expanduser().resolve())
    source = str(args.source or "custom_gpt").strip() or "custom_gpt"
    notes = str(args.notes or "").strip()
    now = _utc_now()

    packet_doc = {
        "project_id": str(job.get("project_id") or ""),
        "status": "ready",
        "source": source,
        "job_id": job_id,
        "packet": result_obj,
        "imported": False,
        "meta": {"notes": notes, "job_source": str(job.get("source") or ""), "from": "mongo_bridge_cli"},
        "created_at": now,
        "updated_at": now,
    }
    packet_insert = _packets().insert_one(packet_doc)
    _jobs().update_one(
        {"_id": ObjectId(job_id)},
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
                "result_packet_id": str(packet_insert.inserted_id),
            }
        },
    )
    _print_json(
        {
            "ok": True,
            "job_id": job_id,
            "packet_id": str(packet_insert.inserted_id),
            "status": "completed",
        }
    )
    return 0


def _cmd_fail(args: argparse.Namespace) -> int:
    job_id = str(args.job_id).strip()
    if not job_id:
        raise RuntimeError("--job-id is required")
    error_message = str(args.error or "").strip() or "unknown_error"
    now = _utc_now()
    details: Dict[str, Any] = {}
    if args.details_json:
        details = _load_json_obj(Path(args.details_json).expanduser().resolve())

    result = _jobs().update_one(
        {"_id": ObjectId(job_id)},
        {
            "$set": {
                "status": "failed",
                "error": error_message,
                "error_details": details,
                "failed_at": now,
                "updated_at": now,
            }
        },
    )
    if result.matched_count <= 0:
        _print_json({"ok": False, "error": "job not found", "job_id": job_id})
        return 2
    _print_json({"ok": True, "job_id": job_id, "status": "failed"})
    return 0


def _cmd_requeue(args: argparse.Namespace) -> int:
    job_id = str(args.job_id).strip()
    if not job_id:
        raise RuntimeError("--job-id is required")
    now = _utc_now()
    result = _jobs().update_one(
        {"_id": ObjectId(job_id)},
        {
            "$set": {
                "status": "queued",
                "updated_at": now,
            },
            "$unset": {
                "claimed_at": "",
                "lock_expires_at": "",
                "completed_at": "",
                "failed_at": "",
                "error": "",
                "error_details": "",
                "result_packet_id": "",
            },
        },
    )
    if result.matched_count <= 0:
        _print_json({"ok": False, "error": "job not found", "job_id": job_id})
        return 2
    _print_json({"ok": True, "job_id": job_id, "status": "queued"})
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mongo bridge CLI for local claim/complete/fail flows.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health", help="Ping Mongo bridge backend")
    p_health.set_defaults(func=_cmd_health)

    p_claim = sub.add_parser("claim", help="Claim next queued job for project")
    p_claim.add_argument("--project", required=True, help="Project ID (e.g. cat_pod_us)")
    p_claim.add_argument("--lock-for-seconds", type=int, default=900, help="Lock duration in seconds")
    p_claim.add_argument("--dry-run", action="store_true", help="Preview next queued job without claiming it")
    p_claim.set_defaults(func=_cmd_claim)

    p_complete = sub.add_parser("complete", help="Complete a job and insert result packet")
    p_complete.add_argument("--job-id", required=True, help="Mongo job id")
    p_complete.add_argument("--result-path", required=True, help="Path to intel_result.json")
    p_complete.add_argument("--source", default="custom_gpt", help="Source tag")
    p_complete.add_argument("--notes", default="", help="Optional completion notes")
    p_complete.set_defaults(func=_cmd_complete)

    p_fail = sub.add_parser("fail", help="Mark a job as failed")
    p_fail.add_argument("--job-id", required=True, help="Mongo job id")
    p_fail.add_argument("--error", required=True, help="Failure message")
    p_fail.add_argument("--details-json", default="", help="Optional path to JSON details object")
    p_fail.set_defaults(func=_cmd_fail)

    p_requeue = sub.add_parser("requeue", help="Reset a job back to queued state")
    p_requeue.add_argument("--job-id", required=True, help="Mongo job id")
    p_requeue.set_defaults(func=_cmd_requeue)

    return parser


def main() -> int:
    _load_env()
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as e:
        _print_json({"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
