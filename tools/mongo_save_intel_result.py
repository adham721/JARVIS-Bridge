from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_engine.mongo_intel_bridge import mongo_bridge_enabled, save_intel_result_packet


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Result file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise RuntimeError(f"Invalid JSON: {type(e).__name__}: {e}") from e
    if not isinstance(payload, dict):
        raise RuntimeError("Result JSON root must be an object.")
    return payload


def run(project_id: str, result_path: Path, source: str, job_id: str) -> Dict[str, Any]:
    if not mongo_bridge_enabled():
        raise RuntimeError("Mongo bridge disabled. Set JARVIS_MONGO_BRIDGE_ENABLED=1 in .env")
    packet = _load_json(result_path)
    mongo_id = save_intel_result_packet(
        project_id,
        packet,
        source=source,
        job_id=job_id,
        meta={"result_path": str(result_path)},
    )
    return {
        "ok": True,
        "project_id": project_id,
        "mongo_packet_id": mongo_id,
        "result_path": str(result_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Save a local intel_result.json into Mongo intel_packets.")
    parser.add_argument("--project", required=True, help="Project ID (e.g. cat_pod_us)")
    parser.add_argument(
        "--result-path",
        default="intel_result.json",
        help="Path to intel_result.json (default: intel_result.json in current directory)",
    )
    parser.add_argument("--source", default="custom_gpt_manual", help="Source tag for auditing")
    parser.add_argument("--job-id", default="", help="Optional job id that produced this result")
    args = parser.parse_args()

    try:
        report = run(
            args.project,
            result_path=Path(args.result_path).expanduser().resolve(),
            source=args.source,
            job_id=args.job_id,
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
