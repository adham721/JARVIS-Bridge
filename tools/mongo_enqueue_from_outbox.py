from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import uuid
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_engine.bridge_paths import resolve_intel_outbox_base
from jarvis_engine.mongo_intel_bridge import enqueue_intel_job, mongo_bridge_enabled


def _pending_path(project_id: str) -> Path:
    root = REPO_ROOT / "data" / "mongo_bridge_pending" / str(project_id).strip()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"pending_{stamp}_{uuid.uuid4().hex[:10]}.json"


def _save_pending_local(project_id: str, intel_input: str, source: str, meta: Dict[str, Any]) -> Path:
    path = _pending_path(project_id)
    payload = {
        "schema_version": 1,
        "project_id": str(project_id).strip(),
        "source": str(source or "manual_cli"),
        "input_markdown": str(intel_input or ""),
        "meta": dict(meta or {}),
        "status": "pending_local",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _latest_local_export(project_id: str) -> Path:
    export_dir = REPO_ROOT / "data" / "intel_bridge_exports" / str(project_id).strip()
    candidates = sorted(
        [p for p in export_dir.glob("*.intel_input.md") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"No local intel_input exports found in {export_dir}")
    return candidates[0]


def _read_input(project_id: str, input_path: str = "") -> tuple[Path, str]:
    # Explicit input path wins.
    if str(input_path or "").strip():
        p = Path(str(input_path).strip()).expanduser().resolve()
        if not p.exists():
            raise RuntimeError(f"input file not found: {p}")
        text = p.read_text(encoding="utf-8-sig")
        if not text.strip():
            raise RuntimeError(f"input file is empty: {p}")
        return p, text

    outbox = resolve_intel_outbox_base() / str(project_id).strip() / "intel_input.md"
    try:
        if outbox.exists():
            text = outbox.read_text(encoding="utf-8")
            if text.strip():
                return outbox, text
            raise RuntimeError(f"intel_input.md is empty: {outbox}")
        raise RuntimeError(f"intel_input.md not found: {outbox}")
    except Exception:
        # Fallback to local exported inputs when configured outbox is inaccessible.
        local = _latest_local_export(project_id)
        text = local.read_text(encoding="utf-8-sig")
        if not text.strip():
            raise RuntimeError(f"local export is empty: {local}")
        return local, text


def run(
    project_id: str,
    source: str,
    *,
    strict_mongo: bool = False,
    force_local: bool = False,
    input_path: str = "",
) -> Dict[str, Any]:
    if not mongo_bridge_enabled():
        raise RuntimeError("Mongo bridge disabled. Set JARVIS_MONGO_BRIDGE_ENABLED=1 in .env")

    outbox_path, text = _read_input(project_id, input_path=input_path)
    meta = {"outbox_latest_path": str(outbox_path)}
    if force_local:
        pending_path = _save_pending_local(project_id, text, source=source, meta=meta)
        return {
            "ok": True,
            "project_id": project_id,
            "status": "pending_local",
            "pending_path": str(pending_path),
            "outbox_latest_path": str(outbox_path),
            "warning": "Mongo enqueue skipped by --force-local",
        }
    try:
        job_id, status = enqueue_intel_job(
            project_id,
            text,
            source=source,
            meta=meta,
        )
        return {
            "ok": True,
            "project_id": project_id,
            "job_id": job_id,
            "status": status,
            "outbox_latest_path": str(outbox_path),
        }
    except Exception as e:
        if strict_mongo:
            raise
        pending_path = _save_pending_local(project_id, text, source=source, meta=meta)
        return {
            "ok": True,
            "project_id": project_id,
            "status": "pending_local",
            "pending_path": str(pending_path),
            "outbox_latest_path": str(outbox_path),
            "warning": f"Mongo enqueue failed; saved locally: {type(e).__name__}: {e}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read latest intel_input.md from outbox and enqueue it into Mongo intel_jobs."
    )
    parser.add_argument("--project", required=True, help="Project ID (e.g. cat_pod_us)")
    parser.add_argument("--source", default="manual_cli", help="Source tag for auditing")
    parser.add_argument(
        "--strict-mongo",
        action="store_true",
        help="Fail if Mongo enqueue fails (disable local pending fallback).",
    )
    parser.add_argument(
        "--force-local",
        action="store_true",
        help="Skip Mongo enqueue and write pending_local file immediately.",
    )
    parser.add_argument(
        "--input-path",
        default="",
        help="Optional path to intel_input.md (used if outbox path is inaccessible).",
    )
    args = parser.parse_args()

    try:
        report = run(
            args.project,
            source=args.source,
            strict_mongo=bool(args.strict_mongo),
            force_local=bool(args.force_local),
            input_path=str(args.input_path or ""),
        )
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "project_id": str(args.project).strip(),
                    "error": f"{type(e).__name__}: {e}",
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
