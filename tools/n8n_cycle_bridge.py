from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tail_lines(value: str, max_lines: int) -> List[str]:
    lines = [line for line in (value or "").splitlines() if line.strip()]
    if max_lines <= 0:
        return []
    return lines[-max_lines:]


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _find_latest(path_pattern: str) -> Path | None:
    candidates = list(ROOT_DIR.glob(path_pattern))
    if not candidates:
        return None
    return max(candidates, key=lambda file_path: file_path.stat().st_mtime)


def _parse_statuses(stdout_text: str) -> Dict[str, str]:
    project_status: Dict[str, str] = {}
    matcher = re.compile(r"^- (?P<project>[a-zA-Z0-9_\-]+): (?P<status>[a-zA-Z_]+)\b")
    for line in (stdout_text or "").splitlines():
        match = matcher.match(line.strip())
        if not match:
            continue
        project_status[match.group("project")] = match.group("status").lower()
    return project_status


def _load_project_ids(projects_dir: Path, include_disabled: bool, project: str | None) -> List[str]:
    sys.path.insert(0, str(ROOT_DIR))
    from jarvis_engine.profiles import load_profiles  # type: ignore

    profiles = load_profiles(projects_dir, include_disabled=include_disabled or bool(project))
    if project:
        return [profile.project_id for profile in profiles if profile.project_id == project]
    return [profile.project_id for profile in profiles]


def _gate_stats(platform_report: Dict[str, Any]) -> Tuple[int, int, List[str], List[str]]:
    platforms = platform_report.get("platforms")
    if not isinstance(platforms, dict):
        return 0, 0, [], []

    passed = 0
    total = 0
    failed_platforms: List[str] = []
    reauth_platforms: List[str] = []

    for platform_name, metadata_raw in platforms.items():
        metadata = dict(metadata_raw or {}) if isinstance(metadata_raw, dict) else {}
        gate_value = str(
            metadata.get("effective_quality_gate")
            if metadata.get("effective_quality_gate") is not None
            else metadata.get("quality_gate") or ""
        ).strip().lower()
        total += 1
        if gate_value == "pass":
            passed += 1
        else:
            failed_platforms.append(str(platform_name))
        if bool(metadata.get("needs_reauth", False)):
            reauth_platforms.append(str(platform_name))
    return passed, total, failed_platforms, reauth_platforms


def _project_snapshot(data_dir: Path, project_id: str) -> Dict[str, Any]:
    project_report_path = _find_latest(f"{data_dir.relative_to(ROOT_DIR).as_posix()}/reports/{project_id}/*/platform_report.json")
    project_workpack_path = _find_latest(f"{data_dir.relative_to(ROOT_DIR).as_posix()}/workpacks/{project_id}/*/workpack.json")

    platform_report = _load_json(project_report_path) if project_report_path else {}
    workpack = _load_json(project_workpack_path) if project_workpack_path else {}

    passed_gates, total_gates, failed_platforms, reauth_platforms = _gate_stats(platform_report)
    content_candidates = workpack.get("content_candidates") if isinstance(workpack.get("content_candidates"), list) else []
    first_candidate = dict(content_candidates[0]) if content_candidates else {}
    required_manual_actions = (
        workpack.get("required_manual_actions")
        if isinstance(workpack.get("required_manual_actions"), list)
        else []
    )

    return {
        "project_id": project_id,
        "platform_report_path": str(project_report_path) if project_report_path else "",
        "workpack_path": str(project_workpack_path) if project_workpack_path else "",
        "gate_passed": passed_gates,
        "gate_total": total_gates,
        "gate_failed_platforms": failed_platforms,
        "reauth_platforms": reauth_platforms,
        "top_idea_title": str(first_candidate.get("title") or "").strip(),
        "top_idea_hook": str(first_candidate.get("hook") or "").strip(),
        "next_action": str(required_manual_actions[0] if required_manual_actions else "").strip(),
    }


def _build_runner_command(
    python_executable: str,
    data_dir: Path,
    projects_dir: Path,
    dedup_hours: float,
    include_disabled: bool,
    project: str | None,
) -> List[str]:
    command: List[str] = [
        python_executable,
        str(ROOT_DIR / "jarvis_runner.py"),
        "--data-dir",
        str(data_dir),
        "--projects-dir",
        str(projects_dir),
        "--dedup-hours",
        str(dedup_hours),
    ]
    if include_disabled or project:
        command.append("--include-disabled")
    if project:
        command.extend(["--project", project])
    return command


def _run_runner(command: List[str], timeout_seconds: int) -> Dict[str, Any]:
    started_at = utc_now_iso()
    process = subprocess.run(
        command,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(60, int(timeout_seconds)),
    )
    finished_at = utc_now_iso()
    return {
        "exit_code": int(process.returncode),
        "stdout": process.stdout or "",
        "stderr": process.stderr or "",
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _render_summary_text(project_rows: List[Dict[str, Any]], mode: str) -> str:
    lines: List[str] = []
    lines.append(f"JARVIS cycle ({mode})")
    lines.append(f"projects={len(project_rows)}")
    for row in project_rows:
        project_id = str(row.get("project_id") or "")
        status = str(row.get("status") or "unknown")
        gate_passed = int(row.get("gate_passed") or 0)
        gate_total = int(row.get("gate_total") or 0)
        title = str(row.get("top_idea_title") or "")
        title_short = (title[:70] + "...") if len(title) > 73 else title
        lines.append(f"- {project_id}: {status} | gates={gate_passed}/{gate_total} | top={title_short or 'n/a'}")
    return "\n".join(lines).strip()


def _compose_payload(
    mode: str,
    runner_result: Dict[str, Any],
    project_statuses: Dict[str, str],
    data_dir: Path,
    project_ids: List[str],
    stdout_tail_lines: int,
    stderr_tail_lines: int,
) -> Dict[str, Any]:
    all_project_ids = sorted(set(project_ids) | set(project_statuses.keys()))
    project_rows: List[Dict[str, Any]] = []
    for project_id in all_project_ids:
        row = _project_snapshot(data_dir, project_id)
        row["status"] = project_statuses.get(project_id, "unknown")
        project_rows.append(row)

    failed_projects = [
        row["project_id"]
        for row in project_rows
        if str(row.get("status") or "").lower() == "failed"
        or (int(row.get("gate_total") or 0) > 0 and int(row.get("gate_passed") or 0) < int(row.get("gate_total") or 0))
    ]

    payload = {
        "schema_version": 1,
        "mode": mode,
        "ok": int(runner_result.get("exit_code", 0)) == 0 and not failed_projects,
        "runner_exit_code": int(runner_result.get("exit_code", 0)),
        "started_at": runner_result.get("started_at"),
        "finished_at": runner_result.get("finished_at"),
        "project_count": len(project_rows),
        "failed_projects": failed_projects,
        "projects": project_rows,
        "summary_text": _render_summary_text(project_rows, mode=mode),
        "stdout_tail": _tail_lines(str(runner_result.get("stdout") or ""), stdout_tail_lines),
        "stderr_tail": _tail_lines(str(runner_result.get("stderr") or ""), stderr_tail_lines),
    }
    return payload


def _read_latest_report_state(data_dir: Path) -> Dict[str, Any]:
    latest_path = data_dir / "reports" / "_summary" / "latest.md"
    content = ""
    if latest_path.exists():
        content = latest_path.read_text(encoding="utf-8")
    return {
        "schema_version": 1,
        "mode": "summarize-latest",
        "ok": bool(content.strip()),
        "runner_exit_code": 0,
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "project_count": 0,
        "failed_projects": [],
        "projects": [],
        "summary_text": content.strip()[:3900] if content else "No latest summary file found.",
        "stdout_tail": [],
        "stderr_tail": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="n8n bridge for JARVIS cycle run + structured summary.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="run-and-summarize",
        choices=["run-and-summarize", "summarize-latest"],
    )
    parser.add_argument("--project", default=None, help="Run and summarize a single project_id.")
    parser.add_argument("--projects-dir", default=str(ROOT_DIR / "projects"))
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"))
    parser.add_argument("--dedup-hours", type=float, default=24.0)
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--stdout-tail-lines", type=int, default=40)
    parser.add_argument("--stderr-tail-lines", type=int, default=20)
    parser.add_argument("--output", default=str(ROOT_DIR / "data" / "reports" / "_summary" / "latest_cycle.json"))
    parser.add_argument("--runner-python", default=sys.executable)
    parser.add_argument("--fail-on-project-failure", action="store_true")
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if args.mode == "summarize-latest":
        payload = _read_latest_report_state(data_dir)
    else:
        project_ids = _load_project_ids(projects_dir, include_disabled=bool(args.include_disabled), project=args.project)
        if args.project and not project_ids:
            payload = {
                "schema_version": 1,
                "mode": args.mode,
                "ok": False,
                "runner_exit_code": 2,
                "started_at": utc_now_iso(),
                "finished_at": utc_now_iso(),
                "project_count": 0,
                "failed_projects": [args.project],
                "projects": [],
                "summary_text": f"Project not found or disabled: {args.project}",
                "stdout_tail": [],
                "stderr_tail": [],
            }
        else:
            command = _build_runner_command(
                python_executable=str(args.runner_python),
                data_dir=data_dir,
                projects_dir=projects_dir,
                dedup_hours=float(args.dedup_hours),
                include_disabled=bool(args.include_disabled),
                project=args.project,
            )
            runner_result = _run_runner(command=command, timeout_seconds=int(args.timeout_seconds))
            statuses = _parse_statuses(str(runner_result.get("stdout") or ""))
            payload = _compose_payload(
                mode=args.mode,
                runner_result=runner_result,
                project_statuses=statuses,
                data_dir=data_dir,
                project_ids=project_ids,
                stdout_tail_lines=int(args.stdout_tail_lines),
                stderr_tail_lines=int(args.stderr_tail_lines),
            )
            payload["runner_command"] = command

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if payload.get("runner_exit_code", 0) != 0:
        raise SystemExit(int(payload.get("runner_exit_code", 1)))
    if args.fail_on_project_failure and payload.get("failed_projects"):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
