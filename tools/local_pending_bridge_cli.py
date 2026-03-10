from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

from jarvis_engine.bridge_paths import resolve_intel_inbox_base


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_env() -> None:
    if load_dotenv is None:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


def _pending_dir(project_id: str) -> Path:
    return REPO_ROOT / "data" / "mongo_bridge_pending" / str(project_id).strip()


def _processing_dir(project_id: str) -> Path:
    return REPO_ROOT / "data" / "mongo_bridge_pending_processing" / str(project_id).strip()


def _done_dir(project_id: str) -> Path:
    return REPO_ROOT / "data" / "mongo_bridge_pending_done" / str(project_id).strip()


def _ensure_dirs(project_id: str) -> None:
    _pending_dir(project_id).mkdir(parents=True, exist_ok=True)
    _processing_dir(project_id).mkdir(parents=True, exist_ok=True)
    _done_dir(project_id).mkdir(parents=True, exist_ok=True)


def _iter_json(dir_path: Path) -> List[Path]:
    if not dir_path.exists():
        return []
    return sorted(
        [
            p
            for p in dir_path.glob("*.json")
            if p.is_file() and not p.name.endswith(".receipt.json")
        ],
        key=lambda x: x.name,
    )


def _job_id(path: Path) -> str:
    return path.stem


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return data


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _find_job_file(project_id: str, job_id: str, in_processing: bool = True) -> Optional[Path]:
    root = _processing_dir(project_id) if in_processing else _pending_dir(project_id)
    exact = root / f"{job_id}.json"
    if exact.exists():
        return exact
    for p in _iter_json(root):
        if p.stem == job_id:
            return p
    return None


def _claim(project_id: str) -> Dict[str, Any]:
    _ensure_dirs(project_id)
    pending = _iter_json(_pending_dir(project_id))
    if not pending:
        return {"ok": True, "project_id": project_id, "job": None}

    src = pending[0]
    dst = _processing_dir(project_id) / src.name
    shutil.move(str(src), str(dst))

    payload = _load_json(dst)
    payload["status"] = "processing_local"
    payload["local_job_id"] = _job_id(dst)
    payload["claimed_at"] = _utc_now_iso()
    _write_json(dst, payload)

    export_dir = REPO_ROOT / "data" / "intel_bridge_exports" / project_id
    export_dir.mkdir(parents=True, exist_ok=True)
    intel_input_path = export_dir / f"{_job_id(dst)}.intel_input.md"
    intel_input_path.write_text(str(payload.get("input_markdown") or ""), encoding="utf-8")

    return {
        "ok": True,
        "project_id": project_id,
        "job": {
            "job_id": _job_id(dst),
            "status": str(payload.get("status") or ""),
            "pending_source": str(src),
            "processing_path": str(dst),
            "intel_input_path": str(intel_input_path),
            "input_markdown_len": len(str(payload.get("input_markdown") or "")),
        },
    }


def _complete(project_id: str, job_id: str, result_path: Path, source: str) -> Dict[str, Any]:
    _ensure_dirs(project_id)
    proc_path = _find_job_file(project_id, job_id, in_processing=True)
    if proc_path is None:
        raise RuntimeError(f"processing job not found: {job_id}")
    result_obj = _load_json(result_path)

    warnings: List[str] = []
    inbox_base = resolve_intel_inbox_base()
    inbox_dir = inbox_base / project_id
    inbox_file = inbox_dir / f"local_pending_{job_id}_intel_result.json"
    try:
        inbox_dir.mkdir(parents=True, exist_ok=True)
        inbox_file.write_text(json.dumps(result_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as e:
        # Fallback to repo-local inbox when configured inbox is unreachable.
        warnings.append(f"configured inbox write failed: {type(e).__name__}: {e}")
        inbox_dir = REPO_ROOT / "data" / "intel_inbox" / project_id
        inbox_dir.mkdir(parents=True, exist_ok=True)
        inbox_file = inbox_dir / f"local_pending_{job_id}_intel_result.json"
        inbox_file.write_text(json.dumps(result_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    done_path = _done_dir(project_id) / proc_path.name
    shutil.move(str(proc_path), str(done_path))
    receipt = {
        "project_id": project_id,
        "job_id": job_id,
        "status": "completed_local",
        "source": str(source or "local_pending_bridge"),
        "completed_at": _utc_now_iso(),
        "result_path": str(result_path),
        "inbox_result_path": str(inbox_file),
    }
    (done_path.with_suffix(done_path.suffix + ".receipt.json")).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "ok": True,
        "project_id": project_id,
        "job_id": job_id,
        "status": "completed_local",
        "done_path": str(done_path),
        "inbox_result_path": str(inbox_file),
        "warnings": warnings,
    }


def _requeue(project_id: str, job_id: str, reason: str) -> Dict[str, Any]:
    _ensure_dirs(project_id)
    proc_path = _find_job_file(project_id, job_id, in_processing=True)
    if proc_path is None:
        raise RuntimeError(f"processing job not found: {job_id}")
    payload = _load_json(proc_path)
    payload["status"] = "pending_local"
    payload["requeued_at"] = _utc_now_iso()
    payload["requeue_reason"] = str(reason or "").strip()
    _write_json(proc_path, payload)
    pending_path = _pending_dir(project_id) / proc_path.name
    shutil.move(str(proc_path), str(pending_path))
    return {
        "ok": True,
        "project_id": project_id,
        "job_id": job_id,
        "status": "pending_local",
        "pending_path": str(pending_path),
    }


def _fail(project_id: str, job_id: str, error: str) -> Dict[str, Any]:
    _ensure_dirs(project_id)
    proc_path = _find_job_file(project_id, job_id, in_processing=True)
    if proc_path is None:
        raise RuntimeError(f"processing job not found: {job_id}")
    payload = _load_json(proc_path)
    payload["status"] = "failed_local"
    payload["failed_at"] = _utc_now_iso()
    payload["error"] = str(error or "").strip() or "unknown_error"
    _write_json(proc_path, payload)
    done_path = _done_dir(project_id) / proc_path.name
    shutil.move(str(proc_path), str(done_path))
    return {
        "ok": True,
        "project_id": project_id,
        "job_id": job_id,
        "status": "failed_local",
        "done_path": str(done_path),
    }


def _status(project_id: str, limit: int) -> Dict[str, Any]:
    _ensure_dirs(project_id)
    pending = _iter_json(_pending_dir(project_id))
    processing = _iter_json(_processing_dir(project_id))
    done = _iter_json(_done_dir(project_id))
    n = max(1, int(limit))
    return {
        "ok": True,
        "project_id": project_id,
        "counts": {"pending": len(pending), "processing": len(processing), "done": len(done)},
        "samples": {
            "pending": [p.name for p in pending[:n]],
            "processing": [p.name for p in processing[:n]],
            "done": [p.name for p in done[:n]],
        },
    }


def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Local fallback bridge over pending files (no Mongo required).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Show local pending/processing/done counts")
    p_status.add_argument("--project", required=True, help="Project ID")
    p_status.add_argument("--limit", type=int, default=10, help="Sample file count")

    p_claim = sub.add_parser("claim", help="Claim next pending file")
    p_claim.add_argument("--project", required=True, help="Project ID")

    p_complete = sub.add_parser("complete", help="Complete claimed local job by writing result into intel_inbox")
    p_complete.add_argument("--project", required=True, help="Project ID")
    p_complete.add_argument("--job-id", required=True, help="Local job id (pending filename without .json)")
    p_complete.add_argument("--result-path", required=True, help="Path to intel_result.json")
    p_complete.add_argument("--source", default="local_pending_bridge", help="Source tag")

    p_requeue = sub.add_parser("requeue", help="Move processing job back to pending")
    p_requeue.add_argument("--project", required=True, help="Project ID")
    p_requeue.add_argument("--job-id", required=True, help="Local job id")
    p_requeue.add_argument("--reason", default="", help="Optional reason")

    p_fail = sub.add_parser("fail", help="Mark processing job as failed_local and move to done")
    p_fail.add_argument("--project", required=True, help="Project ID")
    p_fail.add_argument("--job-id", required=True, help="Local job id")
    p_fail.add_argument("--error", required=True, help="Failure reason")

    args = parser.parse_args()
    try:
        if args.command == "status":
            payload = _status(str(args.project).strip(), int(args.limit))
        elif args.command == "claim":
            payload = _claim(str(args.project).strip())
        elif args.command == "complete":
            payload = _complete(
                str(args.project).strip(),
                str(args.job_id).strip(),
                Path(args.result_path).expanduser().resolve(),
                source=str(args.source),
            )
        elif args.command == "requeue":
            payload = _requeue(
                str(args.project).strip(),
                str(args.job_id).strip(),
                reason=str(args.reason),
            )
        elif args.command == "fail":
            payload = _fail(str(args.project).strip(), str(args.job_id).strip(), str(args.error))
        else:
            raise RuntimeError(f"unknown command: {args.command}")
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    except Exception as e:
        print(
            json.dumps(
                {"ok": False, "command": str(args.command or ""), "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
