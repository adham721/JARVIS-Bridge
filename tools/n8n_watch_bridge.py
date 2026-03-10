from __future__ import annotations

import argparse
import hashlib
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


def _tail_lines(text: str, max_lines: int) -> List[str]:
    rows = [line for line in (text or "").splitlines() if line.strip()]
    return rows[-max(0, int(max_lines)) :] if max_lines > 0 else []


def _sha256_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest().lower()
    except Exception:
        return ""


def _read_import_marker_hash(marker_path: Path) -> str:
    if not marker_path.exists():
        return ""
    try:
        raw = marker_path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            value = str(payload.get("sha256") or "").strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", value or ""):
                return value
    except Exception:
        pass
    first = raw.splitlines()[0].strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", first or ""):
        return first
    return ""


def _resolve_intel_inbox_root(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env_value = str((__import__("os").environ.get("JARVIS_INTEL_DIR") or "")).strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    local_default = ROOT_DIR / "data" / "intel_inbox"
    drive_default = Path("G:/My Drive/JARVIS_INTEL_INBOX")
    if drive_default.exists():
        has_drive_packets = any(drive_default.rglob("intel_result.json"))
        has_local_packets = local_default.exists() and any(local_default.rglob("intel_result.json"))
        if has_drive_packets and not has_local_packets:
            return drive_default.resolve()
        if not local_default.exists():
            return drive_default.resolve()
    return local_default.resolve()


def _resolve_openclaw_inbox_root(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env_value = str((__import__("os").environ.get("JARVIS_OPENCLAW_INBOX_DIR") or "")).strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    return ROOT_DIR / "data" / "openclaw_inbox"


def _load_project_ids(projects_dir: Path, include_disabled: bool) -> List[str]:
    sys.path.insert(0, str(ROOT_DIR))
    from jarvis_engine.profiles import load_profiles  # type: ignore

    profiles = load_profiles(projects_dir, include_disabled=include_disabled)
    return [profile.project_id for profile in profiles]


def _intel_reason_for_project(intel_root: Path, project_id: str) -> Dict[str, Any]:
    result_path = intel_root / project_id / "intel_result.json"
    marker_path = Path(str(result_path) + ".imported")
    result_hash = _sha256_file(result_path)
    marker_hash = _read_import_marker_hash(marker_path)
    changed = bool(result_hash and result_hash != marker_hash)
    return {
        "changed": changed,
        "result_path": str(result_path),
        "marker_path": str(marker_path),
        "result_hash": result_hash,
        "marker_hash": marker_hash,
    }


def _openclaw_reason_for_project(openclaw_root: Path, project_id: str) -> Dict[str, Any]:
    project_dir = openclaw_root / project_id
    pending_files: List[str] = []
    if project_dir.exists():
        for file_path in sorted(project_dir.glob("*.json")):
            marker = Path(str(file_path) + ".imported")
            if marker.exists():
                continue
            pending_files.append(str(file_path))
    return {
        "changed": bool(pending_files),
        "pending_count": len(pending_files),
        "pending_files": pending_files[:20],
    }


def _runner_command(
    python_executable: str,
    project_id: str,
    projects_dir: Path,
    data_dir: Path,
    dedup_hours: float,
) -> List[str]:
    return [
        python_executable,
        str(ROOT_DIR / "jarvis_runner.py"),
        "--projects-dir",
        str(projects_dir),
        "--data-dir",
        str(data_dir),
        "--project",
        project_id,
        "--include-disabled",
        "--dedup-hours",
        str(dedup_hours),
    ]


def _run_project(
    command: List[str],
    project_id: str,
    timeout_seconds: int,
    stdout_tail_lines: int,
    stderr_tail_lines: int,
) -> Dict[str, Any]:
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

    status = "unknown"
    summary_path = ""
    matcher = re.compile(rf"^- {re.escape(project_id)}: (?P<status>[a-zA-Z_]+)\b")
    for line in (process.stdout or "").splitlines():
        match = matcher.match(line.strip())
        if match:
            status = match.group("status").lower()
        if line.startswith("Summary: "):
            summary_path = line.split("Summary: ", 1)[1].strip()

    return {
        "project_id": project_id,
        "exit_code": int(process.returncode),
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "summary_path": summary_path,
        "stdout_tail": _tail_lines(process.stdout or "", stdout_tail_lines),
        "stderr_tail": _tail_lines(process.stderr or "", stderr_tail_lines),
    }


def _build_payload(
    mode: str,
    project_checks: List[Dict[str, Any]],
    triggered_projects: List[Dict[str, Any]],
    run_results: List[Dict[str, Any]],
    intel_root: Path,
    openclaw_root: Path,
) -> Dict[str, Any]:
    failed_runs = [row for row in run_results if int(row.get("exit_code") or 0) != 0 or str(row.get("status") or "") == "failed"]
    payload = {
        "schema_version": 1,
        "mode": mode,
        "ok": len(failed_runs) == 0,
        "generated_at": utc_now_iso(),
        "intel_inbox_root": str(intel_root),
        "openclaw_inbox_root": str(openclaw_root),
        "projects_checked": project_checks,
        "triggered_projects": triggered_projects,
        "triggered_count": len(triggered_projects),
        "run_results": run_results,
        "failed_projects": [str(row.get("project_id") or "") for row in failed_runs],
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="n8n watcher bridge for Intel/OpenClaw inboxes.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="watch-bridges",
        choices=["watch-intel-results", "watch-openclaw-inbox", "watch-bridges"],
    )
    parser.add_argument("--projects-dir", default=str(ROOT_DIR / "projects"))
    parser.add_argument("--data-dir", default=str(ROOT_DIR / "data"))
    parser.add_argument("--intel-inbox-root", default=None)
    parser.add_argument("--openclaw-inbox-root", default=None)
    parser.add_argument("--runner-python", default=sys.executable)
    parser.add_argument("--dedup-hours", type=float, default=0.0)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--stdout-tail-lines", type=int, default=20)
    parser.add_argument("--stderr-tail-lines", type=int, default=10)
    parser.add_argument("--output", default=str(ROOT_DIR / "data" / "reports" / "_summary" / "latest_watch_cycle.json"))
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--no-run", action="store_true", help="Detect changes only; do not run jarvis_runner.")
    parser.add_argument("--fail-on-run-error", action="store_true")
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    intel_root = _resolve_intel_inbox_root(args.intel_inbox_root)
    openclaw_root = _resolve_openclaw_inbox_root(args.openclaw_inbox_root)
    output_path = Path(args.output).expanduser().resolve()

    project_ids = _load_project_ids(projects_dir, include_disabled=bool(args.include_disabled))
    checks: List[Dict[str, Any]] = []
    triggered: List[Dict[str, Any]] = []
    run_results: List[Dict[str, Any]] = []

    watch_intel = args.mode in {"watch-intel-results", "watch-bridges"}
    watch_openclaw = args.mode in {"watch-openclaw-inbox", "watch-bridges"}

    for project_id in project_ids:
        row: Dict[str, Any] = {"project_id": project_id, "reasons": []}
        if watch_intel:
            intel_state = _intel_reason_for_project(intel_root, project_id)
            row["intel_result"] = intel_state
            if bool(intel_state.get("changed")):
                row["reasons"].append("intel_result_updated")
        if watch_openclaw:
            openclaw_state = _openclaw_reason_for_project(openclaw_root, project_id)
            row["openclaw"] = openclaw_state
            if bool(openclaw_state.get("changed")):
                row["reasons"].append("openclaw_pending")
        checks.append(row)
        if row["reasons"]:
            triggered.append({"project_id": project_id, "reasons": list(row["reasons"])})

    if not args.no_run:
        for row in triggered:
            project_id = str(row.get("project_id") or "")
            if not project_id:
                continue
            command = _runner_command(
                python_executable=str(args.runner_python),
                project_id=project_id,
                projects_dir=projects_dir,
                data_dir=data_dir,
                dedup_hours=float(args.dedup_hours),
            )
            result = _run_project(
                command=command,
                project_id=project_id,
                timeout_seconds=int(args.timeout_seconds),
                stdout_tail_lines=int(args.stdout_tail_lines),
                stderr_tail_lines=int(args.stderr_tail_lines),
            )
            result["trigger_reasons"] = list(row.get("reasons") or [])
            run_results.append(result)

    payload = _build_payload(
        mode=args.mode,
        project_checks=checks,
        triggered_projects=triggered,
        run_results=run_results,
        intel_root=intel_root,
        openclaw_root=openclaw_root,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.fail_on_run_error and payload.get("failed_projects"):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
