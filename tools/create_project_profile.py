from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import List


def _slugify(value: str) -> str:
    text = re.sub(r"[^\w\s-]", " ", str(value or "").strip(), flags=re.UNICODE)
    text = re.sub(r"[\s-]+", "_", text, flags=re.UNICODE).strip("_").lower()
    text = re.sub(r"_+", "_", text)
    return text


def _split_queries(value: str) -> List[str]:
    if not value:
        return []
    items = [part.strip() for part in re.split(r"[,\n;|]+", value) if part.strip()]
    return items


def _default_queries(project_id: str, niche: str) -> List[str]:
    seeds = _split_queries(niche)
    if not seeds:
        seeds = [project_id.replace("_", " ")]
    uniq: List[str] = []
    for item in seeds:
        key = item.lower()
        if key in {x.lower() for x in uniq}:
            continue
        uniq.append(item)
    return uniq[:6]


def _toml_list(items: List[str]) -> str:
    return "[" + ", ".join(f'"{item}"' for item in items) + "]"


def build_profile_toml(
    *,
    project_id: str,
    name: str,
    language: str,
    market: str,
    timezone: str,
    niche: str,
    audience: str,
    positioning: str,
    voice: str,
    enabled: bool,
    queries: List[str],
) -> str:
    youtube_queries = queries[:4] if queries else [project_id.replace("_", " ")]
    trend_keywords = queries if queries else youtube_queries
    signal_queries = (queries[:5] if queries else youtube_queries) or youtube_queries
    project_niche = niche or project_id.replace("_", " ")

    return f"""id = "{project_id}"
name = "{name}"
enabled = {"true" if enabled else "false"}
language = "{language}"
market = "{market}"
timezone = "{timezone}"

[brand]
channel_name = "{name}"
niche = "{project_niche}"
audience = "{audience}"
positioning = "{positioning}"
voice = "{voice}"
constraints = []
pod_products = []

[workflow]
mode = "chat_only_human_gate"
daily_slots = 1
platform_sequence = ["youtube", "tiktok", "facebook"]
quality_gate_required = true
cycle_minutes = 120

[kpi_targets]
monthly_videos = 30
min_ctr_percent = 2.0
min_retention_percent = 35.0

[youtube]
queries = {_toml_list(youtube_queries)}
country = "US"
limit = 15
max_age_hours = 72
min_views = 5000
min_views_per_hour = 80
min_duration_seconds = 45
fetch_details = false
details_limit = 6

[trends]
enabled = true
country = "{market}"
keywords = {_toml_list(trend_keywords)}
top_n = 20
timeframe = "now 7-d"

[signals]
enabled = true
platforms = ["etsy", "amazon", "redbubble", "tiktok", "facebook", "instagram"]
queries = {_toml_list(signal_queries)}
max_results_per_query = 4
engines = "google,bing,duckduckgo"
request_timeout_seconds = 8
min_delay_seconds = 0.8
max_domains_per_platform = 2
max_hints_per_platform = 2
max_requests_per_run = 60
cache_ttl_seconds = 10800
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new JARVIS project profile TOML")
    parser.add_argument("--project-id", required=True, help="Unique project id (e.g. gaming_kids_lab)")
    parser.add_argument("--name", default="", help="Display name")
    parser.add_argument("--niche", default="", help="Niche/topic (can include comma-separated seed terms)")
    parser.add_argument("--language", default="ar")
    parser.add_argument("--market", default="EG")
    parser.add_argument("--timezone", default="Africa/Cairo")
    parser.add_argument("--audience", default="General audience")
    parser.add_argument("--positioning", default="Differentiate by format quality + speed of execution")
    parser.add_argument("--voice", default="clear, practical")
    parser.add_argument("--query", action="append", default=[], help="Repeatable query seed")
    parser.add_argument("--output-dir", default="projects")
    parser.add_argument("--force", action="store_true", help="Overwrite if profile exists")
    parser.add_argument("--disabled", action="store_true", help="Create profile with enabled=false")
    parser.add_argument("--print-only", action="store_true", help="Print TOML only, do not write file")
    args = parser.parse_args()

    project_id = _slugify(args.project_id)
    if not project_id:
        raise SystemExit("Invalid --project-id after normalization")

    name = str(args.name or project_id.replace("_", " ").title()).strip()
    query_list = [q.strip() for q in list(args.query or []) if q and q.strip()]
    if not query_list:
        query_list = _default_queries(project_id, str(args.niche or ""))

    toml_text = build_profile_toml(
        project_id=project_id,
        name=name,
        language=str(args.language).strip() or "ar",
        market=str(args.market).strip() or "EG",
        timezone=str(args.timezone).strip() or "Africa/Cairo",
        niche=str(args.niche).strip(),
        audience=str(args.audience).strip() or "General audience",
        positioning=str(args.positioning).strip() or "Differentiate by format quality + speed of execution",
        voice=str(args.voice).strip() or "clear, practical",
        enabled=not bool(args.disabled),
        queries=query_list,
    )

    if args.print_only:
        print(toml_text)
        return 0

    output_dir = Path(str(args.output_dir)).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{project_id}.toml"
    if out_path.exists() and not args.force:
        raise SystemExit(f"Profile already exists: {out_path} (use --force to overwrite)")
    out_path.write_text(toml_text, encoding="utf-8")
    print(f"[OK] profile written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

