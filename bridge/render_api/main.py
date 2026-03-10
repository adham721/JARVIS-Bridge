from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict

from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo import ASCENDING, MongoClient, ReturnDocument

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


def _load_env() -> None:
    if load_dotenv is None:
        return
    load_dotenv(override=False)


_load_env()

app = FastAPI(title="JARVIS Mongo Intel Bridge API", version="1.0.0")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


@lru_cache(maxsize=1)
def _mongo_client() -> MongoClient:
    uri = _env("JARVIS_MONGO_URI")
    if not uri:
        raise RuntimeError("JARVIS_MONGO_URI is required")
    timeout_ms = _int_env("JARVIS_MONGO_CONNECT_TIMEOUT_MS", 6000)
    return MongoClient(uri, serverSelectionTimeoutMS=max(1000, timeout_ms))


def _db():
    return _mongo_client()[_env("JARVIS_MONGO_DB", "jarvis_intel")]


def _jobs():
    return _db()[_env("JARVIS_MONGO_JOBS_COLLECTION", "intel_jobs")]


def _packets():
    return _db()[_env("JARVIS_MONGO_INTEL_COLLECTION", "intel_packets")]


def _ok(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


def _err(error: str, status_code: int = 500) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(error)}, status_code=status_code)


def _job_public(doc: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not doc:
        return None
    return {
        "job_id": str(doc.get("_id")),
        "project_id": str(doc.get("project_id") or ""),
        "status": str(doc.get("status") or ""),
        "type": str(doc.get("type") or ""),
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
        "claimed_at": _iso(doc.get("claimed_at")),
        "lock_expires_at": _iso(doc.get("lock_expires_at")),
        "input_markdown": str(doc.get("input_markdown") or ""),
        "meta": doc.get("meta") if isinstance(doc.get("meta"), dict) else {},
    }


def _require_api_key(x_jarvis_key: str | None = Header(default=None)) -> None:
    expected = _env("JARVIS_BRIDGE_API_KEY")
    if not expected:
        return
    provided = str(x_jarvis_key or "").strip()
    if provided == expected:
        return
    raise HTTPException(status_code=401, detail={"ok": False, "error": "unauthorized"})


class CreateJobBody(BaseModel):
    project_id: str
    input_markdown: str
    source: str = "bridge_api"
    meta: Dict[str, Any] = Field(default_factory=dict)


class CompleteJobBody(BaseModel):
    result: Dict[str, Any]
    source: str = "custom_gpt"
    notes: str = ""


class FailJobBody(BaseModel):
    error: str
    details: Dict[str, Any] = Field(default_factory=dict)


@app.get("/api/v1/health")
def health(_: None = Depends(_require_api_key)) -> JSONResponse:
    try:
        ping = dict(_mongo_client().admin.command("ping") or {})
        return _ok({"ok": True, "service": "jarvis-bridge", "mongo": ping, "time": _iso(_utc_now())})
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", status_code=500)


@app.get("/healthz")
def healthz() -> JSONResponse:
    # Unauthenticated lightweight health endpoint for Render health checks.
    return _ok({"ok": True, "service": "jarvis-bridge", "time": _iso(_utc_now())}, status_code=200)


@app.post("/api/v1/jobs/create")
def create_job(body: CreateJobBody, _: None = Depends(_require_api_key)) -> JSONResponse:
    try:
        project_id = str(body.project_id or "").strip()
        input_markdown = str(body.input_markdown or "").strip()
        if not project_id:
            return _err("project_id is required", status_code=400)
        if not input_markdown:
            return _err("input_markdown is required", status_code=400)

        now = _utc_now()
        doc = {
            "project_id": project_id,
            "type": "intel_research",
            "status": "queued",
            "source": str(body.source or "bridge_api").strip() or "bridge_api",
            "input_markdown": input_markdown,
            "meta": dict(body.meta or {}),
            "created_at": now,
            "updated_at": now,
        }
        result = _jobs().insert_one(doc)
        return _ok(
            {
                "ok": True,
                "job_id": str(result.inserted_id),
                "project_id": project_id,
                "status": "queued",
            },
            status_code=201,
        )
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", status_code=500)


@app.get("/api/v1/jobs/next")
def next_job(project_id: str = "", lock_for_seconds: int = 900, _: None = Depends(_require_api_key)) -> JSONResponse:
    try:
        project_id = str(project_id or "").strip()
        if not project_id:
            return _err("project_id query param is required", status_code=400)

        now = _utc_now()
        lock_for = max(60, int(lock_for_seconds or _int_env("JARVIS_BRIDGE_JOB_LOCK_SECONDS", 900)))
        lock_expires = now + timedelta(seconds=lock_for)

        query = {"project_id": project_id, "$or": [{"status": "queued"}, {"status": "new"}]}
        update = {
            "$set": {
                "status": "processing",
                "claimed_at": now,
                "lock_expires_at": lock_expires,
                "updated_at": now,
            }
        }
        doc = _jobs().find_one_and_update(
            query,
            update,
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return _ok({"ok": True, "project_id": project_id, "job": _job_public(doc)})
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", status_code=500)


@app.post("/api/v1/jobs/{job_id}/complete")
def complete_job(job_id: str, body: CompleteJobBody, _: None = Depends(_require_api_key)) -> JSONResponse:
    try:
        raw_job_id = str(job_id or "").strip()
        if not raw_job_id:
            return _err("job_id is required", status_code=400)

        obj_id = ObjectId(raw_job_id)
        job = _jobs().find_one({"_id": obj_id})
        if not job:
            return _err("job not found", status_code=404)

        now = _utc_now()
        packet_doc = {
            "project_id": str(job.get("project_id") or ""),
            "status": "ready",
            "source": str(body.source or "custom_gpt").strip() or "custom_gpt",
            "job_id": raw_job_id,
            "packet": dict(body.result or {}),
            "imported": False,
            "meta": {
                "notes": str(body.notes or "").strip(),
                "job_source": str(job.get("source") or ""),
            },
            "created_at": now,
            "updated_at": now,
        }
        packet_insert = _packets().insert_one(packet_doc)
        _jobs().update_one(
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
        return _ok(
            {
                "ok": True,
                "job_id": raw_job_id,
                "packet_id": str(packet_insert.inserted_id),
                "status": "completed",
            }
        )
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", status_code=500)


@app.post("/api/v1/jobs/{job_id}/fail")
def fail_job(job_id: str, body: FailJobBody, _: None = Depends(_require_api_key)) -> JSONResponse:
    try:
        raw_job_id = str(job_id or "").strip()
        if not raw_job_id:
            return _err("job_id is required", status_code=400)
        error_message = str(body.error or "").strip() or "unknown_error"
        details = dict(body.details or {})
        now = _utc_now()

        obj_id = ObjectId(raw_job_id)
        result = _jobs().update_one(
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
            return _err("job not found", status_code=404)
        return _ok({"ok": True, "job_id": raw_job_id, "status": "failed"})
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}", status_code=500)
