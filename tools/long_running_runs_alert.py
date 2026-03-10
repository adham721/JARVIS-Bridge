from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent.parent


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


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


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"last_alert_at": "", "last_signature": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_alert_at": "", "last_signature": ""}
    if not isinstance(payload, dict):
        return {"last_alert_at": "", "last_signature": ""}
    return {
        "last_alert_at": str(payload.get("last_alert_at") or ""),
        "last_signature": str(payload.get("last_signature") or ""),
    }


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_dotenv_value(dotenv_path: Path, key: str) -> str:
    if not dotenv_path.exists():
        return ""
    pattern = f"{key}="
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = str(raw or "").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if not line.startswith(pattern):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        return value
    return ""


def _query_candidates(db_path: Path, older_than_minutes: int) -> List[Dict[str, Any]]:
    cutoff = _utc_now() - timedelta(minutes=max(1, int(older_than_minutes)))
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = cur.execute(
            """
            select run_id, project_id, status, started_at
            from runs
            where status='running'
            order by started_at asc
            """
        ).fetchall()
    finally:
        con.close()

    out: List[Dict[str, Any]] = []
    now = _utc_now()
    for row in rows:
        started_at = _parse_iso(row["started_at"])
        if started_at is None:
            continue
        if started_at >= cutoff:
            continue
        age_minutes = round((now - started_at).total_seconds() / 60.0, 1)
        out.append(
            {
                "run_id": int(row["run_id"]),
                "project_id": str(row["project_id"] or ""),
                "status": str(row["status"] or ""),
                "started_at": str(row["started_at"] or ""),
                "age_minutes": age_minutes,
            }
        )
    return out


def _build_signature(candidates: List[Dict[str, Any]]) -> str:
    ids = [str(int(c.get("run_id") or 0)) for c in candidates]
    return ",".join(sorted(ids))


def _send_telegram(token: str, chat_id: str, text: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except Exception:
                body = {"ok": False, "raw": raw}
            return {"ok": bool(body.get("ok", False)), "response": body}
    except (HTTPError, URLError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Alert on long-running rows in runs table.")
    parser.add_argument("--db-path", default=str(ROOT_DIR / "data" / "jarvis_ops.db"))
    parser.add_argument("--older-than-minutes", type=int, default=90)
    parser.add_argument("--cooldown-minutes", type=int, default=60)
    parser.add_argument(
        "--state-path",
        default=str(ROOT_DIR / "data" / "runtime" / "long_running_alert.state.json"),
    )
    parser.add_argument("--send-telegram", action="store_true", default=False)
    parser.add_argument("--max-alert-runs", type=int, default=8)
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser().resolve()
    state_path = Path(args.state_path).expanduser().resolve()
    older_than = max(1, int(args.older_than_minutes or 90))
    cooldown_minutes = max(1, int(args.cooldown_minutes or 60))

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "mode": "long-running-runs-alert",
        "generated_at": _utc_now_iso(),
        "db_path": str(db_path),
        "older_than_minutes": older_than,
        "cooldown_minutes": cooldown_minutes,
        "send_telegram": bool(args.send_telegram),
        "ok": True,
        "alert_candidates_count": 0,
        "alert_candidates": [],
        "alert_sent": False,
        "cooldown_active": False,
        "telegram_result": {},
    }

    if not db_path.exists():
        payload["ok"] = False
        payload["error"] = f"DB not found: {db_path}"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    candidates = _query_candidates(db_path=db_path, older_than_minutes=older_than)
    payload["alert_candidates_count"] = len(candidates)
    payload["alert_candidates"] = candidates[: max(1, int(args.max_alert_runs or 8))]

    state = _load_state(state_path)
    signature = _build_signature(candidates)

    should_alert = len(candidates) > 0
    if should_alert and state.get("last_alert_at"):
        last_dt = _parse_iso(state.get("last_alert_at"))
        if last_dt is not None:
            elapsed = (_utc_now() - last_dt).total_seconds() / 60.0
            if elapsed < cooldown_minutes and state.get("last_signature", "") == signature:
                payload["cooldown_active"] = True
                should_alert = False

    if should_alert and args.send_telegram:
        dotenv = ROOT_DIR / ".env"
        token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip() or _load_dotenv_value(dotenv, "TELEGRAM_BOT_TOKEN")
        chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip() or _load_dotenv_value(dotenv, "TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            payload["telegram_result"] = {
                "ok": False,
                "error": "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
            }
        else:
            lines = [
                "JARVIS Long-Running Alert",
                f"threshold_minutes={older_than}",
                f"count={len(candidates)}",
            ]
            for row in payload["alert_candidates"]:
                lines.append(
                    f"- run_id={row.get('run_id')} project={row.get('project_id')} age_minutes={row.get('age_minutes')}"
                )
            telegram_result = _send_telegram(token=token, chat_id=chat_id, text="\n".join(lines))
            payload["telegram_result"] = telegram_result
            payload["alert_sent"] = bool(telegram_result.get("ok", False))
    elif should_alert:
        payload["alert_sent"] = False

    state["last_signature"] = signature
    if should_alert and (payload.get("alert_sent") or not args.send_telegram):
        state["last_alert_at"] = _utc_now_iso()
    _save_state(state_path, state)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

