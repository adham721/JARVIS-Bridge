from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # type: ignore

from jarvis_engine.bridge_paths import repo_root


PLACEHOLDER_TOKENS = (
    "manual_reauth_placeholder",
    "placeholder",
    "changeme",
    "dummy",
    "example",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform_defaults(platform: str) -> Dict[str, str]:
    key = str(platform or "").strip().lower()
    table = {
        "etsy": {
            "login_url": "https://www.etsy.com/signin",
            "cookie_store": "data/auth_sessions/etsy.cookies.json",
            "domain": "etsy.com",
        },
        "instagram": {
            "login_url": "https://www.instagram.com/accounts/login/",
            "cookie_store": "data/auth_sessions/instagram.cookies.json",
            "domain": "instagram.com",
        },
    }
    if key not in table:
        raise ValueError(f"Unsupported platform: {platform}")
    return dict(table[key])


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_cookie_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        nested = payload.get("cookies")
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
    return []


def _normalize_samesite(value: Any) -> str | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token in {"lax"}:
        return "Lax"
    if token in {"strict"}:
        return "Strict"
    if token in {"none", "no_restriction", "norestriction"}:
        return "None"
    return None


def _normalize_cookie_domain(value: Any) -> str:
    domain = str(value or "").strip().lower()
    if domain.startswith(".www."):
        domain = "." + domain[5:]
    elif domain.startswith("www."):
        domain = domain[4:]
    return domain


def _normalize_cookie_record(row: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or "").strip()
    value = str(row.get("value") or "")
    if not name:
        return None
    out: Dict[str, Any] = {
        "name": name,
        "value": value,
    }
    url = str(row.get("url") or "").strip()
    domain = _normalize_cookie_domain(row.get("domain"))
    if url:
        out["url"] = url
    elif domain:
        out["domain"] = domain
        path_value = str(row.get("path") or "/").strip() or "/"
        if not path_value.startswith("/"):
            path_value = f"/{path_value}"
        out["path"] = path_value
    else:
        return None

    if "path" not in out:
        path_value = str(row.get("path") or "/").strip() or "/"
        if not path_value.startswith("/"):
            path_value = f"/{path_value}"
        out["path"] = path_value

    out["secure"] = bool(row.get("secure", False))
    out["httpOnly"] = bool(row.get("httpOnly", False))

    expires = row.get("expires", row.get("expirationDate"))
    try:
        expires_value = float(expires)
        if expires_value > 0:
            out["expires"] = expires_value
    except Exception:
        pass

    same_site = _normalize_samesite(row.get("sameSite"))
    if same_site:
        out["sameSite"] = same_site
    return out


def _normalize_cookie_list(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in cookies:
        normalized = _normalize_cookie_record(row)
        if not normalized:
            continue
        key = (
            str(normalized.get("domain") or normalized.get("url") or "").lower(),
            str(normalized.get("path") or "/"),
            str(normalized.get("name") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _is_placeholder_cookie(cookies: List[Dict[str, Any]]) -> bool:
    for row in cookies:
        value = str(row.get("value") or "").strip().lower()
        if not value:
            continue
        if any(token in value for token in PLACEHOLDER_TOKENS):
            return True
    return False


def _filter_cookies_for_domain(cookies: List[Dict[str, Any]], domain_token: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    domain_token = str(domain_token or "").strip().lower()
    for row in cookies:
        domain = str(row.get("domain") or "").strip().lower()
        if domain_token and domain_token not in domain:
            continue
        out.append(row)
    return out


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
            payload = _load_json(missions_file)
        except Exception:
            continue
        missions = payload.get("missions") if isinstance(payload, dict) else None
        if not isinstance(missions, list):
            continue
        for row in missions:
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "").strip().lower() != "session_refresh":
                continue
            if str(row.get("platform") or "").strip().lower() != platform:
                continue
            mission_id = str(row.get("mission_id") or "").strip()
            if mission_id:
                return mission_id
    return ""


def _capture_cookies_interactive(login_url: str, domain_token: str, headless: bool) -> List[Dict[str, Any]]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url, wait_until="domcontentloaded", timeout=120000)
        print(f"Opened login page: {login_url}")
        print("Login manually in the opened browser, then press Enter here...")
        input()
        cookies = context.cookies()
        browser.close()
    return _normalize_cookie_list(_filter_cookies_for_domain(cookies, domain_token))


def _build_payload(
    *,
    mission_id: str,
    platform: str,
    source_url: str,
    cookie_store: str,
    cookies: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "type": "session_refresh",
        "mission_id": mission_id,
        "platform": platform,
        "source_url": source_url,
        "session_status": "ok",
        "refreshed_at": _utc_now_iso(),
        "cookie_store": cookie_store,
        "cookies": cookies,
    }


def _write_status_sidecar(platform: str, cookie_store_abs: Path, source_file: str, mission_id: str, source_url: str) -> Path:
    status_path = cookie_store_abs.with_suffix(cookie_store_abs.suffix + ".status.json")
    payload = {
        "schema_version": 1,
        "platform": platform,
        "session_status": "ok",
        "refreshed_at": _utc_now_iso(),
        "mission_id": mission_id,
        "source_url": source_url,
        "cookie_store": str(cookie_store_abs.relative_to(repo_root())).replace("\\", "/"),
        "cookie_store_written": True,
        "source_file": source_file,
        "meta": {
            "auth_mode": "project_login",
            "reason": "manual_refresh_capture",
        },
    }
    _save_json(status_path, payload)
    return status_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture real login cookies and write OpenClaw session_refresh payload.")
    parser.add_argument("--project", required=True, help="Project id (e.g. kids_pod)")
    parser.add_argument("--platform", required=True, choices=["etsy", "instagram"], help="Target platform")
    parser.add_argument("--mission-id", default="", help="Optional mission_id override")
    parser.add_argument("--login-url", default="", help="Optional login URL override")
    parser.add_argument("--cookie-store", default="", help="Optional cookie store path (relative to repo root)")
    parser.add_argument("--from-cookie-file", default="", help="Use cookies from file instead of interactive browser")
    parser.add_argument("--headless", action="store_true", help="Run browser headless (default: false)")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    args = parser.parse_args()

    defaults = _platform_defaults(args.platform)
    login_url = str(args.login_url or defaults["login_url"]).strip()
    cookie_store_rel = str(args.cookie_store or defaults["cookie_store"]).strip().replace("\\", "/")
    domain_token = defaults["domain"]
    cookie_store_abs = (repo_root() / cookie_store_rel).resolve()
    inbox_dir = repo_root() / "data" / "openclaw_inbox" / str(args.project).strip()

    if args.from_cookie_file:
        from_file = Path(args.from_cookie_file).expanduser().resolve()
        if not from_file.exists():
            raise FileNotFoundError(f"Cookie file not found: {from_file}")
        cookies = _extract_cookie_list(_load_json(from_file))
        cookies = _normalize_cookie_list(_filter_cookies_for_domain(cookies, domain_token))
    else:
        cookies = _capture_cookies_interactive(login_url=login_url, domain_token=domain_token, headless=bool(args.headless))

    if len(cookies) < 2:
        print(f"[ERROR] Not enough cookies for {args.platform}: {len(cookies)}")
        return 2
    if _is_placeholder_cookie(cookies):
        print("[ERROR] Placeholder cookie values detected. Export real cookies and retry.")
        return 3

    mission_id = str(args.mission_id or "").strip() or _load_existing_mission_id(str(args.project).strip(), args.platform)
    if not mission_id:
        now_key = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        mission_id = f"{args.project}-{now_key}-openclaw-reauth-manual-{args.platform}"

    payload = _build_payload(
        mission_id=mission_id,
        platform=args.platform,
        source_url=login_url,
        cookie_store=cookie_store_rel,
        cookies=cookies,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    inbox_file = inbox_dir / f"session_refresh_{args.platform}_{stamp}.json"

    print(f"[OK] Valid cookies: {len(cookies)} for {args.platform}")
    print(f"[OK] mission_id={mission_id}")
    print(f"[OK] cookie_store={cookie_store_rel}")
    print(f"[OK] inbox_file={inbox_file}")

    if args.dry_run:
        return 0

    _save_json(cookie_store_abs, cookies)
    _save_json(inbox_file, payload)
    status_path = _write_status_sidecar(
        platform=args.platform,
        cookie_store_abs=cookie_store_abs,
        source_file=inbox_file.name,
        mission_id=mission_id,
        source_url=login_url,
    )
    print(f"[WRITE] cookie_store -> {cookie_store_abs}")
    print(f"[WRITE] inbox payload -> {inbox_file}")
    print(f"[WRITE] status sidecar -> {status_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
