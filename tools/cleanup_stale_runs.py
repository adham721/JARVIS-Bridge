from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parent.parent


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
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


def _safe_summary(payload: str) -> Dict[str, Any]:
    try:
        obj = json.loads(payload or "{}")
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark stale running rows in runs table as failed.")
    parser.add_argument("--db-path", default=str(ROOT_DIR / "data" / "jarvis_ops.db"))
    parser.add_argument("--older-than-minutes", type=int, default=180)
    parser.add_argument("--project", default="", help="Optional single project_id filter")
    parser.add_argument("--tag", default="stale_running_cleanup")
    parser.add_argument(
        "--reason",
        default="Marked failed by stale-running cleanup",
        help="Error text written into summary_json.error",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(cleanup_duplicates=True)
    parser.add_argument(
        "--cleanup-duplicates",
        dest="cleanup_duplicates",
        action="store_true",
        help="Mark older duplicate running rows for same project as failed (keeps newest run).",
    )
    parser.add_argument(
        "--no-cleanup-duplicates",
        dest="cleanup_duplicates",
        action="store_false",
        help="Disable duplicate-running cleanup.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser().resolve()
    now = _utc_now()
    threshold_minutes = max(1, int(args.older_than_minutes or 180))
    cutoff = now - timedelta(minutes=threshold_minutes)
    project_filter = str(args.project or "").strip()

    payload: Dict[str, Any] = {
        "ok": True,
        "db_path": str(db_path),
        "project": project_filter,
        "older_than_minutes": threshold_minutes,
        "cutoff_utc": cutoff.isoformat(),
        "dry_run": bool(args.dry_run),
        "cleanup_duplicates": bool(args.cleanup_duplicates),
        "matched_count": 0,
        "updated_count": 0,
        "updated_runs": [],
    }

    if not db_path.exists():
        payload["ok"] = False
        payload["error"] = f"DB not found: {db_path}"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        if project_filter:
            rows = cur.execute(
                """
                select run_id, project_id, started_at, summary_json
                from runs
                where status='running' and project_id=?
                order by run_id asc
                """,
                (project_filter,),
            ).fetchall()
        else:
            rows = cur.execute(
                """
                select run_id, project_id, started_at, summary_json
                from runs
                where status='running'
                order by run_id asc
                """
            ).fetchall()

        matched: List[sqlite3.Row] = []
        duplicate_rows: List[sqlite3.Row] = []
        for row in rows:
            started_at = _parse_iso(str(row["started_at"] or ""))
            if started_at is None or started_at < cutoff:
                matched.append(row)

        if bool(args.cleanup_duplicates):
            by_project: Dict[str, List[sqlite3.Row]] = {}
            for row in rows:
                project_id = str(row["project_id"] or "")
                if not project_id:
                    continue
                by_project.setdefault(project_id, []).append(row)

            for _, project_rows in by_project.items():
                if len(project_rows) <= 1:
                    continue
                ordered = sorted(project_rows, key=lambda x: int(x["run_id"]), reverse=True)
                duplicate_rows.extend(ordered[1:])

        # Merge stale + duplicate rows by run_id (no duplicates in final list).
        by_run_id: Dict[int, sqlite3.Row] = {}
        for row in [*matched, *duplicate_rows]:
            by_run_id[int(row["run_id"])] = row
        matched = [by_run_id[k] for k in sorted(by_run_id.keys())]

        payload["matched_count"] = len(matched)
        for row in matched:
            run_id = int(row["run_id"])
            project_id = str(row["project_id"] or "")
            started = str(row["started_at"] or "")
            started_at = _parse_iso(started)
            is_stale = started_at is None or started_at < cutoff
            reason_kind = "stale" if is_stale else "duplicate_running"
            payload["updated_runs"].append(
                {
                    "run_id": run_id,
                    "project_id": project_id,
                    "started_at": started,
                    "cleanup_reason": reason_kind,
                }
            )
            if args.dry_run:
                continue

            summary = _safe_summary(str(row["summary_json"] or ""))
            summary["error"] = str(args.reason or "Marked failed by stale-running cleanup")
            summary["cleanup_tag"] = str(args.tag or "stale_running_cleanup")
            summary["cleanup_reason"] = reason_kind
            summary["cleanup_at"] = now.isoformat()
            cur.execute(
                """
                update runs
                set status='failed', finished_at=?, summary_json=?
                where run_id=?
                """,
                (now.isoformat(), json.dumps(summary, ensure_ascii=False), run_id),
            )

        if not args.dry_run:
            con.commit()
            payload["updated_count"] = len(matched)
        else:
            payload["updated_count"] = 0
    finally:
        con.close()

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
