from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

from pymongo import DESCENDING, MongoClient

from jarvis_engine.bridge_paths import resolve_intel_inbox_base
from jarvis_engine.contracts import validate_payload
from jarvis_engine.mongo_intel_bridge import mongo_bridge_enabled, mongo_ping
from tools.manual_intel_ingest_once import run as ingest_run
from tools.mongo_enqueue_from_outbox import run as enqueue_run


STATE_PATH_DEFAULT = REPO_ROOT / "data" / "telegram_focus" / "state.json"
RAW_EXPORT_ROOT = REPO_ROOT / "data" / "telegram_focus" / "raw_packets"
REQUEST_EXPORT_ROOT = REPO_ROOT / "data" / "telegram_focus" / "requests"
PROJECTS_DIR = REPO_ROOT / "projects"
DATA_DIR = REPO_ROOT / "data"

MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "ابدأ مشروع"}, {"text": "تشغيل قاعدة البيانات"}],
        [{"text": "جهز رسالة GPT"}, {"text": "متابعة الرد"}],
        [{"text": "استيراد النتيجة"}, {"text": "حالة"}],
        [{"text": "مساعدة"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}


def _load_env() -> None:
    if load_dotenv is None:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value or "")


def _safe_project_id(value: str) -> str:
    return str(value or "").strip().lower()


def _load_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(fallback)
    if not isinstance(payload, dict):
        return dict(fallback)
    merged = dict(fallback)
    merged.update(payload)
    return merged


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_state() -> Dict[str, Any]:
    return {
        "focus_project_id": "",
        "awaiting_project_id": False,
        "last_update_id": 0,
        "last_seen_packet": {},
        "last_watch_iso": "",
    }


def _available_projects() -> List[str]:
    if not PROJECTS_DIR.exists():
        return []
    out: List[str] = []
    for path in sorted(PROJECTS_DIR.glob("*.toml")):
        if not path.is_file():
            continue
        if path.stem.startswith("_"):
            continue
        out.append(path.stem)
    return out


def _tg_base_url(token: str) -> str:
    return f"https://api.telegram.org/bot{token}"


def _tg_get_updates(token: str, offset: int, timeout: int) -> List[Dict[str, Any]]:
    url = f"{_tg_base_url(token)}/getUpdates"
    params = {"offset": offset, "timeout": max(1, int(timeout))}
    try:
        resp = requests.get(url, params=params, timeout=max(10, timeout + 10))
        payload = resp.json()
    except Exception:
        return []
    if not isinstance(payload, dict) or not payload.get("ok"):
        return []
    rows = payload.get("result")
    return rows if isinstance(rows, list) else []


def _tg_send_message(token: str, chat_id: str, text: str, *, with_menu: bool = True) -> None:
    url = f"{_tg_base_url(token)}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": str(chat_id),
        "text": str(text),
        "disable_web_page_preview": True,
    }
    if with_menu:
        payload["reply_markup"] = MENU_KEYBOARD
    try:
        requests.post(url, json=payload, timeout=30)
    except Exception:
        return


def _tg_send_document(token: str, chat_id: str, path: Path, caption: str = "") -> None:
    if not path.exists():
        return
    url = f"{_tg_base_url(token)}/sendDocument"
    data = {"chat_id": str(chat_id), "caption": str(caption)[:1024]}
    try:
        with path.open("rb") as handle:
            files = {"document": (path.name, handle, "application/octet-stream")}
            requests.post(url, data=data, files=files, timeout=120)
    except Exception:
        return


def _mongo_client() -> MongoClient:
    uri = _env("JARVIS_MONGO_URI")
    if not uri:
        raise RuntimeError("JARVIS_MONGO_URI is required.")
    timeout_ms = int(_env("JARVIS_MONGO_CONNECT_TIMEOUT_MS", "6000") or "6000")
    return MongoClient(uri, serverSelectionTimeoutMS=max(1000, timeout_ms))


def _db():
    return _mongo_client()[_env("JARVIS_MONGO_DB", "jarvis_intel")]


def _jobs_coll():
    return _db()[_env("JARVIS_MONGO_JOBS_COLLECTION", "intel_jobs")]


def _packets_coll():
    return _db()[_env("JARVIS_MONGO_INTEL_COLLECTION", "intel_packets")]


def _extract_packet(doc: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("packet", "payload", "result", "data", "raw"):
        value = doc.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _chunk_text(value: str, max_chars: int = 3500) -> List[str]:
    text = str(value or "")
    if not text:
        return []
    out: List[str] = []
    start = 0
    while start < len(text):
        out.append(text[start : start + max_chars])
        start += max_chars
    return out


def _resolve_accessible_inbox_project(project_id: str) -> Path:
    configured_base = resolve_intel_inbox_base()
    local_base = REPO_ROOT / "data" / "intel_inbox"
    candidates: List[Path] = [configured_base / project_id]
    try:
        same_base = configured_base.resolve() == local_base.resolve()
    except Exception:
        same_base = str(configured_base) == str(local_base)
    if not same_base:
        candidates.append(local_base / project_id)

    for candidate in candidates:
        try:
            candidate.exists()
            return candidate
        except PermissionError:
            continue
    return candidates[-1]


def _latest_local_packet(project_id: str) -> Dict[str, Any]:
    inbox = _resolve_accessible_inbox_project(project_id)
    if not inbox.exists():
        return {"ok": False, "error": f"inbox missing: {inbox}"}
    try:
        candidates = sorted(
            [p for p in inbox.glob("*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except PermissionError:
        return {"ok": False, "error": f"inbox access denied: {inbox}"}
    if not candidates:
        return {"ok": False, "error": "no local packet files"}
    path = candidates[0]
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return {"ok": False, "error": f"invalid local packet json: {path.name}: {type(e).__name__}: {e}"}
    payload = raw if isinstance(raw, dict) else {}
    key = f"file:{path}:{int(path.stat().st_mtime_ns)}"
    return {
        "ok": True,
        "key": key,
        "channel": "local_file",
        "packet": payload,
        "created_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
        "source_ref": str(path),
    }


def _latest_mongo_packet(project_id: str) -> Dict[str, Any]:
    doc = _packets_coll().find_one({"project_id": project_id}, sort=[("created_at", DESCENDING)])
    if not doc:
        return {"ok": False, "error": "no mongo packet"}
    packet = _extract_packet(doc)
    if not packet:
        return {"ok": False, "error": "latest mongo packet has no payload"}
    mongo_id = str(doc.get("_id"))
    return {
        "ok": True,
        "key": f"mongo:{mongo_id}",
        "channel": "mongo",
        "packet": packet,
        "created_at": doc.get("created_at"),
        "source_ref": mongo_id,
    }


def _latest_packet(project_id: str) -> Dict[str, Any]:
    project_id = _safe_project_id(project_id)
    if not project_id:
        return {"ok": False, "error": "project_id empty"}
    if mongo_bridge_enabled():
        try:
            mongo_result = _latest_mongo_packet(project_id)
            if mongo_result.get("ok"):
                return mongo_result
        except Exception as e:
            mongo_error = f"{type(e).__name__}: {e}"
            local_result = _latest_local_packet(project_id)
            if local_result.get("ok"):
                local_result["warning"] = f"mongo latest failed: {mongo_error}"
                return local_result
            return {"ok": False, "error": f"mongo latest failed: {mongo_error}"}
    return _latest_local_packet(project_id)


def _packet_hash(packet: Dict[str, Any]) -> str:
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _save_raw_packet(project_id: str, packet_key: str, packet: Dict[str, Any]) -> Tuple[Path, str]:
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    safe_key = "".join(ch for ch in packet_key if ch.isalnum() or ch in {"-", "_", ":"})
    safe_key = safe_key.replace(":", "_")
    export_dir = RAW_EXPORT_ROOT / project_id
    export_dir.mkdir(parents=True, exist_ok=True)
    out_path = export_dir / f"{stamp}_{safe_key}.json"
    out_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = _packet_hash(packet)
    (out_path.with_suffix(out_path.suffix + ".sha256")).write_text(digest + "\n", encoding="utf-8")
    return out_path, digest


def _response_time_stats(project_id: str) -> Dict[str, Any]:
    rows = _jobs_coll().find(
        {
            "project_id": project_id,
            "status": "completed",
            "created_at": {"$exists": True},
            "completed_at": {"$exists": True},
        }
    ).sort("completed_at", DESCENDING).limit(50)
    samples: List[float] = []
    for row in rows:
        created = row.get("created_at")
        completed = row.get("completed_at")
        if not isinstance(created, datetime) or not isinstance(completed, datetime):
            continue
        seconds = (completed - created).total_seconds()
        if 0 < seconds < 86400:
            samples.append(float(seconds))
    if not samples:
        return {"count": 0, "avg_seconds": 0.0, "last_seconds": 0.0}
    return {
        "count": len(samples),
        "avg_seconds": sum(samples) / len(samples),
        "last_seconds": samples[0],
    }


def _job_counts(project_id: str) -> Dict[str, int]:
    statuses = ["queued", "new", "processing", "completed", "failed"]
    out: Dict[str, int] = {}
    for status in statuses:
        out[status] = int(_jobs_coll().count_documents({"project_id": project_id, "status": status}))
    return out


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, sec = divmod(total, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs}س {mins}د {sec}ث"
    if mins > 0:
        return f"{mins}د {sec}ث"
    return f"{sec}ث"


def _simple_packet_summary(packet: Dict[str, Any]) -> str:
    ideas = packet.get("ideas") if isinstance(packet.get("ideas"), list) else []
    findings = packet.get("findings") if isinstance(packet.get("findings"), list) else []
    actionables = packet.get("actionables") if isinstance(packet.get("actionables"), list) else []
    sources = packet.get("sources") if isinstance(packet.get("sources"), list) else []
    first_idea = ideas[0] if ideas and isinstance(ideas[0], dict) else {}
    title = str(first_idea.get("title") or "").strip()
    hook = str(first_idea.get("hook") or "").strip()
    return (
        f"الفكرة الأولى: {title or 'غير موجود'}\n"
        f"الهوك: {hook or 'غير موجود'}\n"
        f"المصادر: {len(sources)} | النتائج: {len(findings)} | الأفكار: {len(ideas)} | الإجراءات: {len(actionables)}"
    )


def _missing_prompt(packet: Dict[str, Any]) -> str:
    required_arrays = ["sources", "findings", "ideas", "actionables"]
    missing_sections = [key for key in required_arrays if not isinstance(packet.get(key), list) or not packet.get(key)]
    schema_result = validate_payload(packet, "intel_packet.v1.json")
    if not missing_sections and schema_result.ok:
        return ""
    request_id = str(packet.get("request_id") or "").strip()
    missing_text = ", ".join(missing_sections) if missing_sections else "راجع أخطاء الصيغة"
    schema_error = schema_result.errors[0] if schema_result.errors else "schema_not_valid"
    return (
        "الرد الحالي ناقص/غير صالح.\n"
        "ابعت لـ GPT النص ده:\n"
        f"\"أعد نفس نتيجة الطلب {request_id or '[request_id]'} بصيغة JSON فقط وبدون أي نص إضافي. "
        f"لا تغير المعنى. أكمل الأجزاء الناقصة: {missing_text}. "
        f"أول خطأ صيغة ظهر عندي: {schema_error}.\""
    )


def _wake_bridge(wait_seconds: int, interval_seconds: int) -> Dict[str, Any]:
    server_url = _env("JARVIS_BRIDGE_SERVER_URL")
    bridge_key = _env("JARVIS_BRIDGE_API_KEY")

    if server_url and "YOUR_AZURE_FUNCTION_HOST" not in server_url:
        health_url = server_url.rstrip("/") + "/api/v1/health"
        deadline = time.time() + max(10, int(wait_seconds))
        last_error = ""
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            headers = {}
            if bridge_key:
                headers["x-jarvis-key"] = bridge_key
            try:
                resp = requests.get(health_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    try:
                        payload = resp.json()
                    except Exception:
                        payload = {}
                    return {
                        "ok": True,
                        "mode": "http_bridge",
                        "health_url": health_url,
                        "attempts": attempts,
                        "payload": payload,
                    }
                last_error = f"http_{resp.status_code}"
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
            time.sleep(max(1, int(interval_seconds)))
        return {"ok": False, "mode": "http_bridge", "health_url": health_url, "error": last_error}

    try:
        ping = mongo_ping()
        return {"ok": True, "mode": "mongo_ping", "payload": ping}
    except Exception as e:
        return {"ok": False, "mode": "mongo_ping", "error": f"{type(e).__name__}: {e}"}


def _require_focus_project(state: Dict[str, Any]) -> str:
    return _safe_project_id(str(state.get("focus_project_id") or ""))


def _handle_start_or_help(token: str, chat_id: str, state: Dict[str, Any]) -> None:
    focus = _require_focus_project(state)
    txt = (
        "وضع التركيز شغال.\n"
        f"المشروع الحالي: {focus or 'غير محدد'}\n\n"
        "الأزرار الأساسية:\n"
        "1) ابدأ مشروع\n"
        "2) تشغيل قاعدة البيانات\n"
        "3) جهز رسالة GPT\n"
        "4) متابعة الرد\n"
        "5) استيراد النتيجة"
    )
    _tg_send_message(token, chat_id, txt, with_menu=True)


def _handle_set_project_request(token: str, chat_id: str, state: Dict[str, Any]) -> None:
    projects = _available_projects()
    state["awaiting_project_id"] = True
    if not projects:
        _tg_send_message(token, chat_id, "مش لاقي ملفات مشاريع داخل مجلد projects.", with_menu=True)
        return
    head = "اكتب project_id دلوقتي.\nالمشاريع المتاحة:\n"
    body = "\n".join(f"- {p}" for p in projects[:30])
    _tg_send_message(token, chat_id, head + body, with_menu=True)


def _set_focus_project(token: str, chat_id: str, state: Dict[str, Any], project_id_raw: str) -> None:
    project_id = _safe_project_id(project_id_raw)
    projects = _available_projects()
    if project_id not in projects:
        _tg_send_message(token, chat_id, f"المشروع `{project_id}` غير موجود. اضغط ابدأ مشروع واختار من القائمة.", with_menu=True)
        return
    state["focus_project_id"] = project_id
    state["awaiting_project_id"] = False
    _tg_send_message(token, chat_id, f"تم التركيز على المشروع: {project_id}", with_menu=True)


def _handle_wake(token: str, chat_id: str, wait_seconds: int, interval_seconds: int) -> None:
    _tg_send_message(token, chat_id, "بشغّل الجسر الآن... استنى ثواني.", with_menu=True)
    result = _wake_bridge(wait_seconds=wait_seconds, interval_seconds=interval_seconds)
    if result.get("ok"):
        mode = str(result.get("mode") or "")
        _tg_send_message(token, chat_id, f"اشتغل بنجاح.\nالطريقة: {mode}\nتقدر تكمل إرسال طلب GPT.", with_menu=True)
        return
    _tg_send_message(token, chat_id, f"لسه مش جاهز.\nالسبب: {result.get('error')}", with_menu=True)


def _handle_prepare_gpt(token: str, chat_id: str, state: Dict[str, Any]) -> None:
    project_id = _require_focus_project(state)
    if not project_id:
        _tg_send_message(token, chat_id, "حدد مشروع الأول عبر زر: ابدأ مشروع.", with_menu=True)
        return
    try:
        report = enqueue_run(project_id, source="telegram_focus_bot", strict_mongo=False, force_local=False, input_path="")
    except Exception as e:
        _tg_send_message(token, chat_id, f"فشل تجهيز الطلب: {type(e).__name__}: {e}", with_menu=True)
        return

    outbox = Path(str(report.get("outbox_latest_path") or "")).expanduser()
    if not outbox.exists():
        _tg_send_message(
            token,
            chat_id,
            "اتعمل enqueue لكن ملف intel_input.md غير موجود للرفع. راجع outbox.",
            with_menu=True,
        )
        return

    REQUEST_EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    req_dir = REQUEST_EXPORT_ROOT / project_id
    req_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    copy_path = req_dir / f"{stamp}_{outbox.name}"
    copy_path.write_text(outbox.read_text(encoding="utf-8-sig"), encoding="utf-8")

    status = str(report.get("status") or "unknown")
    job_id = str(report.get("job_id") or "").strip()
    warning = str(report.get("warning") or "").strip()
    head = (
        f"تم تجهيز طلب GPT للمشروع: {project_id}\n"
        f"status: {status}\n"
        f"{('job_id: ' + job_id) if job_id else ''}"
    ).strip()
    if warning:
        head += f"\nتنبيه: {warning}"
    head += "\n\nابعت ملف الطلب كما هو إلى GPT."
    _tg_send_message(token, chat_id, head, with_menu=True)
    _tg_send_document(token, chat_id, copy_path, caption=f"{project_id} intel_input.md (exact)")


def _handle_check_response(token: str, chat_id: str, state: Dict[str, Any], *, proactive: bool = False) -> None:
    project_id = _require_focus_project(state)
    if not project_id:
        if not proactive:
            _tg_send_message(token, chat_id, "حدد مشروع الأول عبر زر: ابدأ مشروع.", with_menu=True)
        return

    latest = _latest_packet(project_id)
    if not latest.get("ok"):
        if not proactive:
            _tg_send_message(token, chat_id, f"لا يوجد رد جديد الآن.\n{latest.get('error')}", with_menu=True)
        return

    packet = latest.get("packet") if isinstance(latest.get("packet"), dict) else {}
    packet_key = str(latest.get("key") or "")
    if not packet or not packet_key:
        return

    seen_map = state.get("last_seen_packet")
    if not isinstance(seen_map, dict):
        seen_map = {}
        state["last_seen_packet"] = seen_map
    is_new = str(seen_map.get(project_id) or "") != packet_key
    if not is_new and proactive:
        return

    seen_map[project_id] = packet_key
    saved_path, digest = _save_raw_packet(project_id, packet_key, packet)
    summary = _simple_packet_summary(packet)
    created_at = _iso(latest.get("created_at"))
    warning = str(latest.get("warning") or "").strip()
    prefix = "وصل رد جديد" if is_new else "آخر رد متاح"
    msg = (
        f"{prefix}\n"
        f"project: {project_id}\n"
        f"source: {latest.get('channel')}\n"
        f"time: {created_at}\n"
        f"raw_sha256: {digest}\n"
        f"raw_path: {saved_path}\n\n"
        f"{summary}"
    )
    if warning:
        msg += f"\n\nتنبيه: {warning}"
    _tg_send_message(token, chat_id, msg, with_menu=True)
    _tg_send_document(token, chat_id, saved_path, caption=f"{project_id} raw_packet.json")

    fix_prompt = _missing_prompt(packet)
    if fix_prompt:
        _tg_send_message(token, chat_id, fix_prompt, with_menu=True)


def _handle_ingest(token: str, chat_id: str, state: Dict[str, Any]) -> None:
    project_id = _require_focus_project(state)
    if not project_id:
        _tg_send_message(token, chat_id, "حدد مشروع الأول عبر زر: ابدأ مشروع.", with_menu=True)
        return
    try:
        report = ingest_run(project_id=project_id, projects_dir=PROJECTS_DIR, data_dir=DATA_DIR)
    except Exception as e:
        _tg_send_message(token, chat_id, f"فشل الاستيراد: {type(e).__name__}: {e}", with_menu=True)
        return
    msg = (
        f"تم الاستيراد.\n"
        f"project: {project_id}\n"
        f"manual_items: {report.get('manual_items')}\n"
        f"tasks_generated: {report.get('tasks_generated')}\n"
        f"tasks_inserted: {report.get('tasks_inserted')}\n"
        f"warnings: {len(report.get('warnings') or [])}"
    )
    _tg_send_message(token, chat_id, msg, with_menu=True)
    warnings = report.get("warnings") or []
    if warnings:
        _tg_send_message(token, chat_id, "تنبيهات:\n- " + "\n- ".join(str(x) for x in warnings[:6]), with_menu=True)


def _handle_status(token: str, chat_id: str, state: Dict[str, Any]) -> None:
    project_id = _require_focus_project(state)
    if not project_id:
        _tg_send_message(token, chat_id, "المشروع الحالي غير محدد. استخدم زر: ابدأ مشروع.", with_menu=True)
        return

    try:
        counts = _job_counts(project_id)
        stats = _response_time_stats(project_id)
    except Exception as e:
        _tg_send_message(token, chat_id, f"تعذر قراءة حالة Mongo: {type(e).__name__}: {e}", with_menu=True)
        return

    avg_text = _format_duration(float(stats.get("avg_seconds") or 0.0)) if stats.get("count") else "غير متاح"
    last_text = _format_duration(float(stats.get("last_seconds") or 0.0)) if stats.get("count") else "غير متاح"
    msg = (
        f"حالة المشروع: {project_id}\n"
        f"queued/new: {counts.get('queued', 0) + counts.get('new', 0)}\n"
        f"processing: {counts.get('processing', 0)}\n"
        f"completed: {counts.get('completed', 0)}\n"
        f"failed: {counts.get('failed', 0)}\n"
        f"متوسط زمن الرد: {avg_text}\n"
        f"آخر زمن رد: {last_text}"
    )
    _tg_send_message(token, chat_id, msg, with_menu=True)


def _authorized_chat(message_chat_id: str, allowed_chat_id: str) -> bool:
    if not allowed_chat_id:
        return True
    return str(message_chat_id).strip() == str(allowed_chat_id).strip()


def _extract_message(update: Dict[str, Any]) -> Tuple[str, str]:
    message = update.get("message") if isinstance(update.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    text = str(message.get("text") or "").strip()
    return chat_id, text


def _handle_text(token: str, chat_id: str, text: str, state: Dict[str, Any], wake_wait: int, wake_interval: int) -> None:
    command = text.strip()
    if not command:
        return

    if command in {"/start", "/menu", "menu", "القائمة", "مساعدة"}:
        _handle_start_or_help(token, chat_id, state)
        return

    if command in {"ابدأ مشروع", "/project"}:
        _handle_set_project_request(token, chat_id, state)
        return

    if command.startswith("/focus "):
        _set_focus_project(token, chat_id, state, command.split(" ", 1)[1])
        return

    if command in {"تشغيل قاعدة البيانات", "/wake"}:
        _handle_wake(token, chat_id, wait_seconds=wake_wait, interval_seconds=wake_interval)
        return

    if command in {"جهز رسالة GPT", "/prepare"}:
        _handle_prepare_gpt(token, chat_id, state)
        return

    if command in {"متابعة الرد", "/check"}:
        _handle_check_response(token, chat_id, state, proactive=False)
        return

    if command in {"استيراد النتيجة", "/ingest"}:
        _handle_ingest(token, chat_id, state)
        return

    if command in {"حالة", "/status"}:
        _handle_status(token, chat_id, state)
        return

    if bool(state.get("awaiting_project_id")):
        _set_focus_project(token, chat_id, state, command)
        return

    _tg_send_message(
        token,
        chat_id,
        "الأمر غير واضح. استخدم الأزرار أو /start.",
        with_menu=True,
    )


def run_bot(
    *,
    token: str,
    chat_id: str,
    state_path: Path,
    poll_timeout: int,
    watch_interval_seconds: int,
    wake_wait_seconds: int,
    wake_interval_seconds: int,
) -> None:
    state = _load_json(state_path, _default_state())
    offset = int(state.get("last_update_id") or 0) + 1
    last_watch = time.monotonic()

    while True:
        dirty = False
        updates = _tg_get_updates(token, offset=offset, timeout=poll_timeout)
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = int(update.get("update_id") or 0)
            offset = max(offset, update_id + 1)
            state["last_update_id"] = update_id
            dirty = True

            msg_chat_id, text = _extract_message(update)
            if not msg_chat_id or not text:
                continue
            if not _authorized_chat(msg_chat_id, chat_id):
                continue
            _handle_text(
                token,
                msg_chat_id,
                text,
                state,
                wake_wait=wake_wait_seconds,
                wake_interval=wake_interval_seconds,
            )
            dirty = True

        now_mono = time.monotonic()
        if watch_interval_seconds > 0 and now_mono - last_watch >= watch_interval_seconds:
            focus_project = _require_focus_project(state)
            if focus_project and chat_id and _authorized_chat(chat_id, chat_id):
                _handle_check_response(token, chat_id, state, proactive=True)
                state["last_watch_iso"] = _utc_now_iso()
                dirty = True
            last_watch = now_mono

        if dirty:
            _write_json(state_path, state)


def main() -> int:
    _load_env()

    parser = argparse.ArgumentParser(description="Simple Telegram Project Focus bot for JARVIS bridge.")
    parser.add_argument("--token", default=_env("TELEGRAM_BOT_TOKEN"), help="Telegram bot token")
    parser.add_argument(
        "--chat-id",
        default=_env("JARVIS_TELEGRAM_FOCUS_CHAT_ID", _env("TELEGRAM_CHAT_ID")),
        help="Allowed Telegram chat ID (optional)",
    )
    parser.add_argument("--state-path", default=str(STATE_PATH_DEFAULT), help="State JSON path")
    parser.add_argument("--poll-timeout", type=int, default=45, help="Telegram getUpdates timeout seconds")
    parser.add_argument("--watch-interval-seconds", type=int, default=30, help="Proactive response check interval")
    parser.add_argument("--wake-wait-seconds", type=int, default=90, help="Wake loop max seconds")
    parser.add_argument("--wake-interval-seconds", type=int, default=5, help="Wake ping interval seconds")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        print(json.dumps({"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN / --token"}, ensure_ascii=False, indent=2))
        return 2

    chat_id = str(args.chat_id or "").strip()
    state_path = Path(args.state_path).expanduser().resolve()

    print(
        json.dumps(
            {
                "ok": True,
                "status": "running",
                "state_path": str(state_path),
                "watch_interval_seconds": int(args.watch_interval_seconds),
                "chat_guard": chat_id or "<none>",
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    try:
        run_bot(
            token=token,
            chat_id=chat_id,
            state_path=state_path,
            poll_timeout=int(args.poll_timeout),
            watch_interval_seconds=int(args.watch_interval_seconds),
            wake_wait_seconds=int(args.wake_wait_seconds),
            wake_interval_seconds=int(args.wake_interval_seconds),
        )
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
