from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path | None) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_file(base: Path, pattern: str) -> Path | None:
    if not base.exists():
        return None
    candidates = list(base.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime if p.exists() else 0.0)


def _gate_stats(platform_report: Dict[str, Any]) -> Tuple[int, int, List[str], List[str]]:
    platforms = platform_report.get("platforms")
    if not isinstance(platforms, dict):
        return 0, 0, [], []

    passed = 0
    total = 0
    failed_platforms: List[str] = []
    reauth_platforms: List[str] = []
    for platform_name, row_raw in platforms.items():
        row = dict(row_raw or {}) if isinstance(row_raw, dict) else {}
        gate = str(
            row.get("effective_quality_gate")
            if row.get("effective_quality_gate") is not None
            else row.get("quality_gate") or ""
        ).strip().lower()
        total += 1
        if gate == "pass":
            passed += 1
        else:
            failed_platforms.append(str(platform_name))
        if bool(row.get("needs_reauth", False)):
            reauth_platforms.append(str(platform_name))
    return passed, total, failed_platforms, reauth_platforms


def _latest_run_meta(db_path: Path, project_id: str) -> Dict[str, Any]:
    if not db_path.exists():
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute(
            """
            select run_id, status, started_at, finished_at, summary_json
            from runs
            where project_id = ? and status != 'running'
            order by run_id desc
            limit 1
            """,
            (project_id,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                select run_id, status, started_at, finished_at, summary_json
                from runs
                where project_id = ?
                order by run_id desc
                limit 1
                """,
                (project_id,),
            )
            row = cur.fetchone()
        if not row:
            return {}
        run_id, status, started_at, finished_at, summary_json = row
        try:
            summary = json.loads(summary_json or "{}")
        except Exception:
            summary = {}
        return {
            "run_id": int(run_id),
            "status": str(status or "").strip().lower(),
            "started_at": str(started_at or ""),
            "finished_at": str(finished_at or ""),
            "auto_recovery": dict(summary.get("auto_recovery") or {})
            if isinstance(summary.get("auto_recovery"), dict)
            else {},
        }
    finally:
        con.close()


def _project_snapshot(
    *,
    data_dir: Path,
    db_path: Path,
    project_id: str,
) -> Dict[str, Any]:
    report_root = data_dir / "reports" / project_id
    workpack_root = data_dir / "workpacks" / project_id
    platform_report_path = _latest_file(report_root, "*/platform_report.json")
    precision_report_path = _latest_file(report_root, "*/precision_report.json")
    workpack_path = _latest_file(workpack_root, "*/workpack.json")

    platform_report = _load_json(platform_report_path)
    precision_report = _load_json(precision_report_path)
    workpack = _load_json(workpack_path)
    run_meta = _latest_run_meta(db_path, project_id)

    gate_passed, gate_total, gate_failed_platforms, reauth_platforms = _gate_stats(platform_report)
    content_candidates = (
        workpack.get("content_candidates") if isinstance(workpack.get("content_candidates"), list) else []
    )
    top_candidate = dict(content_candidates[0]) if content_candidates else {}
    precision_summary = (
        dict(precision_report.get("summary") or {})
        if isinstance(precision_report.get("summary"), dict)
        else {}
    )

    return {
        "project_id": project_id,
        "platform_report_path": str(platform_report_path) if platform_report_path else "",
        "precision_report_path": str(precision_report_path) if precision_report_path else "",
        "workpack_path": str(workpack_path) if workpack_path else "",
        "gate_passed": int(gate_passed),
        "gate_total": int(gate_total),
        "gate_failed_platforms": gate_failed_platforms,
        "reauth_platforms": reauth_platforms,
        "top_idea_title": str(top_candidate.get("title") or "").strip(),
        "top_idea_hook": str(top_candidate.get("hook") or "").strip(),
        "precision_summary": precision_summary,
        "auto_recovery": dict(run_meta.get("auto_recovery") or {}),
        "latest_run": {
            "run_id": run_meta.get("run_id"),
            "status": run_meta.get("status") or "",
            "started_at": run_meta.get("started_at") or "",
            "finished_at": run_meta.get("finished_at") or "",
        },
    }


def _summary_text(rows: List[Dict[str, Any]]) -> str:
    lines = ["JARVIS Daily Health Snapshot", f"projects={len(rows)}"]
    for row in rows:
        project_id = str(row.get("project_id") or "")
        gate_passed = int(row.get("gate_passed") or 0)
        gate_total = int(row.get("gate_total") or 0)
        top = str(row.get("top_idea_title") or "")
        top_short = (top[:70] + "...") if len(top) > 73 else (top or "n/a")
        precision = dict(row.get("precision_summary") or {})
        if "content_candidates_in_topic_ratio" in precision:
            in_topic_ratio = float(precision.get("content_candidates_in_topic_ratio") or 0.0)
            in_topic_label = f"{in_topic_ratio:.2f}"
        else:
            in_topic_label = "n/a"
        auto = dict(row.get("auto_recovery") or {})
        attempted = bool(auto.get("attempted", False))
        lines.append(
            f"- {project_id}: gates={gate_passed}/{gate_total} | in_topic={in_topic_label} | "
            f"auto_recovery_attempted={str(attempted).lower()} | top={top_short}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create daily health snapshot (gates + precision + auto_recovery).")
    parser.add_argument("--projects-dir", default=str(ROOT_DIR / "projects"))
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"))
    parser.add_argument("--db-path", default=str(ROOT_DIR / "data" / "jarvis_ops.db"))
    parser.add_argument("--project", default="", help="Optional single project_id")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--output", default="", help="Optional output path")
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()

    import sys

    sys.path.insert(0, str(ROOT_DIR))
    from jarvis_engine.profiles import load_profiles  # type: ignore

    profiles = load_profiles(projects_dir, include_disabled=bool(args.include_disabled or args.project))
    if args.project:
        profiles = [p for p in profiles if p.project_id == args.project]

    rows: List[Dict[str, Any]] = []
    for profile in profiles:
        rows.append(
            _project_snapshot(
                data_dir=data_dir,
                db_path=db_path,
                project_id=profile.project_id,
            )
        )

    failed_projects = [
        str(r.get("project_id") or "")
        for r in rows
        if int(r.get("gate_total") or 0) > 0 and int(r.get("gate_passed") or 0) < int(r.get("gate_total") or 0)
    ]
    projects_with_reauth = [
        str(r.get("project_id") or "")
        for r in rows
        if bool(r.get("reauth_platforms"))
    ]
    projects_off_topic_top = [
        str(r.get("project_id") or "")
        for r in rows
        if isinstance(r.get("precision_summary"), dict)
        and "top_candidate_in_topic" in dict(r.get("precision_summary") or {})
        and bool((dict(r.get("precision_summary") or {})).get("top_candidate_in_topic", False)) is False
    ]

    payload = {
        "schema_version": 1,
        "mode": "daily-health-snapshot",
        "ok": not failed_projects and not projects_with_reauth and not projects_off_topic_top,
        "created_at": _utc_now_iso(),
        "project_count": len(rows),
        "failed_projects": failed_projects,
        "projects_with_reauth": projects_with_reauth,
        "projects_off_topic_top": projects_off_topic_top,
        "projects": rows,
        "summary_text": _summary_text(rows),
    }

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d")
        output_path = ROOT_DIR / "cache" / f"daily_health_snapshot_{stamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
