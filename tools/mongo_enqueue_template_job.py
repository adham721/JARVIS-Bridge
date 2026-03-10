from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis_engine.mongo_intel_bridge import enqueue_intel_job, mongo_bridge_enabled

TEMPLATE_DIR = REPO_ROOT / "actions" / "job_templates"
TEMPLATES: Dict[str, str] = {
    "video_social": "video_social.template.md",
    "ecommerce_pod": "ecommerce_pod.template.md",
    "freelance_services": "freelance_services.template.md",
}

DEFAULT_PLATFORMS: Dict[str, str] = {
    "video_social": "youtube,tiktok,instagram,facebook",
    "ecommerce_pod": "etsy,amazon,redbubble",
    "freelance_services": "instagram,facebook,upwork,freelancer",
}

DEFAULT_GOALS: Dict[str, str] = {
    "video_social": "Produce content strategy and prompts that increase reach and conversion.",
    "ecommerce_pod": "Identify profitable product opportunities and listing assets for conversion.",
    "freelance_services": "Build a clear offer and outreach system to acquire qualified clients.",
}


def _safe_project_id(value: str) -> str:
    return re.sub(r"[^a-z0-9_\\-]", "_", str(value or "").strip().lower())


def _parse_meta_json(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("--meta-json must be a JSON object")
    return data


def _load_template(template_key: str) -> str:
    name = TEMPLATES.get(template_key)
    if not name:
        raise RuntimeError(f"unknown template: {template_key}")
    path = TEMPLATE_DIR / name
    if not path.exists():
        raise RuntimeError(f"template file not found: {path}")
    return path.read_text(encoding="utf-8")


def _render(template_text: str, context: Dict[str, str]) -> str:
    out = template_text
    for key, value in context.items():
        out = out.replace(f"{{{{{key}}}}}", str(value))
    return out


def _context_from_args(args: argparse.Namespace) -> Dict[str, str]:
    template_key = str(args.template).strip()
    project_id = _safe_project_id(args.project)
    if not project_id:
        raise RuntimeError("project_id is empty after normalization")
    niche = str(args.niche or "").strip()
    if not niche:
        raise RuntimeError("--niche is required")
    platforms = str(args.platforms or "").strip() or DEFAULT_PLATFORMS.get(template_key, "")
    goal = str(args.business_goal or "").strip() or DEFAULT_GOALS.get(template_key, "")
    custom_requirements = str(args.custom_requirements or "").strip() or "None"

    return {
        "PROJECT_ID": project_id,
        "NICHE": niche,
        "TARGET_MARKET": str(args.target_market or "us").strip() or "us",
        "PLATFORMS": platforms,
        "BUSINESS_GOAL": goal,
        "CUSTOM_REQUIREMENTS": custom_requirements,
        "GENERATED_AT_UTC": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    context = _context_from_args(args)
    template_text = _load_template(str(args.template))
    rendered = _render(template_text, context).strip() + "\n"

    save_path_raw = str(args.save_path or "").strip()
    saved_path = ""
    if save_path_raw:
        save_path = Path(save_path_raw).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(rendered, encoding="utf-8")
        saved_path = str(save_path)

    if bool(args.dry_run):
        return {
            "ok": True,
            "mode": "dry_run",
            "project_id": context["PROJECT_ID"],
            "template": str(args.template),
            "saved_path": saved_path,
            "input_markdown": rendered,
        }

    if not mongo_bridge_enabled():
        raise RuntimeError("Mongo bridge disabled. Set JARVIS_MONGO_BRIDGE_ENABLED=1 in .env")

    meta = {
        "template_key": str(args.template),
        "target_market": context["TARGET_MARKET"],
        "platforms": context["PLATFORMS"],
    }
    meta.update(_parse_meta_json(str(args.meta_json or "")))

    job_id, status = enqueue_intel_job(
        context["PROJECT_ID"],
        rendered,
        source=str(args.source or "template_cli"),
        meta=meta,
    )
    return {
        "ok": True,
        "mode": "enqueued",
        "project_id": context["PROJECT_ID"],
        "template": str(args.template),
        "job_id": job_id,
        "status": status,
        "saved_path": saved_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a multi-domain Mongo bridge job template and enqueue it."
    )
    parser.add_argument("--project", required=True, help="Project ID (example: cat_pod_us)")
    parser.add_argument(
        "--template",
        required=True,
        choices=sorted(TEMPLATES.keys()),
        help="Template key",
    )
    parser.add_argument("--niche", required=True, help="Niche description")
    parser.add_argument("--target-market", default="us", help="Target market label")
    parser.add_argument("--platforms", default="", help="Comma-separated platforms override")
    parser.add_argument("--business-goal", default="", help="Business goal override")
    parser.add_argument("--custom-requirements", default="", help="Extra job constraints")
    parser.add_argument("--source", default="template_cli", help="Source tag for auditing")
    parser.add_argument("--meta-json", default="", help="Extra meta JSON object")
    parser.add_argument("--save-path", default="", help="Optional path to save rendered markdown")
    parser.add_argument("--dry-run", action="store_true", help="Render only, do not enqueue")
    args = parser.parse_args()

    try:
        payload = run(args)
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "project_id": str(args.project or "").strip(),
                    "template": str(args.template or "").strip(),
                    "error": f"{type(e).__name__}: {e}",
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

