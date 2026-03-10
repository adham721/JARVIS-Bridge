from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_engine.collectors.manual_intel import collect_manual_intel, mark_manual_intel_imported
from jarvis_engine.intel_bridge import tasks_from_manual_intel
from jarvis_engine.profiles import load_profile
from jarvis_engine.storage import SQLiteStore
from jarvis_engine.utils import CollectorError


def run(project_id: str, projects_dir: Path, data_dir: Path) -> Dict[str, Any]:
    profile_path = projects_dir / f"{project_id}.toml"
    if not profile_path.exists():
        raise RuntimeError(f"project profile not found: {profile_path}")

    profile = load_profile(profile_path)
    day = datetime.now(timezone.utc).date().isoformat()
    db_path = data_dir / "jarvis_ops.db"

    store = SQLiteStore(db_path)
    store.upsert_project(profile.project_id, profile.name, profile.to_json())

    warnings: list[str] = []
    try:
        items = collect_manual_intel(profile)
    except CollectorError as e:
        msg = str(e)
        items = list(getattr(e, "items", []) or [])
        warnings.append(msg)
        # Keep automation running when Mongo polling fails but no local packets exist.
        if not items and "mongo" not in msg.lower():
            raise
    tasks = tasks_from_manual_intel(profile, items, run_id=None, day=day)
    tasks_inserted = store.save_tasks(tasks)
    marked, mark_errors = mark_manual_intel_imported(items)

    return {
        "ok": True,
        "project_id": profile.project_id,
        "profile_path": str(profile_path),
        "db_path": str(db_path),
        "manual_items": len(items),
        "tasks_generated": len(tasks),
        "tasks_inserted": int(tasks_inserted),
        "packets_marked_imported": int(marked),
        "mark_errors": mark_errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest manual intel packets once (file inbox + Mongo packets) into tasks and mark imported."
    )
    parser.add_argument("--project", required=True, help="Project ID (e.g. cat_pod_us)")
    parser.add_argument("--projects-dir", default=str(REPO_ROOT / "projects"), help="Path to project profiles")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"), help="Path to data dir")
    args = parser.parse_args()

    try:
        report = run(
            project_id=str(args.project).strip(),
            projects_dir=Path(args.projects_dir).expanduser().resolve(),
            data_dir=Path(args.data_dir).expanduser().resolve(),
        )
        print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
        return 0
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
