from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "actions" / "mongo_intel_bridge.openapi.yaml"
ENV_PATH = REPO_ROOT / ".env"


def _read_lines(path: Path) -> List[str]:
    if not path.exists():
        raise RuntimeError(f"file not found: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def _find_server_url_line(lines: List[str]) -> Tuple[int, str]:
    in_servers = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line == "servers:":
            in_servers = True
            continue
        if in_servers and line.startswith("- url:"):
            value = line.split(":", 1)[1].strip()
            return i, value
        if in_servers and line and not line.startswith("-"):
            # Left servers block
            break
    raise RuntimeError("Could not find `servers: - url:` in OpenAPI file.")


def _validate_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise RuntimeError("URL is empty")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise RuntimeError("URL must start with https://")
    if not parsed.netloc:
        raise RuntimeError("URL must include host")
    return value.rstrip("/")


def _set_or_append_env(key: str, value: str) -> None:
    lines = _read_lines(ENV_PATH) if ENV_PATH.exists() else []
    out: List[str] = []
    replaced = False
    prefix = f"{key}="
    for raw in lines:
        if raw.startswith(prefix):
            out.append(f"{prefix}{value}")
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.append(f"{prefix}{value}")
    ENV_PATH.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def run(url: str, update_env: bool) -> Dict[str, Any]:
    lines = _read_lines(OPENAPI_PATH)
    idx, before = _find_server_url_line(lines)
    normalized = _validate_url(url)

    indent = lines[idx].split("- url:", 1)[0]
    lines[idx] = f"{indent}- url: {normalized}"
    OPENAPI_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if update_env:
        _set_or_append_env("JARVIS_BRIDGE_SERVER_URL", normalized)

    return {
        "ok": True,
        "openapi_path": str(OPENAPI_PATH),
        "before": before,
        "after": normalized,
        "updated_env": bool(update_env),
        "env_path": str(ENV_PATH),
    }


def inspect() -> Dict[str, Any]:
    lines = _read_lines(OPENAPI_PATH)
    _, current = _find_server_url_line(lines)
    placeholder = "YOUR_AZURE_FUNCTION_HOST" in current
    return {
        "ok": True,
        "openapi_path": str(OPENAPI_PATH),
        "current_server_url": current,
        "is_placeholder": placeholder,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or update Mongo Bridge OpenAPI server URL.")
    parser.add_argument("--url", default="", help="Azure Function host URL (https://...)")
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Also set JARVIS_BRIDGE_SERVER_URL in .env",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Only print current URL without editing",
    )
    args = parser.parse_args()

    try:
        if args.inspect or not str(args.url).strip():
            payload = inspect()
        else:
            payload = run(str(args.url), update_env=bool(args.update_env))
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=True, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
