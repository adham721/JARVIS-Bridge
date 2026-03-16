from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Tuple

import azure.functions as func
from bson import ObjectId
from pymongo import ASCENDING, MongoClient, ReturnDocument

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_response(payload: Dict[str, Any], status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(payload, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
    )


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _check_api_key(req: func.HttpRequest) -> Tuple[bool, func.HttpResponse | None]:
    expected = _env("JARVIS_BRIDGE_API_KEY")
    if not expected:
        # In dev mode, allow empty key. Set this in production.
        return True, None
    provided = str(req.headers.get("x-jarvis-key") or "").strip()
    if provided == expected:
        return True, None
    return False, _json_response({"ok": False, "error": "unauthorized"}, status_code=401)


@lru_cache(maxsize=1)
def _mongo_client() -> MongoClient:
    uri = _env("JARVIS_MONGO_URI")
    if not uri:
        raise RuntimeError("JARVIS_MONGO_URI is required")
    timeout_ms = _int_env("JARVIS_MONGO_CONNECT_TIMEOUT_MS", 6000)
    return MongoClient(uri, serverSelectionTimeoutMS=max(1000, timeout_ms))


def _db():
    name = _env("JARVIS_MONGO_DB", "jarvis_intel")
    return _mongo_client()[name]


def _jobs_coll():
    return _db()[_env("JARVIS_MONGO_JOBS_COLLECTION", "intel_jobs")]


def _packets_coll():
    return _db()[_env("JARVIS_MONGO_INTEL_COLLECTION", "intel_packets")]


def _job_public(doc: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not doc:
        return None
    return {
        "job_id": str(doc.get("_id")),
        "project_id": str(doc.get("project_id") or ""),
        "status": str(doc.get("status") or ""),
        "type": str(doc.get("type") or ""),
        "created_at": _iso(doc.get("created_at") or _utc_now()),
        "updated_at": _iso(doc.get("updated_at") or _utc_now()),
        "claimed_at": _iso(doc.get("claimed_at")) if doc.get("claimed_at") else None,
        "lock_expires_at": _iso(doc.get("lock_expires_at")) if doc.get("lock_expires_at") else None,
        "input_markdown": str(doc.get("input_markdown") or ""),
        "meta": doc.get("meta") if isinstance(doc.get("meta"), dict) else {},
    }


def _parse_json(req: func.HttpRequest) -> Dict[str, Any]:
    body = req.get_body()
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"invalid json body: {e}")
    if not isinstance(parsed, dict):
        raise ValueError("json body must be an object")
    return parsed


@app.route(route="v1/health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    ok, denied = _check_api_key(req)
    if not ok:
        return denied  # type: ignore[return-value]
    try:
        ping = dict(_mongo_client().admin.command("ping") or {})
        return _json_response({"ok": True, "service": "jarvis-bridge", "mongo": ping, "time": _iso(_utc_now())})
    except Exception as e:
        return _json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.route(route="v1/jobs/create", methods=["POST"])
def create_job(req: func.HttpRequest) -> func.HttpResponse:
    ok, denied = _check_api_key(req)
    if not ok:
        return denied  # type: ignore[return-value]

    try:
        body = _parse_json(req)
        project_id = str(body.get("project_id") or "").strip()
        input_markdown = str(body.get("input_markdown") or "").strip()
        job_type = str(body.get("type") or "intel_research").strip() or "intel_research"
        source = str(body.get("source") or "bridge_api").strip()
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        if not project_id:
            return _json_response({"ok": False, "error": "project_id is required"}, status_code=400)
        if not input_markdown:
            return _json_response({"ok": False, "error": "input_markdown is required"}, status_code=400)

        now = _utc_now()
        doc = {
            "project_id": project_id,
            "type": job_type,
            "status": "queued",
            "source": source,
            "input_markdown": input_markdown,
            "meta": meta,
            "created_at": now,
            "updated_at": now,
        }
        result = _jobs_coll().insert_one(doc)
        return _json_response(
            {
                "ok": True,
                "job_id": str(result.inserted_id),
                "project_id": project_id,
                "status": "queued",
            },
            status_code=201,
        )
    except Exception as e:
        return _json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.route(route="v1/jobs/next", methods=["GET"])
def next_job(req: func.HttpRequest) -> func.HttpResponse:
    ok, denied = _check_api_key(req)
    if not ok:
        return denied  # type: ignore[return-value]

    project_id = str(req.params.get("project_id") or "").strip()
    if not project_id:
        return _json_response({"ok": False, "error": "project_id query param is required"}, status_code=400)
    job_type = str(req.params.get("type") or "").strip()

    try:
        lock_for = _int_env("JARVIS_BRIDGE_JOB_LOCK_SECONDS", 900)
        if req.params.get("lock_for_seconds"):
            try:
                lock_for = max(60, int(req.params.get("lock_for_seconds") or "900"))
            except Exception:
                lock_for = _int_env("JARVIS_BRIDGE_JOB_LOCK_SECONDS", 900)

        now = _utc_now()
        lock_expires = now + timedelta(seconds=max(60, lock_for))
        query = {
            "project_id": project_id,
            "$or": [{"status": "queued"}, {"status": "new"}],
        }
        if job_type:
            query["type"] = job_type
        update = {
            "$set": {
                "status": "processing",
                "claimed_at": now,
                "lock_expires_at": lock_expires,
                "updated_at": now,
            }
        }
        doc = _jobs_coll().find_one_and_update(
            query,
            update,
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return _json_response({"ok": True, "project_id": project_id, "has_job": False, "job": None}, status_code=200)
        return _json_response({"ok": True, "project_id": project_id, "has_job": True, "job": _job_public(doc)}, status_code=200)
    except Exception as e:
        return _json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.route(route="v1/jobs/{job_id}/complete", methods=["POST"])
def complete_job(req: func.HttpRequest) -> func.HttpResponse:
    ok, denied = _check_api_key(req)
    if not ok:
        return denied  # type: ignore[return-value]

    job_id = str(req.route_params.get("job_id") or "").strip()
    if not job_id:
        return _json_response({"ok": False, "error": "job_id is required"}, status_code=400)

    try:
        body = _parse_json(req)
        result_json = body.get("result")
        if not isinstance(result_json, dict):
            return _json_response({"ok": False, "error": "body.result must be an object"}, status_code=400)
        source = str(body.get("source") or "custom_gpt").strip()
        notes = str(body.get("notes") or "").strip()
        now = _utc_now()

        obj_id = ObjectId(job_id)
        job = _jobs_coll().find_one({"_id": obj_id})
        if not job:
            return _json_response({"ok": False, "error": "job not found"}, status_code=404)

        packet_doc = {
            "project_id": str(job.get("project_id") or ""),
            "status": "ready",
            "source": source,
            "job_id": job_id,
            "packet": result_json,
            "imported": False,
            "meta": {
                "notes": notes,
                "job_source": str(job.get("source") or ""),
            },
            "created_at": now,
            "updated_at": now,
        }
        packet_insert = _packets_coll().insert_one(packet_doc)
        _jobs_coll().update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": now,
                    "updated_at": now,
                    "result_packet_id": str(packet_insert.inserted_id),
                }
            },
        )

        return _json_response(
            {
                "ok": True,
                "job_id": job_id,
                "packet_id": str(packet_insert.inserted_id),
                "status": "completed",
            }
        )
    except Exception as e:
        return _json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.route(route="v1/jobs/{job_id}/fail", methods=["POST"])
def fail_job(req: func.HttpRequest) -> func.HttpResponse:
    ok, denied = _check_api_key(req)
    if not ok:
        return denied  # type: ignore[return-value]

    job_id = str(req.route_params.get("job_id") or "").strip()
    if not job_id:
        return _json_response({"ok": False, "error": "job_id is required"}, status_code=400)

    try:
        body = _parse_json(req)
        error_message = str(body.get("error") or "").strip() or "unknown_error"
        details = body.get("details")
        now = _utc_now()
        obj_id = ObjectId(job_id)
        result = _jobs_coll().update_one(
            {"_id": obj_id},
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
            return _json_response({"ok": False, "error": "job not found"}, status_code=404)
        return _json_response({"ok": True, "job_id": job_id, "status": "failed"})
    except Exception as e:
        return _json_response({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
