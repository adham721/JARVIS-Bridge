import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yt_dlp


def _normalize_comment_rows(rows: Any, *, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "id": row.get("id"),
                "author": row.get("author"),
                "text": text,
                "like_count": row.get("like_count"),
            }
        )
        if len(out) >= max(1, int(limit)):
            break
    return out


def fetch_video_details(
    video_ids: List[str],
    cookies_path: Optional[str] = None,
    get_comments: bool = False,
    max_comments: int = 12,
) -> List[Dict[str, Any]]:
    if not video_ids:
        return []

    ytb_args: Dict[str, List[str]] = {"player_client": ["default"]}
    if get_comments and int(max_comments or 0) > 0:
        ytb_args["max_comments"] = [str(max(1, int(max_comments)))]

    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "noplaylist": True,
        "geo_bypass": True,
        "writeinfojson": False,
        "getcomments": get_comments,
        # Force default client to avoid SABR/web_safari warnings and JS runtime requirement
        "extractor_args": {"youtube": ytb_args},
    }

    if cookies_path:
        cookiefile = Path(cookies_path)
        if not cookiefile.exists():
            raise FileNotFoundError(f"Cookie file not found: {cookies_path}")
        ydl_opts["cookiefile"] = str(cookiefile)

    details: List[Dict[str, Any]] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for vid in video_ids:
            url = f"https://www.youtube.com/watch?v={vid}"
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                details.append({"id": vid, "error": str(e)})
                continue

            details.append(
                {
                    "id": info.get("id"),
                    "title": info.get("title"),
                    "description": info.get("description"),
                    "channel": info.get("channel"),
                    "channel_id": info.get("channel_id"),
                    "channel_url": info.get("channel_url"),
                    "duration": info.get("duration"),
                    "view_count": info.get("view_count"),
                    "like_count": info.get("like_count"),
                    "comment_count": info.get("comment_count"),
                    "tags": info.get("tags"),
                    "thumbnails": info.get("thumbnails"),
                    "upload_date": info.get("upload_date"),
                    "upload_timestamp": info.get("timestamp"),
                    "comments": (
                        _normalize_comment_rows(info.get("comments"), limit=max_comments)
                        if get_comments
                        else None
                    ),
                    "webpage_url": info.get("webpage_url"),
                }
            )

    return details


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch detailed YouTube metadata for given video IDs")
    parser.add_argument("--ids", nargs="*", help="List of video IDs")
    parser.add_argument("--cookies", default=None, help="Path to YouTube cookies (Netscape format)")
    parser.add_argument("--get-comments", action="store_true", help="Fetch comments (slower)")
    parser.add_argument("--max-comments", type=int, default=12, help="Max comments to return per video")

    args = parser.parse_args()
    if not args.ids:
        print(json.dumps({"success": False, "error": "No video IDs provided"}, ensure_ascii=False, indent=2))
        return

    details = fetch_video_details(
        args.ids,
        cookies_path=args.cookies,
        get_comments=args.get_comments,
        max_comments=args.max_comments,
    )
    print(json.dumps({"success": True, "count": len(details), "results": details}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
