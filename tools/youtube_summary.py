import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import re

import scrapetube

import yt_dlp


class _NoLog:
    def debug(self, msg):  # noqa: ANN001
        return None

    def warning(self, msg):  # noqa: ANN001
        return None

    def error(self, msg):  # noqa: ANN001
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_upload_date(upload_date: str) -> datetime:
    # yt-dlp returns YYYYMMDD
    return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)


def _parse_views_text(text: str) -> int:
    """Parse strings like '1,234 views' or 'No views' to int."""
    if not text:
        return 0
    nums = re.findall(r"[\d,]+", text)
    if not nums:
        return 0
    try:
        return int(nums[0].replace(",", ""))
    except Exception:
        return 0


def _parse_duration_text(text: str) -> int:
    """Parse duration like '12:34' or '1:02:03' to seconds."""
    if not text:
        return 0
    parts = text.split(":")
    try:
        parts = [int(p) for p in parts]
    except Exception:
        return 0
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def _parse_age_hours(text: str) -> float:
    """Parse age like '3 hours ago', '1 day ago', '2 weeks ago'."""
    if not text:
        return 1e9  # very old
    m = re.match(r"(\d+)\s+(second|seconds|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)", text)
    if not m:
        return 1e9
    value = int(m.group(1))
    unit = m.group(2)
    if "second" in unit:
        return value / 3600
    if "minute" in unit:
        return value / 60
    if "hour" in unit:
        return float(value)
    if "day" in unit:
        return value * 24
    if "week" in unit:
        return value * 24 * 7
    if "month" in unit:
        return value * 24 * 30
    if "year" in unit:
        return value * 24 * 365
    return 1e9


def summarize_youtube(
    query: str,
    limit: int = 20,
    country: str = "US",
    max_age_hours: int = 48,
    min_views: int = 10_000,
    min_views_per_hour: int = 200,
    min_duration_seconds: int = 60,
    cookies_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)

    def _using_yt_dlp() -> List[Dict[str, Any]]:
        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "skip_download": True,
            # Fast metadata extraction; falls back to scrapetube if fields are missing.
            "extract_flat": True,
            "noplaylist": True,
            "geo_bypass": True,
            "geo_bypass_country": country,
            # Keep runs bounded when network is flaky.
            "socket_timeout": 15,
            "retries": 1,
            "logger": _NoLog(),
            # Avoid SABR/web_safari warnings and JS runtime requirement.
            "extractor_args": {"youtube": {"player_client": ["default"]}},
        }
        if cookies_path:
            cookiefile = Path(cookies_path)
            if not cookiefile.exists():
                raise FileNotFoundError(f"Cookie file not found: {cookies_path}")
            ydl_opts["cookiefile"] = str(cookiefile)

        search_query = f"ytsearchdate{limit * 3}:{query}"
        results: List[Dict[str, Any]] = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(search_query, download=False)
            except Exception:
                return []
            entries = info.get("entries", []) if isinstance(info, dict) else []
            for entry in entries:
                try:
                    upload_date = entry.get("upload_date") or entry.get("release_date")
                    if not upload_date:
                        continue
                    published = _parse_upload_date(upload_date)
                    age_hours = (now - published).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        continue

                    duration = entry.get("duration") or 0
                    if duration < min_duration_seconds:
                        continue

                    view_count = entry.get("view_count") or 0
                    if view_count < min_views:
                        continue

                    views_per_hour = view_count / max(age_hours, 1)
                    if views_per_hour < min_views_per_hour:
                        continue

                    results.append(
                        {
                            "title": entry.get("title", ""),
                            "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}",
                            "channel": entry.get("uploader") or entry.get("channel") or "",
                            "views": view_count,
                            "views_per_hour": views_per_hour,
                            "duration_seconds": duration,
                            "upload_date": upload_date,
                            "age_hours": age_hours,
                        }
                    )
                except Exception:
                    continue
        results.sort(key=lambda x: x.get("views_per_hour", 0), reverse=True)
        return results[:limit]

    def _fallback_scrapetube() -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        try:
            entries = list(scrapetube.get_search(query, limit=limit * 5))
        except Exception:
            return []
        def filter_entries(age_limit: float) -> List[Dict[str, Any]]:
            subset: List[Dict[str, Any]] = []
            for entry in entries:
                try:
                    info = entry.get("videoRenderer") or entry
                    video_id = info.get("videoId")
                    title_parts = info.get("title", {}).get("runs", [{}])
                    title = title_parts[0].get("text", "") if title_parts else ""

                    view_text = info.get("viewCountText", {}).get("simpleText") or ""
                    view_count = _parse_views_text(view_text)
                    if view_count < min_views:
                        continue

                    duration_text = info.get("lengthText", {}).get("simpleText") or ""
                    duration = _parse_duration_text(duration_text)
                    if duration and duration < min_duration_seconds:
                        continue

                    age_text = info.get("publishedTimeText", {}).get("simpleText") or ""
                    age_hours = _parse_age_hours(age_text)
                    if age_hours > age_limit:
                        continue

                    views_per_hour = view_count / max(age_hours, 1)
                    if views_per_hour < min_views_per_hour:
                        continue

                    channel_runs = info.get("ownerText", {}).get("runs", [{}])
                    uploader = channel_runs[0].get("text", "") if channel_runs else ""

                    subset.append(
                        {
                            "title": title,
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "channel": uploader,
                            "views": view_count,
                            "views_per_hour": views_per_hour,
                            "duration_seconds": duration,
                            "upload_date": None,
                            "age_hours": age_hours,
                        }
                    )
                except Exception:
                    continue
            subset.sort(key=lambda x: x.get("views_per_hour", 0), reverse=True)
            return subset[:limit]

        results = filter_entries(max_age_hours)
        if results:
            return results
        # Strict freshness: if nothing recent is available, return empty.
        return []

    primary = _using_yt_dlp()
    if primary:
        return primary
    return _fallback_scrapetube()


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize YouTube trending/search with quality filters")
    parser.add_argument("--query", default="trending", help="Search query")
    parser.add_argument("--limit", type=int, default=20, help="Max results to return")
    parser.add_argument("--country", default="US", help="Country code for geo bypass")
    parser.add_argument("--max-age-hours", type=int, default=48, help="Max age in hours")
    parser.add_argument("--min-views", type=int, default=10_000, help="Minimum views")
    parser.add_argument("--min-views-per-hour", type=int, default=200, help="Minimum views per hour")
    parser.add_argument("--min-duration", type=int, default=60, help="Minimum duration in seconds (skip shorts)")
    parser.add_argument("--cookies", default=None, help="Path to YouTube cookies (Netscape format)")

    args = parser.parse_args()

    results = summarize_youtube(
        query=args.query,
        limit=args.limit,
        country=args.country,
        max_age_hours=args.max_age_hours,
        min_views=args.min_views,
        min_views_per_hour=args.min_views_per_hour,
        min_duration_seconds=args.min_duration,
        cookies_path=args.cookies,
    )

    print(json.dumps({"success": True, "count": len(results), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
