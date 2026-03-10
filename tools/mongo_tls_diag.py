from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

try:
    import dns.resolver  # type: ignore
except Exception:  # pragma: no cover
    dns = None  # type: ignore


def _load_env() -> None:
    if load_dotenv is None:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def _cluster_host_from_uri(uri: str) -> str:
    text = str(uri or "").strip()
    if not text:
        raise RuntimeError("empty mongo uri")
    if "://" not in text:
        raise RuntimeError("invalid mongo uri")
    parsed = urlparse(text)
    host = str(parsed.hostname or "").strip()
    if not host:
        raise RuntimeError("could not parse host from mongo uri")
    return host


def _resolve_srv(cluster_host: str) -> List[Dict[str, Any]]:
    if dns is None:
        return [{"ok": False, "error": "dnspython not installed"}]
    query = f"_mongodb._tcp.{cluster_host}"
    out: List[Dict[str, Any]] = []
    try:
        answers = dns.resolver.resolve(query, "SRV")
        for ans in answers:
            out.append(
                {
                    "ok": True,
                    "target": str(ans.target).rstrip("."),
                    "port": int(ans.port),
                    "priority": int(ans.priority),
                    "weight": int(ans.weight),
                }
            )
    except Exception as e:
        out.append({"ok": False, "error": f"{type(e).__name__}: {e}", "query": query})
    return out


def _socket_diag(host: str, port: int, timeout: float) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "host": host,
        "port": int(port),
        "tcp_connect": {"ok": False},
        "tls_handshake": {"ok": False},
        "tls_handshake_insecure": {"ok": False},
    }
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
        item["tcp_connect"] = {"ok": True}
    except Exception as e:
        item["tcp_connect"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return item

    try:
        context = ssl.create_default_context()
        with context.wrap_socket(sock, server_hostname=host) as tls_sock:
            _ = tls_sock.version()
            item["tls_handshake"] = {"ok": True, "tls_version": str(tls_sock.version() or "")}
    except Exception as e:
        item["tls_handshake"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass

    # Retry with insecure TLS to distinguish certificate verification issues
    # from deeper handshake failures.
    try:
        sock2 = socket.create_connection((host, int(port)), timeout=timeout)
        insecure = ssl._create_unverified_context()  # nosec B323
        with insecure.wrap_socket(sock2, server_hostname=host) as tls_sock2:
            _ = tls_sock2.version()
            item["tls_handshake_insecure"] = {"ok": True, "tls_version": str(tls_sock2.version() or "")}
    except Exception as e:
        item["tls_handshake_insecure"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            sock2.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return item


def run(uri: str, timeout: float) -> Dict[str, Any]:
    cluster_host = _cluster_host_from_uri(uri)
    srv_records = _resolve_srv(cluster_host)
    targets = [
        (str(r.get("target") or ""), int(r.get("port") or 27017))
        for r in srv_records
        if bool(r.get("ok")) and str(r.get("target") or "").strip()
    ]
    host_checks = [_socket_diag(h, p, timeout) for h, p in targets]
    ok_tls = sum(1 for x in host_checks if (x.get("tls_handshake") or {}).get("ok"))
    return {
        "ok": ok_tls > 0,
        "cluster_host": cluster_host,
        "srv_records": srv_records,
        "host_checks": host_checks,
        "summary": {
            "targets": len(targets),
            "tls_success_hosts": int(ok_tls),
            "tls_failed_hosts": int(max(0, len(host_checks) - ok_tls)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose DNS/TCP/TLS path to Mongo Atlas hosts.")
    parser.add_argument("--uri", default="", help="Mongo URI (defaults to JARVIS_MONGO_URI from .env)")
    parser.add_argument("--timeout-seconds", type=float, default=8.0, help="Network timeout per host")
    args = parser.parse_args()

    _load_env()
    uri = str(args.uri).strip() or _env("JARVIS_MONGO_URI")
    try:
        payload = run(uri=uri, timeout=float(args.timeout_seconds))
        print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
        return 0 if payload.get("ok") else 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=True, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
