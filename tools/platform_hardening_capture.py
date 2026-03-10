from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # type: ignore

from jarvis_engine.bridge_paths import repo_root


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_console(value: Any) -> str:
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def _platform_domain(platform: str) -> str:
    table = {
        "etsy": "etsy.com",
        "instagram": "instagram.com",
        "youtube": "youtube.com",
        "amazon": "amazon.com",
        "redbubble": "redbubble.com",
        "tiktok": "tiktok.com",
        "facebook": "facebook.com",
    }
    key = str(platform or "").strip().lower()
    return table.get(key, "")


def _normalize_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        return ""
    return value


def _looks_like_challenge_page(text: str) -> bool:
    body = str(text or "").lower()
    return any(
        token in body
        for token in (
            "verification required",
            "slide right to secure your access",
            "please enable js and disable any ad blocker",
            "captcha",
            "verify you are human",
            "datadome",
        )
    )


def _collect_rows_from_page(page: Any, domain: str) -> List[Dict[str, Any]]:
    script = """
() => {
  const rows = [];
  const seen = new Set();
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  for (const a of anchors) {
    const href = (a.getAttribute("href") || "").trim();
    if (!href) continue;
    const abs = a.href || href;
    if (!abs || seen.has(abs)) continue;
    const text = (a.innerText || a.textContent || "").replace(/\\s+/g, " ").trim();
    if (text.length < 3) continue;
    seen.add(abs);
    rows.push({ title: text.slice(0, 220), url: abs, evidence_snippet: text.slice(0, 280) });
  }
  return rows;
}
"""
    rows = page.evaluate(script) or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _normalize_url(str(row.get("url") or ""))
        if not url:
            continue
        if domain and domain not in urlsplit(url).netloc.lower():
            continue
        out.append(
            {
                "title": str(row.get("title") or ""),
                "url": url,
                "evidence_snippet": str(row.get("evidence_snippet") or ""),
            }
        )
    return out


def _to_result_rows(
    rows: List[Dict[str, Any]],
    *,
    platform: str,
    source_url: str,
    max_results: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows, start=1):
        url = _normalize_url(str(row.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        title = " ".join(str(row.get("title") or "").split())[:220] or f"{platform} candidate {idx}"
        snippet = " ".join(str(row.get("evidence_snippet") or row.get("snippet") or title).split())[:280]
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        out.append(
            {
                "platform": platform,
                "source": "openclaw_result",
                "source_url": source_url,
                "title": title,
                "url": url,
                "evidence_snippet": snippet,
                "confidence": float(row.get("confidence", 0.72)),
                "metrics": {
                    "rank": int(metrics.get("rank") or len(out) + 1),
                    **metrics,
                    "capture_mode": "manual_browser",
                },
                "retrieved_at": _utc_now_iso(),
            }
        )
        if len(out) >= max(1, int(max_results)):
            break
    return out


def _capture_rows_interactive(
    target_url: str,
    platform: str,
    headless: bool,
    *,
    browser_name: str,
    browser_channel: str,
    user_data_dir: str,
    wait_after_enter_seconds: float,
) -> Dict[str, Any]:
    domain = _platform_domain(platform)
    launch_browser_name = str(browser_name or "").strip().lower() or "chromium"
    launch_channel = str(browser_channel or "").strip()
    profile_dir = Path(user_data_dir).expanduser().resolve() if str(user_data_dir or "").strip() else None
    if launch_browser_name in {"chrome", "msedge", "edge"}:
        launch_browser_name = "chromium"
        if not launch_channel:
            launch_channel = "chrome" if browser_name.lower() == "chrome" else "msedge"

    with sync_playwright() as p:
        if launch_browser_name not in {"chromium", "firefox", "webkit"}:
            launch_browser_name = "chromium"
        browser_type = getattr(p, launch_browser_name)

        if profile_dir:
            context = browser_type.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=launch_channel or None,
                viewport={"width": 1366, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = browser_type.launch(headless=headless, channel=launch_channel or None)
            context = browser.new_context(viewport={"width": 1366, "height": 900})
            page = context.new_page()

        goto_error = ""
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=120000)
            print(f"Opened target page: {target_url}")
        except Exception as exc:
            goto_error = f"{type(exc).__name__}: {exc}"
            print(f"[WARN] Could not open target URL automatically: {goto_error}")
            print(f"[WARN] Open it manually in this browser: {target_url}")
        print("Login/navigate/solve challenge/scroll manually, then press Enter here...")
        input()
        if wait_after_enter_seconds > 0:
            page.wait_for_timeout(max(0, int(wait_after_enter_seconds * 1000)))
        page_text = ""
        try:
            page_text = page.content()
        except Exception:
            page_text = ""
        rows = _collect_rows_from_page(page, domain)
        final_url = str(page.url or target_url)
        challenge_detected = _looks_like_challenge_page(page_text)

        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()  # type: ignore[name-defined]
        except Exception:
            pass

    return {
        "rows": rows,
        "final_url": final_url,
        "challenge_detected": challenge_detected,
        "goto_error": goto_error,
    }


def _load_rows_from_json(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            return [x for x in payload.get("results") if isinstance(x, dict)]
        return [payload]
    return []


def _load_rows_from_urls_file(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    rows: List[Dict[str, Any]] = []
    for raw in text.splitlines():
        line = str(raw or "").strip()
        if not line or line.startswith("#"):
            continue
        markdown_match = re.search(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", line)
        if markdown_match:
            rows.append(
                {
                    "title": markdown_match.group(1).strip(),
                    "url": markdown_match.group(2).strip(),
                    "evidence_snippet": "",
                }
            )
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                rows.append(
                    {
                        "title": parts[0],
                        "url": parts[1],
                        "evidence_snippet": parts[2] if len(parts) >= 3 else "",
                    }
                )
                continue
        if _normalize_url(line):
            rows.append({"title": "", "url": line, "evidence_snippet": ""})
    return rows


def _load_existing_mission_id(project_id: str, platform: str) -> str:
    base = repo_root() / "data" / "openclaw_outbox" / project_id
    if not base.exists():
        return ""
    date_dirs = sorted([p for p in base.iterdir() if p.is_dir()], reverse=True)
    for day_dir in date_dirs:
        missions_file = day_dir / "missions.json"
        if not missions_file.exists():
            continue
        try:
            payload = json.loads(missions_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        missions = payload.get("missions") if isinstance(payload, dict) else None
        if not isinstance(missions, list):
            continue
        for row in missions:
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "").strip().lower() != "platform_hardening":
                continue
            if str(row.get("platform") or "").strip().lower() != platform:
                continue
            mission_id = str(row.get("mission_id") or "").strip()
            if mission_id:
                return mission_id
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture platform_hardening OpenClaw payload into inbox.")
    parser.add_argument("--project", required=True, help="Project id (e.g. kids_pod)")
    parser.add_argument("--platform", required=True, help="Target platform (e.g. etsy)")
    parser.add_argument("--target-url", default="", help="Landing URL to open for manual capture")
    parser.add_argument("--mission-id", default="", help="Optional mission id override")
    parser.add_argument("--from-json-file", default="", help="Use prepared results JSON instead of browser capture")
    parser.add_argument(
        "--from-urls-file",
        default="",
        help="Use plain text/markdown URL list (title|url|snippet OR [title](url) OR url per line)",
    )
    parser.add_argument("--max-results", type=int, default=12, help="Max result rows to include")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument(
        "--browser",
        default="chromium",
        choices=["chromium", "chrome", "msedge", "firefox", "webkit"],
        help="Browser engine for interactive capture",
    )
    parser.add_argument("--browser-channel", default="", help="Optional Playwright channel (e.g. chrome, msedge)")
    parser.add_argument("--user-data-dir", default="", help="Optional persistent browser profile directory")
    parser.add_argument(
        "--wait-after-enter-seconds",
        type=float,
        default=2.0,
        help="Wait time after Enter before scraping links",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    args = parser.parse_args()

    project_id = str(args.project).strip()
    platform = str(args.platform).strip().lower()
    target_url = str(args.target_url).strip()
    if not target_url:
        domain = _platform_domain(platform)
        target_url = f"https://www.{domain}" if domain else "https://www.google.com"

    captured_source_url = target_url
    challenge_detected = False
    goto_error = ""

    if args.from_json_file:
        src = Path(str(args.from_json_file)).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Input file not found: {src}")
        raw_rows = _load_rows_from_json(src)
    elif args.from_urls_file:
        src = Path(str(args.from_urls_file)).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Input file not found: {src}")
        raw_rows = _load_rows_from_urls_file(src)
    else:
        capture_output = _capture_rows_interactive(
            target_url=target_url,
            platform=platform,
            headless=bool(args.headless),
            browser_name=str(args.browser),
            browser_channel=str(args.browser_channel),
            user_data_dir=str(args.user_data_dir),
            wait_after_enter_seconds=float(args.wait_after_enter_seconds),
        )
        raw_rows = list(capture_output.get("rows") or [])
        captured_source_url = str(capture_output.get("final_url") or target_url)
        challenge_detected = bool(capture_output.get("challenge_detected"))
        goto_error = str(capture_output.get("goto_error") or "")

    results = _to_result_rows(
        raw_rows,
        platform=platform,
        source_url=captured_source_url,
        max_results=max(1, int(args.max_results)),
    )
    if not results:
        if goto_error:
            print(f"[WARN] Initial auto-open failed: {goto_error}")
        if captured_source_url.startswith("about:blank"):
            print("[WARN] Browser is still on about:blank. Open the target/search page manually before pressing Enter.")
        if challenge_detected:
            print("[ERROR] Challenge page still active. Complete verification and open real listing/search results page before Enter.")
        print("[ERROR] No usable rows captured. Navigate to listing/search page or use --from-urls-file.")
        return 2

    mission_id = str(args.mission_id or "").strip() or _load_existing_mission_id(project_id, platform)
    if not mission_id:
        mission_id = f"{project_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-openclaw-hardening-manual"

    payload = {
        "type": "platform_hardening",
        "mission_id": mission_id,
        "project_id": project_id,
        "platform": platform,
        "source_url": captured_source_url,
        "session_status": "ok",
        "completed_at": _utc_now_iso(),
        "results": results,
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = repo_root() / "data" / "openclaw_inbox" / project_id / f"platform_hardening_{platform}_{stamp}.json"
    print(f"[OK] mission_id={mission_id}")
    print(f"[OK] platform={platform}")
    print(f"[OK] results={len(results)}")
    print(f"[OK] output={_safe_console(out_file)}")
    if args.dry_run:
        return 0
    _save_json(out_file, payload)
    print(f"[WRITE] inbox payload -> {_safe_console(out_file)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
