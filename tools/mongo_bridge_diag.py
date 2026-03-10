from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_engine.mongo_intel_bridge import (
    fetch_ready_intel_packets,
    list_recent_jobs,
    mongo_ping,
    mongo_bridge_enabled,
)


def run(project_id: str, jobs_limit: int, packets_limit: int) -> Dict[str, Any]:
    if not mongo_bridge_enabled():
        raise RuntimeError("Mongo bridge disabled. Set JARVIS_MONGO_BRIDGE_ENABLED=1 in .env")

    ping = mongo_ping()
    jobs = list_recent_jobs(project_id, limit=max(1, jobs_limit))

    packets, packet_errors = fetch_ready_intel_packets(project_id, limit=max(1, packets_limit))
    packets_preview = [
        {
            "mongo_id": str(row.get("mongo_id") or ""),
            "project_id": str(row.get("project_id") or ""),
            "collection": str(row.get("collection") or ""),
            "payload_keys": sorted(list((row.get("payload") or {}).keys()))[:20],
        }
        for row in packets
    ]

    return {
        "ok": True,
        "ping": ping,
        "project_id": project_id,
        "jobs_count": len(jobs),
        "jobs_preview": jobs,
        "ready_packets_count": len(packets_preview),
        "ready_packets_preview": packets_preview,
        "packet_errors": packet_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Mongo bridge connectivity and queue state.")
    parser.add_argument("--project", required=True, help="Project ID (e.g. cat_pod_us)")
    parser.add_argument("--jobs-limit", type=int, default=5, help="How many jobs to preview")
    parser.add_argument("--packets-limit", type=int, default=5, help="How many ready packets to preview")
    args = parser.parse_args()

    try:
        report = run(args.project, jobs_limit=args.jobs_limit, packets_limit=args.packets_limit)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "project_id": args.project,
                    "error": f"{type(e).__name__}: {e}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
