from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_project_ids(args: argparse.Namespace) -> List[str]:
    explicit = [str(x).strip() for x in list(args.projects or []) if str(x).strip()]
    if explicit:
        return explicit

    projects_dir = Path(args.projects_dir).expanduser().resolve()
    sys.path.insert(0, str(ROOT_DIR))
    from jarvis_engine.profiles import load_profiles  # type: ignore

    profiles = load_profiles(projects_dir, include_disabled=bool(args.include_disabled))
    return [str(p.project_id).strip() for p in profiles if str(p.project_id).strip()]


def _latest_delta_path(data_dir: Path, project_id: str) -> Optional[Path]:
    base = data_dir / "reports" / project_id
    if not base.exists():
        return None
    candidates = list(base.glob("*/cycle_delta_latest.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0.0)


def _project_entry(project_id: str, delta_path: Optional[Path], cycle_start_utc: Optional[datetime]) -> Dict[str, Any]:
    if not delta_path:
        return {
            "project_id": project_id,
            "status": "missing",
            "delta_path": "",
            "before_run_id": None,
            "after_run_id": None,
            "changed_platform_count": 0,
            "changed_platforms": [],
            "coverage_block_rate": {"before": None, "after": None, "delta": None},
            "is_from_current_cycle": False,
        }

    payload = _load_json(delta_path)
    before_run_id = payload.get("before_run_id")
    after_run_id = payload.get("after_run_id")
    platform_changes = payload.get("platform_changes") if isinstance(payload.get("platform_changes"), dict) else {}
    changed_platforms = [str(k) for k in platform_changes.keys()]
    changed_count = int(payload.get("changed_platform_count") or len(changed_platforms))
    coverage = payload.get("coverage_block_rate") if isinstance(payload.get("coverage_block_rate"), dict) else {}

    after_run = payload.get("after_run") if isinstance(payload.get("after_run"), dict) else {}
    after_started = _parse_iso(after_run.get("started_at"))
    after_finished = _parse_iso(after_run.get("finished_at"))
    marker = after_finished or after_started
    is_from_current_cycle = False
    if cycle_start_utc and marker:
        is_from_current_cycle = marker >= cycle_start_utc
    elif marker:
        is_from_current_cycle = True

    status = "ok"
    if not is_from_current_cycle:
        status = "stale"

    return {
        "project_id": project_id,
        "status": status,
        "delta_path": str(delta_path),
        "before_run_id": before_run_id,
        "after_run_id": after_run_id,
        "changed_platform_count": changed_count,
        "changed_platforms": changed_platforms,
        "coverage_block_rate": {
            "before": coverage.get("before"),
            "after": coverage.get("after"),
            "delta": coverage.get("delta"),
        },
        "is_from_current_cycle": bool(is_from_current_cycle),
    }


def _summary_text(entries: List[Dict[str, Any]], *, cycle_number: int) -> str:
    lines = [f"Master Cycle Delta Summary (cycle={cycle_number})", f"projects={len(entries)}"]
    for row in entries:
        project_id = str(row.get("project_id") or "")
        status = str(row.get("status") or "unknown")
        changed = int(row.get("changed_platform_count") or 0)
        coverage = dict(row.get("coverage_block_rate") or {})
        b = coverage.get("before")
        a = coverage.get("after")
        try:
            cov = f"{float(b):.4f}->{float(a):.4f}"
        except Exception:
            cov = "n/a"
        lines.append(f"- {project_id}: status={status} changed_platforms={changed} coverage={cov}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate per-project cycle delta reports into a master cycle summary.")
    parser.add_argument("--projects", nargs="*", default=[], help="Optional explicit project ids.")
    parser.add_argument("--projects-dir", default=str(ROOT_DIR / "projects"))
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"))
    parser.add_argument("--cycle-number", type=int, default=0)
    parser.add_argument("--cycle-start-utc", default="", help="Optional ISO timestamp for current master cycle start.")
    parser.add_argument("--output", default="", help="Optional explicit output path.")
    parser.add_argument("--latest-output", default="", help="Optional explicit latest-output path.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    cycle_number = max(0, int(args.cycle_number or 0))
    cycle_start_utc = _parse_iso(args.cycle_start_utc)

    project_ids = _resolve_project_ids(args)
    entries: List[Dict[str, Any]] = []
    for project_id in project_ids:
        entries.append(_project_entry(project_id, _latest_delta_path(data_dir, project_id), cycle_start_utc))

    missing_projects = [str(r.get("project_id") or "") for r in entries if str(r.get("status") or "") == "missing"]
    stale_projects = [str(r.get("project_id") or "") for r in entries if str(r.get("status") or "") == "stale"]
    changed_projects = [str(r.get("project_id") or "") for r in entries if int(r.get("changed_platform_count") or 0) > 0]
    changed_platform_total = sum(int(r.get("changed_platform_count") or 0) for r in entries)

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "mode": "master-cycle-delta-summary",
        "generated_at": _utc_now_iso(),
        "cycle_number": cycle_number,
        "cycle_start_utc": args.cycle_start_utc or "",
        "project_count": len(entries),
        "missing_project_count": len(missing_projects),
        "stale_project_count": len(stale_projects),
        "changed_project_count": len(changed_projects),
        "changed_platform_total": changed_platform_total,
        "missing_projects": missing_projects,
        "stale_projects": stale_projects,
        "changed_projects": changed_projects,
        "projects": entries,
        "ok": len(missing_projects) == 0 and len(stale_projects) == 0,
        "summary_text": _summary_text(entries, cycle_number=cycle_number),
    }

    date_token = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_dir = data_dir / "reports" / "_summary"
    dated_dir = summary_dir / date_token
    dated_dir.mkdir(parents=True, exist_ok=True)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        suffix = f"cycle_{cycle_number}" if cycle_number > 0 else datetime.now(timezone.utc).strftime("%H%M%S")
        output_path = dated_dir / f"cycle_delta_master_{suffix}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.latest_output:
        latest_path = Path(args.latest_output).expanduser().resolve()
    else:
        latest_path = summary_dir / "cycle_delta_master_latest.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    payload["output_path"] = str(output_path)
    payload["latest_output_path"] = str(latest_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

