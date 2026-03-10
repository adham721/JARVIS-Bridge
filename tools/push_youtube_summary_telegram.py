import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

import requests


# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.youtube_details_impl import youtube_summary_deep


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def send_telegram_message(token: str, chat_id: str, text: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=30)
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "status_code": resp.status_code, "text": resp.text}


def format_results(summary: List[Dict[str, Any]], details: List[Dict[str, Any]]) -> str:
    details_by_id = {d.get("id"): d for d in details or []}
    lines = ["YouTube deep summary (top results):"]
    for idx, r in enumerate(summary, start=1):
        url = r.get("url") or ""
        vid = url.split("watch?v=")[-1] if "watch?v=" in url else None
        meta = details_by_id.get(vid, {})

        likes = meta.get("like_count")
        tags = meta.get("tags") or []
        tags_text = ", ".join(tags[:3])
        likes_text = likes if likes is not None else "-"

        lines.append(f"{idx}. {r.get('title','')[:120]}")
        lines.append(
            f"   views: {r.get('views')} | vph: {round(r.get('views_per_hour',0),2)} | dur: {r.get('duration_seconds')}s | likes: {likes_text}"
        )
        if tags_text:
            lines.append(f"   tags: {tags_text}")
        lines.append(f"   {url or meta.get('webpage_url','')}")
    return "\n".join(lines)


def main() -> None:
    # Load .env if present to pick up TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    env_file = ROOT / ".env"
    _load_env_file(env_file)

    parser = argparse.ArgumentParser(description="Run youtube_summary_deep and push to Telegram")
    parser.add_argument("--query", default="trending")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--country", default="US")
    parser.add_argument("--max-age-hours", type=int, default=48)
    parser.add_argument("--min-views", type=int, default=10_000)
    parser.add_argument("--min-views-per-hour", type=int, default=200)
    parser.add_argument("--min-duration", type=int, default=60)
    parser.add_argument("--cookies", default=None, help="Path to YouTube cookies (Netscape format)")
    parser.add_argument("--get-comments", action="store_true", help="Also fetch comments (slower)")
    parser.add_argument("--token", default=None, help="Telegram bot token (overrides env TELEGRAM_BOT_TOKEN)")
    parser.add_argument("--chat-id", default=None, help="Telegram chat id (overrides env TELEGRAM_CHAT_ID)")

    args = parser.parse_args()

    token = args.token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = args.chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(json.dumps({"success": False, "error": "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"}, ensure_ascii=False, indent=2))
        return

    deep_resp = youtube_summary_deep({
        "query": args.query,
        "limit": args.limit,
        "country": args.country,
        "max_age_hours": args.max_age_hours,
        "min_views": args.min_views,
        "min_views_per_hour": args.min_views_per_hour,
        "min_duration_seconds": args.min_duration,
        "cookies_path": args.cookies,
        "get_comments": args.get_comments,
    })

    summary = deep_resp.get("summary") or []
    details = deep_resp.get("details") or []

    text = format_results(summary, details)
    resp = send_telegram_message(token, chat_id, text)

    # Persist last run for auditing
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    outfile = logs_dir / "youtube_summary_last.json"
    payload = {
        "query": args.query,
        "timestamp": int(Path(outfile).stat().st_mtime) if outfile.exists() else None,
        "summary": summary,
        "details": details,
        "telegram_response": resp,
    }
    outfile.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "success": resp.get("ok", False),
        "telegram_response": resp,
        "count": len(summary),
        "saved_to": str(outfile)
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
