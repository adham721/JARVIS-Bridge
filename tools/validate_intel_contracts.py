from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Tuple

from jarvis_engine.contracts import validate_payload


def _validate_file(path: Path, schema_name: str) -> Tuple[bool, List[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"json parse failed: {type(e).__name__}: {e}"]
    result = validate_payload(payload, schema_name)
    return result.ok, list(result.errors or [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate JARVIS Intel contracts")
    parser.add_argument("--workpack", type=str, default="", help="Path to workpack.json")
    parser.add_argument("--publish-gate", type=str, default="", help="Path to publish_gate.json")
    parser.add_argument("--postmortem", type=str, default="", help="Path to postmortem.json")
    parser.add_argument("--evidence", type=str, default="", help="Path to single evidence record JSON object")
    args = parser.parse_args()

    checks = []
    if args.workpack:
        checks.append((Path(args.workpack), "workpack.v1.json"))
    if args.publish_gate:
        checks.append((Path(args.publish_gate), "publish_gate.v1.json"))
    if args.postmortem:
        checks.append((Path(args.postmortem), "postmortem.v1.json"))
    if args.evidence:
        checks.append((Path(args.evidence), "evidence_record.v1.json"))

    if not checks:
        raise SystemExit("No files provided. Use --workpack/--publish-gate/--postmortem/--evidence")

    all_ok = True
    for path, schema in checks:
        ok, errors = _validate_file(path, schema)
        print(f"{path} -> {schema}: {'OK' if ok else 'FAIL'}")
        if not ok:
            all_ok = False
            for err in errors[:10]:
                print(f"  - {err}")

    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
