from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PLATFORMS = ["youtube", "amazon", "etsy", "redbubble", "tiktok", "facebook", "instagram"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_load_json(text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_snapshot(summary: Dict[str, Any]) -> Dict[str, Any]:
    precision = dict(summary.get("precision_summary") or {}) if isinstance(summary.get("precision_summary"), dict) else {}
    coverage = dict(summary.get("coverage_summary") or {}) if isinstance(summary.get("coverage_summary"), dict) else {}
    platform_report = (
        dict(summary.get("crawl_platform_report") or {})
        if isinstance(summary.get("crawl_platform_report"), dict)
        else {}
    )

    platforms: Dict[str, Dict[str, Any]] = {}
    for platform in DEFAULT_PLATFORMS:
        row_raw = platform_report.get(platform)
        row = dict(row_raw or {}) if isinstance(row_raw, dict) else {}
        platforms[platform] = {
            "effective_quality_gate": str(row.get("effective_quality_gate") or row.get("quality_gate") or "").strip().lower(),
            "effective_block_rate": _safe_float(
                row.get("effective_block_rate") if row.get("effective_block_rate") is not None else row.get("block_rate")
            ),
            "effective_records_total": int(row.get("effective_records_total") or row.get("records_total") or 0),
        }

    return {
        "coverage_block_rate": _safe_float(coverage.get("block_rate")),
        "precision": {
            "claims_total": int(precision.get("claims_total") or 0),
            "approved_claims": int(precision.get("approved_claims") or 0),
            "needs_review_claims": int(precision.get("needs_review_claims") or 0),
            "rejected_claims": int(precision.get("rejected_claims") or 0),
            "content_candidates_in_topic_ratio": _safe_float(precision.get("content_candidates_in_topic_ratio")),
            "top_candidate_in_topic": bool(precision.get("top_candidate_in_topic", False)),
        },
        "platforms": platforms,
    }


def _row_to_run_meta(row: sqlite3.Row | None) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "run_id": int(row["run_id"]),
        "project_id": str(row["project_id"] or ""),
        "status": str(row["status"] or "").strip().lower(),
        "started_at": str(row["started_at"] or ""),
        "finished_at": str(row["finished_at"] or ""),
    }


def _load_latest_runs(db_path: Path, project_id: str) -> List[sqlite3.Row]:
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = cur.execute(
            """
            select run_id, project_id, status, started_at, finished_at, summary_json
            from runs
            where project_id = ? and status != 'running'
            order by run_id desc
            limit 2
            """,
            (project_id,),
        ).fetchall()
        return rows
    finally:
        con.close()


def _compute_payload(project_id: str, rows: List[sqlite3.Row]) -> Dict[str, Any]:
    after_row = rows[0] if rows else None
    before_row = rows[1] if len(rows) > 1 else None

    after_summary = _safe_load_json(str(after_row["summary_json"] or "")) if after_row else {}
    before_summary = _safe_load_json(str(before_row["summary_json"] or "")) if before_row else {}
    after_snapshot = _extract_snapshot(after_summary)
    before_snapshot = _extract_snapshot(before_summary) if before_row else {}

    after_platforms = dict(after_snapshot.get("platforms") or {})
    before_platforms = dict(before_snapshot.get("platforms") or {})
    changed_platforms: Dict[str, Any] = {}
    for platform in DEFAULT_PLATFORMS:
        a = dict(after_platforms.get(platform) or {})
        b = dict(before_platforms.get(platform) or {})
        if a != b:
            changed_platforms[platform] = {"before": b, "after": a}

    cov_before = _safe_float(before_snapshot.get("coverage_block_rate")) if before_row else None
    cov_after = _safe_float(after_snapshot.get("coverage_block_rate")) if after_row else None

    precision_before = dict(before_snapshot.get("precision") or {}) if before_row else {}
    precision_after = dict(after_snapshot.get("precision") or {}) if after_row else {}

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "mode": "cycle-delta-report",
        "generated_at": _utc_now_iso(),
        "project_id": project_id,
        "before_run": _row_to_run_meta(before_row),
        "after_run": _row_to_run_meta(after_row),
        "before_run_id": int(before_row["run_id"]) if before_row else None,
        "after_run_id": int(after_row["run_id"]) if after_row else None,
        "coverage_block_rate": {
            "before": cov_before,
            "after": cov_after,
            "delta": (cov_after - cov_before) if (cov_before is not None and cov_after is not None) else None,
        },
        "precision": {
            "claims_total": {
                "before": precision_before.get("claims_total"),
                "after": precision_after.get("claims_total"),
            },
            "approved_claims": {
                "before": precision_before.get("approved_claims"),
                "after": precision_after.get("approved_claims"),
            },
            "needs_review_claims": {
                "before": precision_before.get("needs_review_claims"),
                "after": precision_after.get("needs_review_claims"),
            },
            "rejected_claims": {
                "before": precision_before.get("rejected_claims"),
                "after": precision_after.get("rejected_claims"),
            },
            "content_candidates_in_topic_ratio": {
                "before": precision_before.get("content_candidates_in_topic_ratio"),
                "after": precision_after.get("content_candidates_in_topic_ratio"),
            },
            "top_candidate_in_topic": {
                "before": precision_before.get("top_candidate_in_topic"),
                "after": precision_after.get("top_candidate_in_topic"),
            },
        },
        "platform_changes": changed_platforms,
        "changed_platform_count": len(changed_platforms),
        "ok": bool(after_row is not None),
    }
    return payload


def _write_default_outputs(payload: Dict[str, Any], project_id: str) -> Dict[str, str]:
    run_id = payload.get("after_run_id")
    after_run = dict(payload.get("after_run") or {})
    stamp_source = str(after_run.get("finished_at") or after_run.get("started_at") or "")
    date_dir = ""
    if "T" in stamp_source:
        date_dir = stamp_source.split("T", 1)[0]
    if not date_dir:
        date_dir = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    report_dir = ROOT_DIR / "data" / "reports" / project_id / date_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    latest_path = report_dir / "cycle_delta_latest.json"
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    latest_path.write_text(payload_text, encoding="utf-8")

    run_path = report_dir / f"cycle_delta_run_{run_id}.json" if run_id is not None else report_dir / "cycle_delta_run_unknown.json"
    run_path.write_text(payload_text, encoding="utf-8")

    return {
        "output_latest_path": str(latest_path),
        "output_run_path": str(run_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate before/after cycle delta report from runs.summary_json.")
    parser.add_argument("--project", required=True, help="Project id")
    parser.add_argument("--db-path", default=str(ROOT_DIR / "data" / "jarvis_ops.db"))
    parser.add_argument("--output", default="", help="Optional explicit output path")
    args = parser.parse_args()

    project_id = str(args.project or "").strip()
    db_path = Path(str(args.db_path)).expanduser().resolve()
    if not project_id:
        raise SystemExit("Missing --project")
    if not db_path.exists():
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "mode": "cycle-delta-report",
                    "ok": False,
                    "project_id": project_id,
                    "error": f"DB not found: {db_path}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2)

    rows = _load_latest_runs(db_path, project_id)
    payload = _compute_payload(project_id=project_id, rows=rows)
    if args.output:
        output_path = Path(str(args.output)).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["output_latest_path"] = str(output_path)
        payload["output_run_path"] = str(output_path)
    else:
        payload.update(_write_default_outputs(payload=payload, project_id=project_id))

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

