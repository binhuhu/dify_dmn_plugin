"""Offline whole-head validation. No network/storage access; emit only hashes/counts/codes.

Accept a direct table document or {doc: document}. A head-only metadata summary
is not a document and is refused. Freshness is external: capture/readback must
be supplied immediately before install/store by the deployment owner.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugin"))
from dmn_client.route_table import RouteTableError, content_sha256, validate_doc  # noqa: E402
from dmn_client.stored_tables import evaluate_stored  # noqa: E402


def inspect(path):
    data = path.read_bytes()
    report = {
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "mode": "OFFLINE_SNAPSHOT_ONLY",
        "freshness": "NOT_PROVEN",
    }
    try:
        raw = json.loads(data)
        raw = raw.get("doc", raw) if isinstance(raw, dict) else raw
        if not isinstance(raw, dict) or raw.get("schema") not in (
            "dmn-json.supplement-requirements/v1",
            "dmn-json.supplement-requirements/v2",
        ):
            report.update(status="FAIL", error_code="FULL_SUPPLEMENT_DOCUMENT_REQUIRED")
            return report
        clean = validate_doc(raw)
        failures = {}
        for scene in clean["scenes"]:
            r = evaluate_stored(
                clean["table"],
                "supplement-requirements/v1",
                {"scene_id": scene},
                lambda _: raw,
            )
            if r["status"] != "SUCCEEDED":
                code = (r.get("error") or {}).get("code", "EVALUATE_FAILED")
                failures[code] = failures.get(code, 0) + 1
        digest = content_sha256(clean)
        report.update(
            schema=clean["schema"],
            version=raw.get("version"),
            content_sha256=digest,
            stored_digest_matches=raw.get("content_sha256") == digest,
            row_count=len(clean["scenes"]),
            associations_groups=sum(
                len(row.get("associations", [])) for row in clean["scenes"].values()
            ),
            failures=failures,
            status="PASS" if not failures else "FAIL",
        )
        if (
            raw.get("content_sha256") != digest
            or type(raw.get("version")) is not int
            or raw["version"] < 1
        ):
            report.update(status="FAIL", error_code="HEAD_IDENTITY_MISSING_OR_MISMATCH")
    except RouteTableError as e:
        report.update(status="FAIL", error_code=e.code)  # no labels, IDs or content in output
    except (ValueError, TypeError, UnicodeError):
        report.update(status="FAIL", error_code="INVALID_JSON")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("head", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    r = inspect(a.head)
    s = json.dumps(r, indent=2) + "\n"
    if a.output:
        a.output.write_text(s)
    print(s, end="")
    return 0 if r["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
