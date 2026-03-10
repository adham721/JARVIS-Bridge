from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_engine.mongo_intel_bridge import enqueue_intel_job, mongo_bridge_enabled


def _pending_root() -> Path:
    return REPO_ROOT / "data" / "mongo_bridge_pending"


def _sent_root() -> Path:
    return REPO_ROOT / "data" / "mongo_bridge_pending_sent"


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError("pending payload must be object")
    return data


def _iter_pending(project_id: str) -> List[Path]:
    root = _pending_root() / str(project_id).strip()
    if not root.exists():
        return []
    return sorted([p for p in root.glob("*.json") if p.is_file()], key=lambda x: x.name)


def _move_to_sent(path: Path, receipt: Dict[str, Any]) -> Path:
    sent_dir = _sent_root() / path.parent.name
    sent_dir.mkdir(parents=True, exist_ok=True)
    sent_path = sent_dir / path.name
    shutil.move(str(path), str(sent_path))
    # Sidecar receipt for traceability.
    receipt_path = sent_path.with_suffix(sent_path.suffix + ".receipt.json")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sent_path


def run(project_id: str, limit: int) -> Dict[str, Any]:
    if not mongo_bridge_enabled():
        raise RuntimeError("Mongo bridge disabled. Set JARVIS_MONGO_BRIDGE_ENABLED=1 in .env")

    pending = _iter_pending(project_id)[: max(1, int(limit))]
    if not pending:
        return {
            "ok": True,
            "project_id": project_id,
            "pending_found": 0,
            "flushed": 0,
            "failed": 0,
            "details": [],
        }

    details: List[Dict[str, Any]] = []
    flushed = 0
    failed = 0
    for path in pending:
        try:
            payload = _load_json(path)
            input_markdown = str(payload.get("input_markdown") or "").strip()
            source = str(payload.get("source") or "pending_local").strip() or "pending_local"
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            if not input_markdown:
                raise RuntimeError("input_markdown is empty")

            job_id, status = enqueue_intel_job(
                project_id,
                input_markdown,
                source=source,
                meta=meta,
            )
            sent_path = _move_to_sent(
                path,
                {
                    "project_id": project_id,
                    "job_id": job_id,
                    "status": status,
                    "flushed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "source_pending_file": str(path),
                },
            )
            flushed += 1
            details.append(
                {
                    "ok": True,
                    "pending_path": str(path),
                    "sent_path": str(sent_path),
                    "job_id": job_id,
                    "status": status,
                }
            )
        except Exception as e:
            failed += 1
            details.append({"ok": False, "pending_path": str(path), "error": f"{type(e).__name__}: {e}"})

    return {
        "ok": failed == 0,
        "project_id": project_id,
        "pending_found": len(pending),
        "flushed": flushed,
        "failed": failed,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Flush locally pending Mongo bridge jobs into Mongo queue.")
    parser.add_argument("--project", required=True, help="Project ID (e.g. cat_pod_us)")
    parser.add_argument("--limit", type=int, default=20, help="Max pending files to flush in one run")
    args = parser.parse_args()

    try:
        report = run(project_id=str(args.project).strip(), limit=int(args.limit))
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if report.get("ok") else 1
    except Exception as e:
        print(
            json.dumps(
                {"ok": False, "project_id": str(args.project).strip(), "error": f"{type(e).__name__}: {e}"},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
