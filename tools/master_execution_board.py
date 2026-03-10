from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_file(base: Path, pattern: str) -> Optional[Path]:
    if not base.exists():
        return None
    candidates = [p for p in base.glob(pattern) if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _listify_channels(value: str) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    if ";" in raw:
        parts = raw.split(";")
    elif "," in raw:
        parts = raw.split(",")
    else:
        parts = [raw]
    out: List[str] = []
    for part in parts:
        token = str(part or "").strip().lower()
        if token:
            out.append(token)
    dedup: List[str] = []
    seen = set()
    for token in out:
        if token in seen:
            continue
        seen.add(token)
        dedup.append(token)
    return dedup


def _resolve_profiles(projects_dir: Path, include_disabled: bool) -> Dict[str, Dict[str, Any]]:
    sys.path.insert(0, str(ROOT_DIR))
    from jarvis_engine.profiles import load_profiles  # type: ignore

    profiles = load_profiles(projects_dir, include_disabled=include_disabled)
    out: Dict[str, Dict[str, Any]] = {}
    for profile in profiles:
        out[profile.project_id] = {
            "project_id": profile.project_id,
            "name": profile.name,
            "language": profile.language,
            "market": profile.market,
            "channels": list(profile.signals.platforms or []),
            "objective": (profile.brand.niche or profile.name or profile.project_id),
            "lane": "general",
            "priority_tier": "P1",
        }
    return out


def _load_projects_master(
    csv_path: Path,
    profiles_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                for raw in reader:
                    if not isinstance(raw, dict):
                        continue
                    project_id = str(raw.get("project_id") or "").strip()
                    if not project_id:
                        continue
                    profile_defaults = dict(profiles_map.get(project_id) or {})
                    channels = _listify_channels(str(raw.get("channels") or raw.get("platforms") or ""))
                    if not channels:
                        channels = list(profile_defaults.get("channels") or [])
                    rows.append(
                        {
                            "project_id": project_id,
                            "name": str(profile_defaults.get("name") or project_id),
                            "lane": str(
                                raw.get("lane")
                                or raw.get("domain")
                                or profile_defaults.get("lane")
                                or "general"
                            ).strip(),
                            "priority_tier": str(
                                raw.get("priority_tier") or profile_defaults.get("priority_tier") or "P1"
                            ).strip(),
                            "objective": str(
                                raw.get("objective")
                                or raw.get("niche")
                                or profile_defaults.get("objective")
                                or ""
                            ).strip(),
                            "channels": channels,
                            "language": str(raw.get("language") or profile_defaults.get("language") or "").strip(),
                            "market": str(raw.get("market") or profile_defaults.get("market") or "").strip(),
                        }
                    )
        except Exception:
            rows = []

    if rows:
        return rows

    fallback: List[Dict[str, Any]] = []
    for project_id in sorted(profiles_map.keys()):
        meta = dict(profiles_map.get(project_id) or {})
        fallback.append(
            {
                "project_id": project_id,
                "name": str(meta.get("name") or project_id),
                "lane": str(meta.get("lane") or "general"),
                "priority_tier": str(meta.get("priority_tier") or "P1"),
                "objective": str(meta.get("objective") or ""),
                "channels": list(meta.get("channels") or []),
                "language": str(meta.get("language") or ""),
                "market": str(meta.get("market") or ""),
            }
        )
    return fallback


def _load_capacity(path: Path, project_count: int) -> Dict[str, Any]:
    payload = _load_json(path)
    weekly = payload.get("weekly_capacity") if isinstance(payload.get("weekly_capacity"), dict) else {}
    schedule = payload.get("daily_schedule") if isinstance(payload.get("daily_schedule"), dict) else {}

    return {
        "weekly_capacity": {
            "video_outputs_total": _safe_int(weekly.get("video_outputs_total"), max(0, project_count * 6)),
            "short_videos": _safe_int(weekly.get("short_videos"), max(0, project_count * 4)),
            "long_videos": _safe_int(weekly.get("long_videos"), max(0, project_count * 2)),
            "pod_designs": _safe_int(weekly.get("pod_designs"), max(0, project_count * 20)),
            "custom_gpt_batch_runs": _safe_int(weekly.get("custom_gpt_batch_runs"), 3),
            "manual_publish_slots": _safe_int(weekly.get("manual_publish_slots"), max(0, project_count * 8)),
        },
        "daily_schedule": {
            "work_hours_target": _safe_int(schedule.get("work_hours_target"), 16),
            "custom_gpt_days": [str(x).strip() for x in list(schedule.get("custom_gpt_days") or []) if str(x).strip()],
            "production_days": [str(x).strip() for x in list(schedule.get("production_days") or []) if str(x).strip()],
        },
    }


def _today_mode(schedule: Dict[str, Any]) -> str:
    day_name = datetime.now().strftime("%A")
    custom_days = {str(x).strip().lower() for x in list(schedule.get("custom_gpt_days") or [])}
    production_days = {str(x).strip().lower() for x in list(schedule.get("production_days") or [])}
    key = day_name.strip().lower()
    if key in custom_days and key in production_days:
        return "hybrid"
    if key in custom_days:
        return "custom_gpt"
    if key in production_days:
        return "production"
    return "unassigned"


def _task_counts(con: sqlite3.Connection, project_id: str, day: str) -> Dict[str, int]:
    counts = {"todo": 0, "doing": 0, "open_total": 0, "today_open_total": 0}
    cur = con.cursor()
    cur.execute(
        """
        select status, count(*) as c
        from tasks
        where project_id = ? and status in ('todo', 'doing')
        group by status
        """,
        (project_id,),
    )
    for status, amount in cur.fetchall():
        key = str(status or "").strip().lower()
        if key in {"todo", "doing"}:
            counts[key] = int(amount or 0)
            counts["open_total"] += int(amount or 0)

    cur.execute(
        """
        select count(*)
        from tasks
        where project_id = ?
          and status in ('todo', 'doing')
          and due_date = ?
        """,
        (project_id, day),
    )
    row = cur.fetchone()
    counts["today_open_total"] = int((row[0] if row else 0) or 0)
    return counts


def _latest_run(con: sqlite3.Connection, project_id: str) -> Dict[str, Any]:
    cur = con.cursor()
    cur.execute(
        """
        select run_id, status, started_at, finished_at
        from runs
        where project_id = ?
        order by run_id desc
        limit 1
        """,
        (project_id,),
    )
    row = cur.fetchone()

    if not row:
        return {"run_id": None, "status": "missing", "started_at": "", "finished_at": ""}
    return {
        "run_id": int(row[0]) if row[0] is not None else None,
        "status": str(row[1] or "").strip().lower() or "unknown",
        "started_at": str(row[2] or ""),
        "finished_at": str(row[3] or ""),
    }


def _gate_stats(platform_report: Dict[str, Any]) -> Dict[str, Any]:
    platforms = platform_report.get("platforms")
    if not isinstance(platforms, dict):
        return {
            "passed": 0,
            "total": 0,
            "failed_platforms": [],
            "needs_reauth_platforms": [],
        }

    passed = 0
    total = 0
    failed_platforms: List[str] = []
    reauth_platforms: List[str] = []
    for platform_name, payload in platforms.items():
        row = dict(payload or {}) if isinstance(payload, dict) else {}
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
    return {
        "passed": passed,
        "total": total,
        "failed_platforms": failed_platforms,
        "needs_reauth_platforms": reauth_platforms,
    }


def _project_board_entry(
    *,
    project: Dict[str, Any],
    data_dir: Path,
    db_con: sqlite3.Connection,
    day: str,
    lane_video_count: int,
    lane_pod_count: int,
    weekly_capacity: Dict[str, Any],
    mode_today: str,
) -> Dict[str, Any]:
    project_id = str(project.get("project_id") or "").strip()
    reports_root = data_dir / "reports" / project_id
    workpacks_root = data_dir / "workpacks" / project_id

    platform_report_path = _latest_file(reports_root, "*/platform_report.json")
    workpack_path = _latest_file(workpacks_root, "*/workpack.json")

    platform_report = _load_json(platform_report_path) if platform_report_path else {}
    workpack = _load_json(workpack_path) if workpack_path else {}
    gates = _gate_stats(platform_report)
    run_meta = _latest_run(db_con, project_id)
    task_meta = _task_counts(db_con, project_id, day=day)

    candidates = workpack.get("content_candidates") if isinstance(workpack.get("content_candidates"), list) else []
    top_candidate = dict(candidates[0]) if candidates else {}
    top_idea_title = str(top_candidate.get("title") or "").strip()
    top_idea_hook = str(top_candidate.get("hook") or "").strip()

    channels = list(project.get("channels") or [])
    lane = str(project.get("lane") or "general").strip().lower()
    is_video_lane = lane in {"video", "video_pod", "video_content", "content"} or any(
        c in {"youtube", "tiktok", "instagram", "facebook"} for c in channels
    )
    is_pod_lane = lane in {"pod", "video_pod", "services_stock"} or any(
        c in {"etsy", "amazon", "redbubble", "kdp"} for c in channels
    )

    short_share = 0
    long_share = 0
    pod_share = 0
    if is_video_lane and lane_video_count > 0:
        short_share = max(0, int(weekly_capacity.get("short_videos", 0)) // lane_video_count)
        long_share = max(0, int(weekly_capacity.get("long_videos", 0)) // lane_video_count)
    if is_pod_lane and lane_pod_count > 0:
        pod_share = max(0, int(weekly_capacity.get("pod_designs", 0)) // lane_pod_count)

    if mode_today == "custom_gpt":
        next_action = "run_custom_gpt_and_import_intel"
    elif mode_today == "production":
        next_action = "produce_assets_and_schedule_publish"
    elif mode_today == "hybrid":
        next_action = "custom_gpt_then_production"
    else:
        next_action = "follow_backlog_by_priority"

    return {
        "project_id": project_id,
        "name": str(project.get("name") or project_id),
        "lane": str(project.get("lane") or "general"),
        "priority_tier": str(project.get("priority_tier") or "P1"),
        "objective": str(project.get("objective") or ""),
        "channels": channels,
        "language": str(project.get("language") or ""),
        "market": str(project.get("market") or ""),
        "latest_run": run_meta,
        "gates": gates,
        "tasks": task_meta,
        "top_idea_title": top_idea_title,
        "top_idea_hook": top_idea_hook,
        "workpack_path": str(workpack_path) if workpack_path else "",
        "platform_report_path": str(platform_report_path) if platform_report_path else "",
        "weekly_target_share": {
            "short_videos": short_share,
            "long_videos": long_share,
            "pod_designs": pod_share,
        },
        "next_action": next_action,
    }


def _render_md(payload: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Master Execution Board - {payload.get('day', '')}")
    lines.append("")
    lines.append(f"- generated_at: `{payload.get('generated_at', '')}`")
    lines.append(f"- today_mode: `{payload.get('today_mode', 'unassigned')}`")
    lines.append(f"- projects: `{payload.get('project_count', 0)}`")
    lines.append(f"- open_tasks_total: `{payload.get('open_tasks_total', 0)}`")
    lines.append("")

    capacity = payload.get("capacity") if isinstance(payload.get("capacity"), dict) else {}
    weekly = capacity.get("weekly_capacity") if isinstance(capacity.get("weekly_capacity"), dict) else {}
    schedule = capacity.get("daily_schedule") if isinstance(capacity.get("daily_schedule"), dict) else {}
    lines.append("## Capacity")
    lines.append(
        "- weekly: "
        f"short={weekly.get('short_videos', 0)} | "
        f"long={weekly.get('long_videos', 0)} | "
        f"pod={weekly.get('pod_designs', 0)} | "
        f"gpt_batches={weekly.get('custom_gpt_batch_runs', 0)}"
    )
    lines.append(
        "- schedule: "
        f"custom_gpt_days={', '.join(list(schedule.get('custom_gpt_days') or [])) or 'n/a'} | "
        f"production_days={', '.join(list(schedule.get('production_days') or [])) or 'n/a'}"
    )
    lines.append("")

    for row in list(payload.get("projects") or []):
        project_id = str(row.get("project_id") or "")
        status = str((row.get("latest_run") or {}).get("status") or "unknown")
        gates = row.get("gates") if isinstance(row.get("gates"), dict) else {}
        tasks = row.get("tasks") if isinstance(row.get("tasks"), dict) else {}
        share = row.get("weekly_target_share") if isinstance(row.get("weekly_target_share"), dict) else {}
        top = str(row.get("top_idea_title") or "").strip()
        top_short = (top[:160] + "...") if len(top) > 163 else (top or "n/a")

        lines.append(f"## {project_id} ({status})")
        lines.append(
            f"- lane: `{row.get('lane', 'general')}` | priority: `{row.get('priority_tier', 'P1')}` | "
            f"next_action: `{row.get('next_action', '')}`"
        )
        lines.append(
            f"- gates: `{gates.get('passed', 0)}/{gates.get('total', 0)}` | "
            f"failed={','.join(list(gates.get('failed_platforms') or [])) or '-'} | "
            f"reauth={','.join(list(gates.get('needs_reauth_platforms') or [])) or '-'}"
        )
        lines.append(
            f"- tasks: open_total=`{tasks.get('open_total', 0)}` | today_open=`{tasks.get('today_open_total', 0)}`"
        )
        lines.append(
            f"- weekly_share: short=`{share.get('short_videos', 0)}` long=`{share.get('long_videos', 0)}` pod=`{share.get('pod_designs', 0)}`"
        )
        lines.append(f"- top_idea: {top_short}")
        if row.get("workpack_path"):
            lines.append(f"- workpack: `{row.get('workpack_path')}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a master execution board across all projects.")
    parser.add_argument("--projects-master", default=str(ROOT_DIR / "projects_master.csv"))
    parser.add_argument("--capacity", default=str(ROOT_DIR / "data" / "runtime" / "capacity.json"))
    parser.add_argument("--projects-dir", default=str(ROOT_DIR / "projects"))
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"))
    parser.add_argument("--db-path", default=str(ROOT_DIR / "data" / "jarvis_ops.db"))
    parser.add_argument("--output-json", default=str(ROOT_DIR / "data" / "reports" / "_summary" / "full_focus_plan_latest.json"))
    parser.add_argument("--output-md", default=str(ROOT_DIR / "data" / "reports" / "_summary" / "full_focus_plan_latest.md"))
    args = parser.parse_args()

    projects_master = Path(args.projects_master).expanduser().resolve()
    capacity_path = Path(args.capacity).expanduser().resolve()
    projects_dir = Path(args.projects_dir).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    db_path = Path(args.db_path).expanduser().resolve()
    output_json = Path(args.output_json).expanduser().resolve()
    output_md = Path(args.output_md).expanduser().resolve()

    profiles_map = _resolve_profiles(projects_dir, include_disabled=bool(args.include_disabled))
    projects = _load_projects_master(projects_master, profiles_map)
    capacity = _load_capacity(capacity_path, project_count=len(projects))
    day = datetime.now().strftime("%Y-%m-%d")
    mode_today = _today_mode(capacity.get("daily_schedule") if isinstance(capacity.get("daily_schedule"), dict) else {})

    lane_video_count = 0
    lane_pod_count = 0
    for row in projects:
        channels = list(row.get("channels") or [])
        lane = str(row.get("lane") or "").strip().lower()
        if lane in {"video", "video_pod", "video_content", "content"} or any(
            c in {"youtube", "tiktok", "instagram", "facebook"} for c in channels
        ):
            lane_video_count += 1
        if lane in {"pod", "video_pod", "services_stock"} or any(
            c in {"etsy", "amazon", "redbubble", "kdp"} for c in channels
        ):
            lane_pod_count += 1

    project_rows: List[Dict[str, Any]] = []
    if db_path.exists():
        con = sqlite3.connect(str(db_path))
    else:
        con = sqlite3.connect(":memory:")
    try:
        for project in projects:
            project_rows.append(
                _project_board_entry(
                    project=project,
                    data_dir=data_dir,
                    db_con=con,
                    day=day,
                    lane_video_count=max(0, lane_video_count),
                    lane_pod_count=max(0, lane_pod_count),
                    weekly_capacity=capacity.get("weekly_capacity") if isinstance(capacity.get("weekly_capacity"), dict) else {},
                    mode_today=mode_today,
                )
            )
    finally:
        con.close()

    open_tasks_total = sum(int((row.get("tasks") or {}).get("open_total", 0) or 0) for row in project_rows)
    today_open_tasks_total = sum(int((row.get("tasks") or {}).get("today_open_total", 0) or 0) for row in project_rows)
    failed_projects = [
        str(row.get("project_id") or "")
        for row in project_rows
        if str((row.get("latest_run") or {}).get("status") or "") in {"failed", "degraded"}
    ]
    reauth_projects = [
        str(row.get("project_id") or "")
        for row in project_rows
        if list((row.get("gates") or {}).get("needs_reauth_platforms") or [])
    ]

    payload = {
        "schema_version": 1,
        "mode": "master_execution_board",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "day": day,
        "today_mode": mode_today,
        "project_count": len(project_rows),
        "open_tasks_total": open_tasks_total,
        "today_open_tasks_total": today_open_tasks_total,
        "failed_projects": failed_projects,
        "projects_with_reauth": reauth_projects,
        "capacity": capacity,
        "projects": project_rows,
        "ok": len(failed_projects) == 0,
        "summary_text": (
            f"Execution board ({day}) mode={mode_today} "
            f"projects={len(project_rows)} open_tasks={open_tasks_total} "
            f"failed={len(failed_projects)} reauth={len(reauth_projects)}"
        ),
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(_render_md(payload), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
